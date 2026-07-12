"""
A normalized coach-formation model, decoupled from any single operator's feed.

Every national railway that publishes coach-sequence data does it differently
(see the data-source deep dive and core/formation_providers.py): DB gives an
absolute position in metres + percent + sector; SBB/OeBB give a sector letter
per vehicle; NS/SNCF often give only the *order* of coaches with no position at
all. This module is the common target all of those parse INTO, and the single
place that turns whatever a feed provides into the one thing the boarding layer
needs: a coach -> (start_m, end_m) span along the platform.

Resolution ladder, most precise first (see NormalizedFormation.to_train_formation):
  1. explicit metres          -> use them (DB).
  2. sector letter + a sector->offset map for this platform (from OSM
     railway:platform:section signs, or an equal division) -> the sector's span
     (SBB/OeBB).
  3. order only               -> divide the platform equally by coach count, in
     order, honouring `reversed` for direction of travel (NS/SNCF coarse).

Whatever the input, the output is a boarding.TrainFormation, so the proven
seat -> point -> route pipeline in core/boarding.py runs unchanged on top.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from boarding import TrainFormation

CoachId = Union[int, str]  # real coaches are "12", "26", or letters "A".."E" (Colmar)


# ---------------------------------------------------------------------------
# Where sectors physically sit on a platform
# ---------------------------------------------------------------------------

@dataclass
class PlatformSectorMap:
    """Sector letter -> (start_m, end_m) offset span along a platform.

    Sectors are the coarse A/B/C.. zones painted on the platform and shown on
    the departure boards. A feed that reports "coach 7 stops in sector C" is
    only actionable once you know where C is -- which is exactly what OSM's
    railway:platform:section sign nodes give (see from_section_signs), or, when
    those are absent, an assumed equal division of the platform (equal_division).
    """

    spans: Dict[str, Tuple[float, float]]

    def offset_of(self, sectors: List[str]) -> Tuple[float, float]:
        """Span covering one or more sectors (a coach can straddle two). Raises
        KeyError naming the missing sector rather than guessing."""
        chosen = []
        for s in sectors:
            key = s.upper()
            if key not in self.spans:
                raise KeyError(f"sector {s!r} not in platform sector map {sorted(self.spans)}")
            chosen.append(self.spans[key])
        return (min(s for s, _ in chosen), max(e for _, e in chosen))

    @classmethod
    def equal_division(cls, sectors: List[str], platform_length_m: float) -> "PlatformSectorMap":
        """Split a platform into equal, contiguous sectors in the given order.
        The honest fallback when no sign positions are mapped: right ordering,
        approximate boundaries."""
        n = len(sectors)
        if n == 0:
            raise ValueError("need at least one sector")
        step = platform_length_m / n
        return cls({s.upper(): (i * step, (i + 1) * step) for i, s in enumerate(sectors)})

    @classmethod
    def from_section_signs(
        cls, sign_offsets_m: Dict[str, float], platform_length_m: float
    ) -> "PlatformSectorMap":
        """Build sector spans from the mapped position of each sector *sign*
        (e.g. OSM railway:platform:section nodes projected to an along-platform
        offset). Boundaries are placed at the midpoints between consecutive
        signs; the first sector runs from 0 and the last to the platform end.
        This is the highest-fidelity sector map available without a metres feed.
        """
        if not sign_offsets_m:
            raise ValueError("need at least one sector sign")
        ordered = sorted(sign_offsets_m.items(), key=lambda kv: kv[1])
        spans: Dict[str, Tuple[float, float]] = {}
        for i, (sector, pos) in enumerate(ordered):
            start = 0.0 if i == 0 else (ordered[i - 1][1] + pos) / 2.0
            end = platform_length_m if i == len(ordered) - 1 else (pos + ordered[i + 1][1]) / 2.0
            spans[sector.upper()] = (start, end)
        return cls(spans)


# ---------------------------------------------------------------------------
# One coach's placement, and a whole train's
# ---------------------------------------------------------------------------

@dataclass
class CoachPlacement:
    """One coach as reported by some feed. Fields are progressively more
    precise; a provider fills whatever it actually knows and leaves the rest
    None. `order` is the coach's 1-based position in the physical formation
    (from the reference end), used only when no sector/metres are available."""

    coach: CoachId
    order: Optional[int] = None
    travel_class: Optional[str] = None       # "1", "2", "12", "WR" (diner), "business"...
    sectors: List[str] = field(default_factory=list)
    start_m: Optional[float] = None          # absolute along-platform metres, A-end origin
    end_m: Optional[float] = None
    group: Optional[str] = None              # which portion of a united/divided train


@dataclass
class NormalizedFormation:
    """A whole train's formation at one stop, normalized across operators."""

    train_id: str
    country: str                              # ISO-3166 alpha-2, e.g. "DE"
    source: str                               # provider id that produced this
    placements: List[CoachPlacement]
    station: Optional[str] = None
    track: Optional[str] = None
    reversed: bool = False                    # True: order==1 sits at the FAR end
    seats_per_coach: int = 60                 # placeholder; real feeds vary per coach type
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.placements:
            raise ValueError(f"formation for {self.train_id} has no coaches")

    def has_metres(self) -> bool:
        return all(p.start_m is not None and p.end_m is not None for p in self.placements)

    def has_sectors(self) -> bool:
        return all(p.sectors for p in self.placements)

    def coach_ids(self) -> List[CoachId]:
        return [p.coach for p in self.placements]

    def _resolve_span(
        self, p: CoachPlacement, platform_length_m: float,
        sector_map: Optional[PlatformSectorMap], n: int,
    ) -> Tuple[float, float]:
        # 1. explicit metres win outright.
        if p.start_m is not None and p.end_m is not None:
            return (p.start_m, p.end_m)
        # 2. sector + a map for this platform.
        if p.sectors and sector_map is not None:
            return sector_map.offset_of(p.sectors)
        # 3. order only -> equal division, honouring direction of travel.
        if p.order is None:
            raise ValueError(
                f"coach {p.coach} of {self.train_id} has no metres, no mapped sector, "
                f"and no order -- cannot place it on the platform"
            )
        step = platform_length_m / n
        pos = (n - p.order) if self.reversed else (p.order - 1)
        return (pos * step, (pos + 1) * step)

    def to_train_formation(
        self,
        platform_length_m: float,
        sector_map: Optional[PlatformSectorMap] = None,
        seats_per_coach: Optional[int] = None,
    ) -> TrainFormation:
        """Resolve every coach to a platform span and hand back a
        boarding.TrainFormation. `sector_map` is required iff any coach is
        placed by sector (see the resolution ladder in the module docstring).
        """
        n = len(self.placements)
        spans: Dict[CoachId, Tuple[float, float]] = {}
        for p in self.placements:
            s, e = self._resolve_span(p, platform_length_m, sector_map, n)
            if e <= s:
                raise ValueError(f"coach {p.coach} resolved to a non-positive span {(s, e)}")
            spans[p.coach] = (s, e)
        return TrainFormation(
            train_id=self.train_id,
            coach_span_m=spans,
            seats_per_coach=seats_per_coach or self.seats_per_coach,
        )
