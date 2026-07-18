# Self-hosted MOTIS spike

A time-boxed spike to answer whether transfr can drop the public Transitous
instance for its own MOTIS 2 router — the escape hatch from Transitous's
non-commercial + fair-use terms (see issue #22).

It answers the **two open questions** the feasibility research left:

1. **Does a Europe/country import stay within a sane RAM ceiling?** Transitous's
   own notes show a *full-Europe* import failing even at 100 GB — but the
   bottleneck is MOTIS's **address/geocoding** phase, which transfr does not use.
   This spike runs MOTIS **timetable-only** (no geocoding, street routing, or
   tiles) to see whether that sidesteps the wall.
2. **API-shape parity.** Does a response from our own MOTIS survive transfr's real
   parser (`api/journeys.py`) and yield the (station, platform, timing) tuples the
   core pathfinder needs? `tests/test_motis_selfhost.py` checks exactly this.

## Why timetable-only is enough for transfr

transfr calls `/api/v5/plan` with **coordinates** (it resolves stations itself
from `stations.csv` + the OSM DB) and recomputes every transfer walk over its own
pedestrian geometry. So it needs MOTIS purely as a transit router. That lets us
omit the three expensive layers — the same three that make a continental import
heavy. `config.yml` keeps them commented out.

## Run it

Nothing here has been executed — confirm the **UNVERIFIED** notes in
`docker-compose.yml` (image tag, subcommand shape) against the MOTIS image you
pull before trusting a run.

```bash
cd deploy/motis-selfhost

# 1. Inputs -> ./data/ (gitignored). Default dataset is Switzerland (platform-rich,
#    laptop-sized). Grab the current Swiss GTFS permalink first (see fetch-data.sh).
GTFS_URL='https://…/ch_gtfs.zip' ./fetch-data.sh

# 2. Build the routing graph once (this is the RAM peak — watch `docker stats`).
docker compose --profile import run --rm import

# 3. Serve it on :8080 (serving a small timetable is <2 GB RAM).
docker compose up motis

# 4. Point transfr at it and check shape parity end-to-end.
TRANSFR_MOTIS_SMOKE=1 TRANSFR_MOTIS_BASE=http://localhost:8080 \
  ../../.venv/bin/python -m pytest ../../tests/test_motis_selfhost.py -q
```

To run the real transfr API against the self-host, start it with the same var:

```bash
TRANSFR_MOTIS_BASE=http://localhost:8080 .venv/bin/uvicorn api.main:app
```

## The knob

`TRANSFR_MOTIS_BASE` (added to `api/config.py`) is the single switch. Unset →
public Transitous, unchanged behaviour. Set → self-hosted MOTIS. It is read by
both the served path (`api/journeys.py`, `api/live.py`) and the core tooling path
(`core/boarding/live_sources.py`), so everything repoints together.

## What success looks like, and next steps

- **Green smoke test** ⇒ shape parity holds; the swap is code-complete.
- **Import RAM acceptable for one country** ⇒ scale up: swap the CH dataset for
  more countries (or reuse Transitous's CC0 per-region feed config wholesale) and
  re-measure. Europe-wide is then a data/hardware exercise, not a code one.

## Honest caveats

- **Not executed / unverified:** the MOTIS image tag and CLI subcommand shape in
  `docker-compose.yml`, and the Swiss GTFS permalink in `fetch-data.sh` (it moves
  yearly and may need a free account), are best-effort and flagged inline.
- **Live delays** need GTFS-RT feeds wired per dataset (`config.yml` shows where);
  static-only means no realtime, so `api/live.py`'s refresh path has nothing to
  refresh until you add them.
- **Access/egress is geodesic** without the OSM/street layer. Fine here because
  transfr overrides the walk — but MOTIS's own durations near the endpoints will
  be straight-line approximations.
- This spike is about **technical feasibility only**. Whether to actually leave
  Transitous is the commercial/licensing decision from issue #22, not something
  this proves.
```
