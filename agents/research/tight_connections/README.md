# Tight-connection recovery — research harness

Does the route planner (Transitous / MOTIS) drop train-to-train connections that
are physically makeable, because it assumes a conservative transfer time — and
can `core/`'s real platform-to-platform walk recover them?

Short answer, from live data: **the conservatism is real, but on an on-time
schedule it almost never costs a traveller a faster trip. The payoff is under
delay.** This directory is the measurement that established that.

## What we found

**1. How MOTIS models transfers (proven live).** Access/egress legs are real OSM
street-routed walks (~0.9–1.1 m/s). But the train-to-train transfer *time* is the
operator's **minimum interchange time** — whole-minute, station/pair-specific
(120 / 180 / 240 / 300 s observed), floored over any footpath. It scales exactly
as `defaultDuration·transferTimeFactor + additionalTransferTime` (verified:
240 → 480 → 720). `transferTimeFactor` cannot go below 1.0, so you cannot ask
MOTIS to be less conservative. `core/` computes the *continuous physical* walk
(71 s where MOTIS assumes 240 s). It pads the real walk by a median **+47 s** (high
-frequency) to **+113 s** (intercity), ~1.9×.

**2. On-time impact ≈ zero.** Across 38 intercity + 30 high-frequency O-D pairs,
**0 journeys** had a makeable earlier connection MOTIS dropped. Intercity: the next
train is a headway away. High-frequency: earlier trains exist (7/17 journeys) but
are genuinely too tight even for `core/`'s optimistic walk (60 s gap vs 98–191 s
walk), or platform-data-limited.

**3. The delay case is the win.** Injecting an inbound delay: `core/` rescues a
connection MOTIS drops when `gap − MOTIS_min < delay ≤ gap − real_walk`. The
rescue-window width equals the pad (median 66 s, up to 180 s at Bern). When a 1–4
min inbound delay breaks a connection, the real walk still makes it **~12–50 %**
of the time, concentrated at padded hubs (Bern, Südkreuz, Olten, Nürnberg,
Mannheim). This is the measured justification for live re-assessment — now
implemented in [`api/transfers.py`](../../api/transfers.py) `reassess()` and the
[`api/live.py`](../../api/live.py) poller.

**4. Platform-data gap + fix.** MOTIS omits S-Bahn platforms at München / Hamburg /
Frankfurt (DELFI feed gap; its `/stoptimes` board omits them too), which capped
`core/` validation at ~⅓–½ of interchanges. **DB IRIS** (`dbf.finalrewind.org`,
free, no key) has all of them (München 182/182, Hamburg 120/120, Frankfurt
107/107). `iris.py` resolves hub → EVA (via `stations.csv` `db_id`) → platform by
line + terminus. Enriching lifted high-frequency `core/` coverage 51 % → 60 %, and
every IRIS-touched interchange in that set then routed. Intercity fills hit
`core/`'s *own* OSM platform gap (`platform_not_found`) — the next bottleneck,
which is the `core/PLATFORM-RESOLUTION.md` roadmap, not IRIS.

Two `core/` bugs found in passing (both filed as tasks): `max_search_seconds`
flips a correct verdict to `exceeded_plausibility_bound` (it's a time budget, not
a geometry check); and a lettered platform (Koblenz 9→C) snapped to the wrong OSM
feature yielding a bogus 2032 m walk the plausibility guard didn't reject.

## Files

| File | What it does |
|---|---|
| `iris.py` | DB IRIS platform enrichment (hub → EVA → platform). Reusable; `api/` could adopt it. |
| `tight.py` | Experiment library: MOTIS `/plan`, hub-reconstruction, `core/` walk, IRIS fill. |
| `run.py` | Runner over O-D pairs; checkpoints to `tight_state*.json`. |
| `delay.py` | Delay-injection analysis (pure math on a state file). |
| `analyze2.py` | Mechanism + opportunity breakdown for one state file. |
| `analyze_enrich.py` | Within-run IRIS contribution (coverage lift). |

## Running

Needs the repo's `.venv`, the `transfr_eu` DB up (`core/`), and network to
`api.transitous.org` + `dbf.finalrewind.org`. From the repo root:

```bash
# intercity dataset, IRIS-enriched, at 08:00; writes tight_state_v2.json
DATASET=intercity STATE_SUFFIX=_v2 .venv/bin/python agents/research/tight_connections/run.py
# high-frequency dataset; writes tight_state_hf_v2.json
DATASET=hf        STATE_SUFFIX=_v2 .venv/bin/python agents/research/tight_connections/run.py

# analyses (read the state files above)
STATE_SUFFIX=_v2 .venv/bin/python agents/research/tight_connections/delay.py
.venv/bin/python agents/research/tight_connections/analyze_enrich.py tight_state_v2.json tight_state_hf_v2.json
```

State files (`tight_state*.json`) and logs are run outputs and git-ignored.
Everything is schedule-based ("trains on time"); the delay analysis parameterises
the inbound delay. See the memory note `tight-connection-recovery-finding` for the
condensed findings.
