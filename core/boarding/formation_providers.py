"""
Per-operator coach-formation providers, and a way to rank them.

Each provider knows how to turn ONE national feed's response into a
formation_model.NormalizedFormation. The response *schemas modelled here are
representative* -- they use each operator's real field names and data model as
documented in the data-source deep dive, so a live payload drops in with
minimal change, but the sample fixtures in the tests are synthetic (no network,
no API keys, deterministic). Swapping a fixture for a real `requests.get(...)`
body is the only change needed to go live; the parse + normalize + route path
above it is identical.

Coverage of position data varies enormously by operator, which is the whole
point of ranking them:

  DE  Deutsche Bahn        metres + percent + sector   (richest; access gated)
  CH  SBB                  sector per vehicle          (open; the standout)
  AT  OeBB                 sector per vehicle          (partner access)
  NL  NS                   composition/order only      (partner; no sector)
  FR  SNCF                 composition/order only      (open; coarse)
  GB  National Rail        order + loading, sector rare (open; nascent)
  IT/ES/JP                 no usable open feed          (capability gap)

`rank_providers()` scores each on openness x positional granularity so
"which one (or combination) is most promising" is answered by the data, not a
hunch (see the module's tests and the deep-dive summary).
"""

from typing import Any, Dict, List, Optional

from formation_model import CoachPlacement, NormalizedFormation

# ---------------------------------------------------------------------------
# Capability scoring
# ---------------------------------------------------------------------------

OPENNESS_SCORE = {"open": 3, "partner": 2, "gated": 1, "none": 0}
GRANULARITY_SCORE = {"metres": 3, "sector": 2, "order": 1, "none": 0}

# Positional granularity is weighted above raw openness: an exact metre position
# you must sign a contract for is still more *useful* for this problem than an
# open feed that only tells you the coach order. But openness still counts --
# a source you can actually call today is worth more than a locked one.
_GRANULARITY_WEIGHT = 3
_OPENNESS_WEIGHT = 2


class UnsupportedFormation(NotImplementedError):
    """Raised by capability-only providers (a country with no usable open feed)
    when asked to parse -- so a gap is loud, never a silent empty formation."""


class FormationProvider:
    country: str = ""
    operator: str = ""
    openness: str = "none"        # open | partner | gated | none
    granularity: str = "none"     # metres | sector | order | none
    coverage: str = ""            # human-readable scope note

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        raise UnsupportedFormation(f"{self.operator or self.country}: no formation parser")

    @property
    def promise(self) -> int:
        # No positional granularity => useless for coach positioning, no matter
        # how open it is (e.g. Tokyo's ODPT is open but exposes no car-stop
        # position). Gate to 0 so openness alone can never make a feed look
        # promising for THIS problem.
        if GRANULARITY_SCORE[self.granularity] == 0:
            return 0
        return (
            GRANULARITY_SCORE[self.granularity] * _GRANULARITY_WEIGHT
            + OPENNESS_SCORE[self.openness] * _OPENNESS_WEIGHT
        )

    def capability(self) -> Dict[str, Any]:
        return {
            "country": self.country,
            "operator": self.operator,
            "openness": self.openness,
            "granularity": self.granularity,
            "promise": self.promise,
            "coverage": self.coverage,
        }


# ---------------------------------------------------------------------------
# DE -- Deutsche Bahn (RIS / Wagenreihung): metres + percent + sector
# ---------------------------------------------------------------------------

class DBProvider(FormationProvider):
    country, operator = "DE", "Deutsche Bahn"
    openness, granularity = "gated", "metres"
    coverage = "Long-distance + many regional; positions in metres and percent along the platform."

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        meta = payload["meta"]
        length = float(meta["platformLengthM"])
        placements: List[CoachPlacement] = []
        for w in payload["wagons"]:
            # Skip non-boardable vehicles (locomotives/power cars): no class, no sector.
            if w.get("klasse") is None and not w.get("sektor"):
                continue
            start_m = w.get("startMeter")
            end_m = w.get("endeMeter")
            if start_m is None and w.get("startProzent") is not None:  # percent -> metres
                start_m = float(w["startProzent"]) / 100.0 * length
                end_m = float(w["endeProzent"]) / 100.0 * length
            placements.append(CoachPlacement(
                coach=str(w["wagenordnungsnummer"]),
                order=int(w["wagenordnungsnummer"]) if str(w["wagenordnungsnummer"]).isdigit() else None,
                travel_class=w.get("klasse"),
                sectors=[w["sektor"]] if w.get("sektor") else [],
                start_m=float(start_m) if start_m is not None else None,
                end_m=float(end_m) if end_m is not None else None,
                group=w.get("zugteil"),
            ))
        return NormalizedFormation(
            train_id=meta["trainNumber"], country=self.country, source="db-ris",
            placements=placements, station=meta.get("stationName"), track=str(meta.get("platform")),
            seats_per_coach=int(meta.get("seatsPerCoach", 60)),
        )


# ---------------------------------------------------------------------------
# CH -- SBB (opentransportdata Train Formation Service): sector per vehicle
# ---------------------------------------------------------------------------

class SBBProvider(FormationProvider):
    country, operator = "CH", "SBB"
    openness, granularity = "open", "sector"
    coverage = "Consenting operators via SBB; per-vehicle sector + track at each stop. Open API (key)."

    _CLASS_CI = {"1": "1", "2": "2", "12": "12", "WR": "WR"}  # SBB class CI codes

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        train = payload["train"]
        placements: List[CoachPlacement] = []
        for v in payload["vehicles"]:
            stop = v["stops"][0]  # placement at the requested stop
            placements.append(CoachPlacement(
                coach=str(v.get("label", v["ordNo"])),
                order=int(v["ordNo"]),
                travel_class=self._CLASS_CI.get(str(v.get("classCI", ""))) or None,
                sectors=list(stop.get("sectors", [])),
                group=v.get("groupNo"),
            ))
        return NormalizedFormation(
            train_id=train["operationalNumber"], country=self.country, source="sbb-formation",
            placements=placements, station=train.get("stopName"),
            track=str(payload["vehicles"][0]["stops"][0].get("track")),
            seats_per_coach=int(payload.get("seatsPerCoach", 60)),
        )


# ---------------------------------------------------------------------------
# AT -- OeBB (vehicle-layout / Wagenreihung): sector per vehicle
# ---------------------------------------------------------------------------

class OeBBProvider(FormationProvider):
    country, operator = "AT", "OeBB"
    openness, granularity = "partner", "sector"
    coverage = "Long-distance; per-wagon sector, united/divided sets common. Partner API portal."

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        placements: List[CoachPlacement] = []
        for i, w in enumerate(payload["wagen"], start=1):
            placements.append(CoachPlacement(
                coach=str(w["nummer"]),
                order=int(w.get("reihung", i)),
                travel_class=w.get("klasse"),
                sectors=[w["sektor"]] if w.get("sektor") else [],
                group=w.get("zugteil"),  # e.g. "RJ 1" vs "RJ 2" when two sets are joined
            ))
        return NormalizedFormation(
            train_id=payload["zugnummer"], country=self.country, source="oebb-wagenreihung",
            placements=placements, station=payload.get("bahnhof"), track=str(payload.get("bahnsteig")),
            reversed=bool(payload.get("reversed", False)),
            seats_per_coach=int(payload.get("seatsPerCoach", 60)),
        )


# ---------------------------------------------------------------------------
# NL -- NS (virtual train / composition): order only, no sector in the open feed
# ---------------------------------------------------------------------------

class NSProvider(FormationProvider):
    country, operator = "NL", "NS"
    openness, granularity = "partner", "order"
    coverage = "National; composition + per-coach crowding, but no platform sector in the open feed."

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        placements: List[CoachPlacement] = []
        for i, deel in enumerate(payload["materieeldelen"], start=1):
            placements.append(CoachPlacement(
                coach=str(deel.get("materieelnummer", i)),
                order=int(deel.get("volgorde", i)),
                travel_class=str(deel.get("klasse")) if deel.get("klasse") is not None else None,
            ))
        return NormalizedFormation(
            train_id=str(payload["ritnummer"]), country=self.country, source="ns-virtual-train",
            placements=placements, station=payload.get("station"), track=str(payload.get("spoor")),
            seats_per_coach=int(payload.get("seatsPerCoach", 60)),
        )


# ---------------------------------------------------------------------------
# FR -- SNCF (composition des trains): order/label only, coarse
# ---------------------------------------------------------------------------

class SNCFProvider(FormationProvider):
    country, operator = "FR", "SNCF"
    openness, granularity = "open", "order"
    coverage = "TGV/TER/Intercites composition (car count, class); platform sector rarely in open data."

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        placements: List[CoachPlacement] = []
        for i, v in enumerate(payload["voitures"], start=1):
            placements.append(CoachPlacement(
                coach=str(v["numero"]),
                order=int(v.get("rang", i)),
                travel_class=str(v.get("classe")) if v.get("classe") is not None else None,
            ))
        return NormalizedFormation(
            train_id=payload["train"], country=self.country, source="sncf-composition",
            placements=placements, station=payload.get("gare"), track=str(payload.get("voie")),
            reversed=bool(payload.get("sensInverse", False)),
            seats_per_coach=int(payload.get("seatsPerCoach", 60)),
        )


# ---------------------------------------------------------------------------
# GB -- National Rail (Darwin formation): order + loading; sector rare
# ---------------------------------------------------------------------------

class NationalRailProvider(FormationProvider):
    country, operator = "GB", "National Rail (Darwin)"
    openness, granularity = "open", "order"
    coverage = "Open Darwin feed; coach order + loading. Coach positional/sector data still nascent."

    def parse(self, payload: Dict[str, Any]) -> NormalizedFormation:
        placements: List[CoachPlacement] = []
        for i, c in enumerate(payload["coaches"], start=1):
            placements.append(CoachPlacement(
                coach=str(c["coachNumber"]),
                order=int(c.get("order", i)),
                travel_class="1" if str(c.get("coachClass", "")).lower().startswith("first") else "2",
                sectors=[c["sector"]] if c.get("sector") else [],
            ))
        return NormalizedFormation(
            train_id=str(payload.get("trainId", payload.get("rid"))), country=self.country,
            source="darwin-formation", placements=placements,
            station=payload.get("location"), track=str(payload.get("platform")),
            seats_per_coach=int(payload.get("seatsPerCoach", 60)),
        )


# ---------------------------------------------------------------------------
# Capability-only entries -- honest coverage gaps (no usable open feed today)
# ---------------------------------------------------------------------------

class _CapabilityGap(FormationProvider):
    openness, granularity = "none", "none"


class TrenitaliaProvider(_CapabilityGap):
    country, operator = "IT", "Trenitalia"
    coverage = "No open coach-position feed found; in-app only."


class RenfeProvider(_CapabilityGap):
    country, operator = "ES", "Renfe"
    coverage = "No open coach-position feed found."


class ODPTProvider(_CapabilityGap):
    country, operator = "JP", "ODPT (Tokyo)"
    openness, granularity = "open", "none"
    coverage = "Open transit API, but car-level stop position not exposed."


# ---------------------------------------------------------------------------
# Registry + ranking
# ---------------------------------------------------------------------------

PROVIDERS: Dict[str, FormationProvider] = {
    p.country: p for p in [
        DBProvider(), SBBProvider(), OeBBProvider(), NSProvider(),
        SNCFProvider(), NationalRailProvider(),
        TrenitaliaProvider(), RenfeProvider(), ODPTProvider(),
    ]
}


def get_provider(country: str) -> FormationProvider:
    """Provider for an ISO-3166 alpha-2 country code (KeyError if none)."""
    return PROVIDERS[country.upper()]


def capability_matrix() -> List[Dict[str, Any]]:
    """Every provider's capability, most promising first."""
    return [p.capability() for p in sorted(PROVIDERS.values(), key=lambda p: p.promise, reverse=True)]


def rank_providers(min_promise: int = 1) -> List[str]:
    """Country codes ordered by promise, dropping capability gaps (promise 0)."""
    ranked = sorted(PROVIDERS.values(), key=lambda p: p.promise, reverse=True)
    return [p.country for p in ranked if p.promise >= min_promise]
