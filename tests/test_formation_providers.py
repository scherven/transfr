r"""
Broad, multi-country tests for the coach-formation providers.

These push MANY seats through MANY operators' feeds (DE/CH/AT/NL/FR/GB) across a
dozen stations, then out through the same seat -> point -> route pipeline proven
in test_boarding.py. The payloads are synthetic but operator-shaped (real field
names per core/formation_providers.py), so this exercises the exact parsing +
normalization a live feed would hit -- without network or API keys.

Three things are being tested at once:
  1. each provider parses its own feed's shape correctly;
  2. whatever the granularity (metres / sector / order), a seat resolves to a
     sane, monotonic point on the platform -- across every station and hundreds
     of seats -- and routes;
  3. the limits: reversed trains, united/divided sets, letter coaches, over-long
     trains, missing sectors, and countries with no open feed at all.

The final test asserts the promise ranking the deep dive argued for, computed
from the providers' own capability metadata rather than asserted by hand.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from seat import (  # noqa: E402
    PlatformGeometry,
    find_path_from_seat,
    resolve_alighting_point,
)
from formation_model import PlatformSectorMap  # noqa: E402
from formation_providers import (  # noqa: E402
    PROVIDERS,
    UnsupportedFormation,
    capability_matrix,
    get_provider,
    rank_providers,
)
from graph import WALKING_SPEED_MS, haversine_meters  # noqa: E402

BASE_LAT, BASE_LON = 48.0, 7.75
_R = 6_371_000.0


def _east(dist_m):
    """(lat, lon) `dist_m` metres due east of the base point, on the same sphere
    as haversine_meters (negative = west). Matches PlatformGeometry.straight_line."""
    phi, theta, delta = math.radians(BASE_LAT), math.radians(90.0), dist_m / _R
    lat2 = math.asin(math.sin(phi) * math.cos(delta) + math.cos(phi) * math.sin(delta) * math.cos(theta))
    lon2 = math.radians(BASE_LON) + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi),
        math.cos(delta) - math.sin(phi) * math.sin(lat2))
    return (math.degrees(lat2), math.degrees(lon2))


# ---------------------------------------------------------------------------
# Operator-shaped payload builders (real field names, synthetic values)
# ---------------------------------------------------------------------------

def _db(train, station, platform, plen, coaches, coach_len=26.4, use_percent=False):
    """DB RIS/Wagenreihung shape: metres (or percent) + sector per wagon.
    `coaches` = list of (wagen_number, klasse, sektor)."""
    wagons, cur = [], 0.0
    for num, klass, sektor in coaches:
        w = {"wagenordnungsnummer": str(num), "klasse": klass, "sektor": sektor}
        if use_percent:
            w["startProzent"] = round(cur / plen * 100, 3)
            w["endeProzent"] = round((cur + coach_len) / plen * 100, 3)
        else:
            w["startMeter"] = round(cur, 1)
            w["endeMeter"] = round(cur + coach_len, 1)
        wagons.append(w)
        cur += coach_len
    return {"meta": {"trainNumber": train, "stationName": station, "platform": platform,
                     "platformLengthM": plen}, "wagons": wagons}


def _sbb(train, station, track, vehicles):
    """SBB formation shape: per-vehicle ordNo + classCI + sector at the stop.
    `vehicles` = list of (ordNo, classCI, [sectors])."""
    return {"train": {"operationalNumber": train, "stopName": station, "track": track},
            "vehicles": [{"ordNo": o, "classCI": c, "label": str(o),
                          "stops": [{"track": track, "sectors": secs}]} for o, c, secs in vehicles]}


def _oebb(train, station, platform, wagen, reversed_=False):
    """OeBB shape: per-wagon sector, optional zugteil (united/divided).
    `wagen` = list of (nummer, klasse, sektor, zugteil)."""
    return {"zugnummer": train, "bahnhof": station, "bahnsteig": platform, "reversed": reversed_,
            "wagen": [{"nummer": n, "klasse": k, "sektor": s, "zugteil": z, "reihung": i}
                      for i, (n, k, s, z) in enumerate(wagen, start=1)]}


def _ns(train, station, spoor, units, reversed_=False):
    """NS virtual-train shape: composition order, no sector. `units` = list of (nummer, klasse)."""
    return {"ritnummer": train, "station": station, "spoor": spoor,
            "materieeldelen": [{"materieelnummer": n, "volgorde": i, "klasse": k}
                               for i, (n, k) in enumerate(units, start=1)]}


def _sncf(train, gare, voie, voitures, reversed_=False):
    """SNCF composition shape: voiture numero + classe, order = position."""
    return {"train": train, "gare": gare, "voie": voie, "sensInverse": reversed_,
            "voitures": [{"numero": n, "classe": k, "rang": i} for i, (n, k) in enumerate(voitures, start=1)]}


def _darwin(train, loc, platform, coaches):
    """National Rail Darwin formation shape: coach letters + class + loading."""
    return {"trainId": train, "location": loc, "platform": platform,
            "coaches": [{"coachNumber": n, "coachClass": cls, "loading": load, "order": i}
                        for i, (n, cls, load) in enumerate(coaches, start=1)]}


# ---------------------------------------------------------------------------
# A dozen stations across six countries. `sectors` (when set) builds the
# equal-division sector map a sector-granularity feed needs.
# ---------------------------------------------------------------------------

def _classes_2nd(n, start=1):
    return [(start + i, "2", None) for i in range(n)]


STATIONS = [
    # DE -- Deutsche Bahn, metres. Two cities, one using percent instead of metres.
    dict(country="DE", city="Frankfurt", station="Frankfurt(Main)Hbf", plen=410.0, sectors=None,
         payload=_db("ICE 599", "Frankfurt(Main)Hbf", "7", 410.0,
                     [(11, "1", "A"), (12, "1", "A"), (13, "WR", "B"), (14, "2", "B"),
                      (15, "2", "C"), (16, "2", "C"), (17, "2", "D"), (18, "2", "D")])),
    dict(country="DE", city="Berlin", station="Berlin Hbf", plen=430.0, sectors=None,
         payload=_db("ICE 1001", "Berlin Hbf", "13", 430.0,
                     [(1, "1", "A"), (2, "1", "A"), (3, "2", "B"), (4, "2", "B"),
                      (5, "2", "C"), (6, "2", "C")], use_percent=True)),
    # CH -- SBB, sector. Sectors A-D (SBB convention), two vehicles per sector.
    dict(country="CH", city="Zurich", station="Zürich HB", plen=420.0, sectors=["A", "B", "C", "D"],
         payload=_sbb("IC 723", "Zürich HB", "31",
                      [(1, "1", ["A"]), (2, "1", ["A"]), (3, "12", ["B"]), (4, "2", ["B"]),
                       (5, "2", ["C"]), (6, "2", ["C"]), (7, "2", ["D"]), (8, "2", ["D"])])),
    dict(country="CH", city="Basel", station="Basel SBB", plen=300.0, sectors=["A", "B", "C", "D"],
         payload=_sbb("IR 2519", "Basel SBB", "8",
                      [(1, "1", ["A"]), (2, "2", ["B"]), (3, "2", ["C"]), (4, "2", ["D"])])),
    # AT -- OeBB, sector, one wagon per sector A-G.
    dict(country="AT", city="Vienna", station="Wien Hbf", plen=400.0,
         sectors=["A", "B", "C", "D", "E", "F", "G"],
         payload=_oebb("RJX 65", "Wien Hbf", "5",
                       [("21", "1", "A", "RJX"), ("22", "1", "B", "RJX"), ("23", "WR", "C", "RJX"),
                        ("24", "2", "D", "RJX"), ("25", "2", "E", "RJX"), ("26", "2", "F", "RJX"),
                        ("27", "2", "G", "RJX")])),
    # NL -- NS, order only.
    dict(country="NL", city="Utrecht", station="Utrecht Centraal", plen=340.0, sectors=None,
         payload=_ns("3051", "Utrecht Centraal", "5",
                     [("2401", "1"), ("2402", "2"), ("2403", "2"), ("2404", "2"), ("2405", "2")])),
    dict(country="NL", city="Amsterdam", station="Amsterdam Centraal", plen=280.0, sectors=None,
         payload=_ns("866", "Amsterdam Centraal", "11a",
                     [("4011", "2"), ("4012", "2"), ("4013", "2")])),
    # FR -- SNCF, order only. TGV Duplex, voitures 1-8.
    dict(country="FR", city="Paris", station="Paris Gare de Lyon", plen=400.0, sectors=None,
         payload=_sncf("TGV 6207", "Paris Gare de Lyon", "H",
                       [("1", "1"), ("2", "1"), ("3", "1"), ("4", "1"),
                        ("5", "2"), ("6", "2"), ("7", "2"), ("8", "2")])),
    # GB -- National Rail, order only, coach letters + loading.
    dict(country="GB", city="London", station="London Kings Cross", plen=260.0, sectors=None,
         payload=_darwin("1A23", "KGX", "9",
                         [("A", "First", 30), ("B", "First", 45), ("C", "Standard", 60),
                          ("D", "Standard", 80), ("E", "Standard", 75), ("F", "Standard", 55),
                          ("G", "Standard", 40), ("H", "Standard", 35), ("I", "Standard", 20)])),
    dict(country="GB", city="Edinburgh", station="Edinburgh Waverley", plen=200.0, sectors=None,
         payload=_darwin("1S10", "EDB", "2",
                         [("A", "First", 20), ("B", "Standard", 50), ("C", "Standard", 65),
                          ("D", "Standard", 40)])),
]

STATION_IDS = [f"{s['country']}-{s['city']}" for s in STATIONS]


def _normalized(station):
    return get_provider(station["country"]).parse(station["payload"])


def _sector_map(station):
    if station["sectors"] is None:
        return None
    return PlatformSectorMap.equal_division(station["sectors"], station["plen"])


def _train_formation(station):
    return _normalized(station).to_train_formation(station["plen"], sector_map=_sector_map(station))


# ---------------------------------------------------------------------------
# 1. Per-provider parse correctness
# ---------------------------------------------------------------------------

def test_db_parses_metres_and_skips_non_boardable():
    payload = _db("ICE 5", "Köln Hbf", "4", 400.0,
                  [(1, "1", "A"), (2, "2", "B")])
    payload["wagons"].insert(0, {"wagenordnungsnummer": "0", "klasse": None, "sektor": None})  # a loco
    nf = get_provider("DE").parse(payload)
    assert [p.coach for p in nf.placements] == ["1", "2"]           # loco dropped
    assert nf.placements[0].start_m == 0.0 and nf.placements[0].end_m == pytest.approx(26.4)
    assert nf.has_metres() and nf.placements[0].travel_class == "1"


def test_db_percent_is_converted_to_metres():
    nf = get_provider("DE").parse(STATIONS[1]["payload"])          # Berlin, use_percent=True
    assert nf.has_metres()
    assert nf.placements[0].start_m == pytest.approx(0.0, abs=1e-6)
    assert nf.placements[1].start_m == pytest.approx(26.4, abs=0.05)


def test_sbb_parses_sector_and_class():
    nf = get_provider("CH").parse(STATIONS[2]["payload"])
    assert nf.source == "sbb-formation" and nf.track == "31"
    assert nf.has_sectors() and not nf.has_metres()
    assert nf.placements[2].sectors == ["B"] and nf.placements[2].travel_class == "12"


def test_ns_and_sncf_and_darwin_are_order_only():
    for code, idx in [("NL", 5), ("FR", 7), ("GB", 8)]:
        nf = get_provider(code).parse(STATIONS[idx]["payload"])
        assert not nf.has_metres() and not nf.has_sectors()
        assert all(p.order is not None for p in nf.placements)


# ---------------------------------------------------------------------------
# 2. The broad sweep: every station, hundreds of seats, sane + monotonic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("station", STATIONS, ids=STATION_IDS)
def test_every_seat_resolves_inside_its_platform(station):
    tf = _train_formation(station)
    plen = station["plen"]
    for coach, (lo, hi) in tf.coach_span_m.items():
        for seat in range(1, tf.seats_per_coach + 1):
            off = tf.seat_offset_m(coach, seat)
            assert lo <= off <= hi                     # inside its own coach
            assert 0.0 <= off <= plen                  # and on the platform


@pytest.mark.parametrize("station", STATIONS, ids=STATION_IDS)
def test_offsets_monotonic_in_physical_coach_order(station):
    """Whatever the granularity, walking further down the formation must mean a
    further-along point -- the property the whole feature relies on."""
    nf = _normalized(station)
    tf = nf.to_train_formation(station["plen"], sector_map=_sector_map(station))
    ordered = sorted(nf.placements, key=lambda p: p.order)
    starts = [tf.coach_span_m[p.coach][0] for p in ordered]
    assert starts == sorted(starts)                    # non-decreasing with order


@pytest.mark.parametrize("station", STATIONS, ids=STATION_IDS)
def test_seat_point_matches_geometry_interpolation(station):
    """seat -> offset -> point round-trips against the platform geometry for a
    spread of coaches and seats at every station."""
    geom = PlatformGeometry.straight_line(
        BASE_LAT, BASE_LON, [round(20.0 * i, 3) for i in range(int(station["plen"] // 20) + 1)])
    tf = _train_formation(station)
    coaches = list(tf.coach_span_m)
    for coach in (coaches[0], coaches[len(coaches) // 2], coaches[-1]):
        for seat in (1, tf.seats_per_coach // 2, tf.seats_per_coach):
            ap = resolve_alighting_point(tf, geom, coach, seat)
            assert ap.point == pytest.approx(geom.point_at_offset(ap.offset_m), abs=1e-9)
            assert ap.clamped is False


# ---------------------------------------------------------------------------
# 3. End-to-end routing across countries: further coach -> longer transfer
# ---------------------------------------------------------------------------

def _one_exit_station_graph(geom):
    """A minimal station whose only link to the exit is at the A-end (node 0):
    so transfer distance == (metres alighted from the A-end) + 100 m."""
    coords = dict(geom.coords)
    concourse, target = -1, -2
    coords[concourse] = _east(-40.0)   # 40 m west of the A-end (node ids[0], offset 0)
    coords[target] = _east(-100.0)     # a further 60 m to the departure point
    graph = {}
    ids = geom.nodes
    for a, b in zip(ids, ids[1:]):
        w = haversine_meters(*coords[a], *coords[b]) / WALKING_SPEED_MS
        graph.setdefault(a, []).append((b, w, None))
        graph.setdefault(b, []).append((a, w, None))
    for a, b in ((ids[0], concourse), (concourse, target)):
        w = haversine_meters(*coords[a], *coords[b]) / WALKING_SPEED_MS
        graph.setdefault(a, []).append((b, w, None))
        graph.setdefault(b, []).append((a, w, None))
    return graph, coords, {target}


@pytest.mark.parametrize("station", [STATIONS[0], STATIONS[2], STATIONS[8]],
                         ids=["DE-Frankfurt", "CH-Zurich", "GB-London"])
def test_routing_is_monotonic_in_coach_order(station):
    geom = PlatformGeometry.straight_line(
        BASE_LAT, BASE_LON, [round(10.0 * i, 3) for i in range(int(station["plen"] // 10) + 1)])
    graph, coords, targets = _one_exit_station_graph(geom)
    nf = _normalized(station)
    tf = nf.to_train_formation(station["plen"], sector_map=_sector_map(station))

    dists = []
    for p in sorted(nf.placements, key=lambda p: p.order):
        res = find_path_from_seat(graph, coords, tf, geom, p.coach, seat=1, targets=targets)
        assert res["found"] is True
        dists.append(res["walking_distance_meters"])
    assert dists == sorted(dists)                       # each further coach is >= the last


def test_headline_example_coach_and_seat_on_a_real_shape():
    """The user's own example, on the DE Frankfurt ICE: pick a mid-train coach
    and a seat, get a concrete point and a concrete transfer distance."""
    station = STATIONS[0]
    geom = PlatformGeometry.straight_line(
        BASE_LAT, BASE_LON, [round(10.0 * i, 3) for i in range(42)])   # 410 m
    graph, coords, targets = _one_exit_station_graph(geom)
    tf = _train_formation(station)
    res = find_path_from_seat(graph, coords, tf, geom, coach="15", seat=46, targets=targets)
    assert res["found"] is True
    # coach 15 is the 5th of 8, laid out 4*26.4 m along -> well down the platform.
    assert res["alighting_offset_m"] > 100.0
    assert res["walking_distance_meters"] == pytest.approx(res["alighting_offset_m"] + 100.0, abs=0.5)


# ---------------------------------------------------------------------------
# 4. Testing the limits
# ---------------------------------------------------------------------------

def test_reversed_train_flips_offsets():
    """order==1 at the far end (direction of travel reversed) must invert the
    resolved offsets vs. the same train not reversed."""
    fwd = get_provider("FR").parse(_sncf("TGV 1", "Lyon", "A", [("1", "1"), ("2", "2"), ("3", "2")]))
    rev = get_provider("FR").parse(_sncf("TGV 1", "Lyon", "A", [("1", "1"), ("2", "2"), ("3", "2")],
                                         reversed_=True))
    tf_fwd = fwd.to_train_formation(300.0)
    tf_rev = rev.to_train_formation(300.0)
    assert tf_fwd.coach_span_m["1"][0] == pytest.approx(0.0)      # coach 1 at A-end
    assert tf_rev.coach_span_m["1"][0] == pytest.approx(200.0)    # coach 1 at far end


def test_united_and_divided_sets_keep_each_portion_coherent():
    """Two joined RJ sets (e.g. splitting to different destinations): coaches in
    portion 1 sit in the low sectors, portion 2 in the high sectors, and each
    portion's own coaches stay ordered."""
    payload = _oebb("RJ 100/900", "Wien Hbf", "3",
                    [("1", "2", "A", "to Graz"), ("2", "2", "B", "to Graz"),
                     ("3", "2", "C", "to Graz"), ("4", "2", "E", "to Villach"),
                     ("5", "2", "F", "to Villach"), ("6", "2", "G", "to Villach")])
    nf = get_provider("AT").parse(payload)
    smap = PlatformSectorMap.equal_division(list("ABCDEFG"), 420.0)
    tf = nf.to_train_formation(420.0, sector_map=smap)
    graz = [tf.coach_span_m[c][0] for c in ("1", "2", "3")]
    villach = [tf.coach_span_m[c][0] for c in ("4", "5", "6")]
    assert graz == sorted(graz) and villach == sorted(villach)
    assert max(graz) < min(villach)                              # the two portions don't interleave
    assert {p.group for p in nf.placements} == {"to Graz", "to Villach"}


def test_letter_coaches_resolve_like_colmar():
    """Coach ids need not be numeric (Colmar platforms use letters A-E)."""
    payload = _db("TER 96", "Colmar", "E", 250.0,
                  [("A", "2", "A"), ("B", "2", "B"), ("C", "2", "C")])
    # DB payload keys wagons by number; here force letter ids by reusing the sektor as the id.
    for w, letter in zip(payload["wagons"], "ABC"):
        w["wagenordnungsnummer"] = letter
    nf = get_provider("DE").parse(payload)
    tf = nf.to_train_formation(250.0)
    assert set(tf.coach_span_m) == {"A", "B", "C"}
    # Coach B spans (26.4, 52.8); seat 30 of 60 sits at 26.4 + (29.5/60)*26.4.
    assert 26.4 <= tf.seat_offset_m("B", 30) <= 52.8
    assert tf.seat_offset_m("B", 30) == pytest.approx(26.4 + (29.5 / 60) * 26.4, abs=0.1)


def test_overlong_train_clamps_far_coach_onto_the_platform_end():
    """A train longer than the mapped platform: the last coach's seats snap to
    the platform end and are flagged, not lost off the end of the world."""
    # DB gives ABSOLUTE metres, so a 12*26.4 ~ 317 m train genuinely overhangs a
    # 250 m platform (order-only feeds instead scale to fit and never overhang).
    payload = _db("IC 2000", "Marseille", "A", 250.0, [(i, "2", "A") for i in range(1, 13)])
    nf = get_provider("DE").parse(payload)
    geom = PlatformGeometry.straight_line(BASE_LAT, BASE_LON, [round(10.0 * i, 3) for i in range(26)])  # 250 m
    tf = nf.to_train_formation(250.0)
    near = resolve_alighting_point(tf, geom, "1", 1)
    far = resolve_alighting_point(tf, geom, "12", 60)
    assert near.clamped is False and far.clamped is True
    assert far.point == pytest.approx(geom.coords[geom.nodes[-1]], abs=1e-9)


def test_missing_sector_map_falls_back_to_equal_division():
    """A sector feed with no platform sector map still yields a sane placement
    via the order-only equal division -- lower fidelity, not a crash."""
    nf = get_provider("CH").parse(STATIONS[2]["payload"])          # Zürich, 8 vehicles
    tf = nf.to_train_formation(400.0, sector_map=None)             # no map -> equal division by order
    starts = [tf.coach_span_m[str(o)][0] for o in range(1, 9)]
    assert starts == sorted(starts)
    assert tf.coach_span_m["8"][1] == pytest.approx(400.0)         # last coach ends at platform end


def test_requesting_a_coach_not_in_the_formation_is_an_error():
    tf = _train_formation(STATIONS[0])
    with pytest.raises(KeyError):
        tf.seat_offset_m("99", 1)


def test_capability_gap_countries_raise_loudly():
    for code in ("IT", "ES", "JP"):
        with pytest.raises(UnsupportedFormation):
            get_provider(code).parse({})


# ---------------------------------------------------------------------------
# 5. Which provider is most promising -- computed, not hand-asserted
# ---------------------------------------------------------------------------

def test_promise_ranking_puts_dach_on_top():
    ranked = rank_providers()
    assert ranked[:3] == ["CH", "DE", "AT"]            # open/rich DACH core leads
    assert set(ranked) == {"CH", "DE", "AT", "NL", "FR", "GB"}
    assert "IT" not in ranked and "ES" not in ranked   # capability gaps excluded


def test_capability_matrix_is_sorted_and_complete():
    matrix = capability_matrix()
    promises = [row["promise"] for row in matrix]
    assert promises == sorted(promises, reverse=True)  # most promising first
    assert len(matrix) == len(PROVIDERS)
    ch = next(r for r in matrix if r["country"] == "CH")
    de = next(r for r in matrix if r["country"] == "DE")
    assert ch["granularity"] == "sector" and ch["openness"] == "open"
    assert de["granularity"] == "metres" and de["openness"] == "gated"
