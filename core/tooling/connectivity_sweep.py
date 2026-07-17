#!/usr/bin/env python3
"""
Sweep a whole database's platform connectivity, station by station (issue #29).

`/station-health` scores ONE station; this runs that same classification over
every rail station in the database and streams the results to disk. It is what
produces the connected / stitchable / island survey numbers quoted for
`transfr_eu` / `transfr_kr` in design/DESIGN.md §7.11 (the Map-health screen) --
until now those came from an ad-hoc script that was never committed.

    # a small bite first (always start here)
    .venv/bin/python core/tooling/connectivity_sweep.py --limit 50

    # the full Europe run, overnight
    .venv/bin/python core/tooling/connectivity_sweep.py

    # it died / you Ctrl-C'd it -- pick up exactly where it stopped
    .venv/bin/python core/tooling/connectivity_sweep.py --resume

    # what have we got so far? (works on PARTIAL results, mid-run)
    .venv/bin/python core/tooling/connectivity_sweep.py --report

UNIT OF WORK -- one station (an OSM stop_area relation with >= 2 platform
members and a railway=station/halt member node). For each, `list_platform_refs`
gives the real routable refs, and every unordered pair is bucketed by
`api.station_health._classify_pair`:

    connected   a route is found with plain routing
    stitchable  found ONLY once synthetic stitch bridges are enabled
    island      not found either way

The classifier, the platform cap and the bucket names are IMPORTED from
api.station_health rather than reimplemented, so the survey and the Map-health
screen cannot drift apart -- they are the same code by construction.

WHY THIS IS RESUMABLE AND MEMORY-BOUNDED (both are requirements, not polish):

  * The results file is append-only JSONL, one line per station, flushed as it
    goes. The whole result set is NEVER held in RAM -- only small integer
    counters are. Peak RSS is flat in the number of stations (~30-60 MB, set by
    the largest single station's search, not by how many you sweep).
  * That results file is also the RESUME AUTHORITY: `--resume` reads back the
    station ids it contains and skips them. A half-written final line (what a
    SIGKILL/OOM actually leaves behind) is detected and dropped, not trusted.
  * The state file is metadata + a fast summary, checkpointed every chunk. It is
    deliberately NOT the source of truth for what's done, so a stale or corrupt
    state file can never cause completed work to be redone or lost work to be
    skipped.
  * SIGINT (Ctrl-C) and SIGTERM finish the station in flight, flush, print the
    exact resume command, and exit 130 -- never a torn row.

A note on scale, measured rather than assumed: the relation's own
`member_role='platform'` count badly undercounts the real ref count (Berlin Hbf:
2 members, 14 actual refs), because `list_platform_refs` collects platform ways
from the station's whole footprint bbox. Do not size a run from that proxy --
use `--report`, or a `--limit` bite, which measure the real thing.
"""

import argparse
import json
import os
import signal
import sys
import time
from itertools import combinations
from typing import Dict, List, Optional, Set, Tuple

# Reorg bootstrap: this tool lives in core/tooling/ but the engine is imported by
# bare name (db/graph/search_context/...), and the classifier comes from api/.
# Put core/, its submodule dirs and the repo root on sys.path so this runs both
# directly and as `python -m core.tooling.connectivity_sweep`.
_C = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # core/
_ROOT = os.path.dirname(_C)
for _p in (_C, *(os.path.join(_C, _d) for _d in ("pathfinding", "dbgen", "viz", "boarding", "tooling")), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db import connect  # noqa: E402
from search_context import list_platform_refs  # noqa: E402

# The one definition of the classification -- see module docstring.
from api.station_health import (  # noqa: E402
    CONNECTED,
    ISLAND,
    MAX_PLATFORMS,
    STITCHABLE,
    _classify_pair,
    _sample,
)

DEFAULT_OUT = "core/viz_out/connectivity_sweep.jsonl"
DEFAULT_STATE = "core/viz_out/connectivity_sweep.state.json"
DEFAULT_CHUNK = 25

# Rough lat/lon boxes for --region. These are BOXES, not borders: they overlap at
# the edges and will pull in a neighbour's border stations. They exist to make
# the run bite-sized ("just do Switzerland tonight"), not to produce an
# authoritative per-country statistic. station_points.country is ~99.9% NULL in
# transfr_eu, so a real country filter isn't available to us.
REGIONS: Dict[str, Tuple[float, float, float, float]] = {
    # name:   (lat_min, lat_max, lon_min, lon_max)
    "de":      (47.2, 55.1, 5.8, 15.1),
    "ch":      (45.8, 47.9, 5.9, 10.5),
    "at":      (46.3, 49.1, 9.5, 17.2),
    "fr":      (41.3, 51.2, -5.2, 9.7),
    "nl":      (50.7, 53.7, 3.3, 7.3),
    "be":      (49.4, 51.6, 2.5, 6.5),
    "it":      (36.6, 47.1, 6.6, 18.6),
    "es":      (35.9, 43.9, -9.4, 3.4),
    "gb":      (49.9, 58.7, -8.2, 1.8),
    "dach":    (45.8, 55.1, 5.8, 17.2),
    "benelux": (49.4, 53.7, 2.5, 7.3),
}

# The sweep universe: stop_area relations that (a) look like rail -- a member
# node tagged railway=station/halt -- and (b) have at least two platform members
# worth trying to connect. The platform-member count is only a cheap prefilter;
# list_platform_refs is the authority on what refs actually exist (see docstring).
_UNIVERSE_SQL = """
WITH rail AS (
  SELECT DISTINCT m.relation_id AS rid
  FROM osm_relation_members m
  JOIN osm_relations r ON r.id = m.relation_id
  JOIN osm_nodes n ON n.id = m.member_ref AND m.member_type = 'N'
  WHERE r.tags->>'public_transport' IN ('stop_area', 'stop_area_group')
    AND n.tags->>'railway' IN ('station', 'halt')
), plat AS (
  SELECT relation_id AS rid, count(*) AS n_plat
  FROM osm_relation_members
  WHERE member_role = 'platform'
  GROUP BY relation_id
)
SELECT u.rid, sp.name, sp.lat, sp.lon
FROM (SELECT rid FROM rail JOIN plat USING (rid) WHERE plat.n_plat >= 2) u
JOIN station_points sp ON sp.relation_id = u.rid
{where}
ORDER BY u.rid
"""

# Set by the signal handler; the main loop finishes its current station and stops.
_STOP = False


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        global _STOP
        if _STOP:  # a second Ctrl-C means "I mean it" -- bail immediately
            raise KeyboardInterrupt
        _STOP = True
        name = signal.Signals(signum).name
        print(f"\n{name} received -- finishing the station in flight, then flushing. "
              f"(again = quit now)", flush=True)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def load_done(path: str) -> Set[int]:
    """Station ids already in the results file.

    This -- not the state file -- is what `--resume` trusts, because it is the
    same bytes we actually wrote. A truncated trailing line is what an OOM kill
    leaves, so a line that won't parse is dropped with a warning rather than
    crashing the resume or, worse, being silently miscounted as done.
    """
    done: Set[int] = set()
    if not os.path.exists(path):
        return done
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rid = json.loads(line).get("rid")
            except json.JSONDecodeError:
                print(f"  warning: {path}:{lineno} is truncated/corrupt -- ignoring it "
                      f"(that station will be redone)", flush=True)
                continue
            if isinstance(rid, int):
                done.add(rid)
    return done


def heal_tail(path: str) -> None:
    """Terminate a partial last line before we append to it.

    A SIGKILL (the OOM killer's weapon -- no handler runs) can land mid-write and
    leave a row with no trailing newline. Opening in append mode then glues the
    NEXT row onto that fragment, producing one line with two records in it: the
    fragment is unreadable *and* it swallows a good result, so that station is
    silently lost and gets redone on every subsequent resume, forever. Closing
    the line first isolates the damage to the fragment, which load_done/report
    already skip. Found by SIGKILLing a real run -- see the module docstring's
    resume contract.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) != b"\n":
            f.write(b"\n")
            print(f"  note: {path} ended mid-line (a hard kill); closed the partial row "
                  f"so the append below stays clean", flush=True)


def fetch_universe(cur, region: Optional[str]) -> List[dict]:
    """The (ordered) list of stations to sweep.

    Bounded by construction: ~16k rows for all of Europe, id + name + coords
    only -- a few MB. The thing that must never accumulate is per-pair results,
    and those are counted and dropped, never collected.
    """
    params: List[float] = []
    where = ""
    if region:
        lat_min, lat_max, lon_min, lon_max = REGIONS[region]
        where = ("WHERE sp.lat BETWEEN %s AND %s AND sp.lon BETWEEN %s AND %s")
        params = [lat_min, lat_max, lon_min, lon_max]
    cur.execute(_UNIVERSE_SQL.format(where=where), params)
    return [dict(r) for r in cur.fetchall()]


def sweep_station(conn, rid: int) -> dict:
    """Classify one station: every platform pair bucketed connected/stitchable/
    island. Returns the JSONL row. Only counters are kept -- the per-pair results
    are consumed and dropped, which is what keeps memory flat."""
    t0 = time.monotonic()
    with conn.cursor() as cur:
        refs = list_platform_refs(cur, rid)

    if len(refs) < 2:
        # Real and common (~36% of the universe): a halt with one platform, or a
        # relation whose platforms aren't mapped as ways. Recorded, not skipped,
        # so --resume doesn't retry it forever and --report can count it.
        return {"rid": rid, "status": "no_platforms", "platform_count": len(refs),
                "pairs": 0, "elapsed_s": round(time.monotonic() - t0, 3)}

    sampled_refs, sampled = _sample(refs, MAX_PLATFORMS)
    counts = {CONNECTED: 0, STITCHABLE: 0, ISLAND: 0}
    for a, b in combinations(sampled_refs, 2):
        counts[_classify_pair(conn, rid, a, b)] += 1

    return {
        "rid": rid,
        "status": "ok",
        "platform_count": len(refs),
        "sampled": sampled,
        "pairs": sum(counts.values()),
        "connected": counts[CONNECTED],
        "stitchable": counts[STITCHABLE],
        "island": counts[ISLAND],
        "elapsed_s": round(time.monotonic() - t0, 3),
    }


def _fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds or seconds == float("inf"):
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def write_state(path: str, state: dict) -> None:
    """Checkpoint metadata atomically (write + rename), so a kill mid-write can't
    leave a corrupt state file behind."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def resume_command(args) -> str:
    parts = [".venv/bin/python core/tooling/connectivity_sweep.py", "--resume"]
    if args.region:
        parts.append(f"--region {args.region}")
    if args.out != DEFAULT_OUT:
        parts.append(f"--out {args.out}")
    if args.state != DEFAULT_STATE:
        parts.append(f"--state {args.state}")
    if args.chunk_size != DEFAULT_CHUNK:
        parts.append(f"--chunk-size {args.chunk_size}")
    return " ".join(parts)


def report(path: str) -> int:
    """Aggregate the results file into the survey breakdown.

    Streams line by line and keeps only counters, so this is just as safe to run
    against a 16k-station finished sweep as against a partial one mid-flight --
    which is the point: partial progress stays analysable.
    """
    if not os.path.exists(path):
        print(f"No results file at {path} -- nothing to report yet.")
        return 1

    stations = ok = no_plat = errors = sampled = 0
    pairs = c = s = i = 0
    worst: List[Tuple[float, int]] = []
    fully_connected = 0
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # truncated tail; see load_done
        stations += 1
        st = row.get("status")
        if st == "no_platforms":
            no_plat += 1
            continue
        if st == "error":
            errors += 1
            continue
        ok += 1
        sampled += 1 if row.get("sampled") else 0
        pairs += row.get("pairs", 0)
        c += row.get("connected", 0)
        s += row.get("stitchable", 0)
        i += row.get("island", 0)
        if row.get("pairs") and row.get("connected") == row.get("pairs"):
            fully_connected += 1
        worst.append((row.get("elapsed_s", 0.0), row["rid"]))

    def pct(n: int) -> str:
        return f"{100.0 * n / pairs:5.1f}%" if pairs else "    -"

    print(f"\n=== connectivity sweep report: {path} ===")
    print(f"stations recorded   : {stations:,}")
    print(f"  classified (ok)   : {ok:,}")
    print(f"  no platforms      : {no_plat:,}   (<2 routable refs -- nothing to connect)")
    print(f"  errors            : {errors:,}")
    print(f"  platform-capped   : {sampled:,}   (>{MAX_PLATFORMS} refs, sampled down)")
    print(f"  fully connected   : {fully_connected:,}" + (f" ({100.0*fully_connected/ok:.1f}% of classified)" if ok else ""))
    print(f"\nplatform pairs      : {pairs:,}")
    print(f"  connected         : {c:8,}  {pct(c)}")
    print(f"  stitchable        : {s:8,}  {pct(s)}")
    print(f"  island            : {i:8,}  {pct(i)}")
    if pairs:
        print(f"\nheadline (DESIGN.md §7.11 form): "
              f"{round(100.0*c/pairs)} / {round(100.0*s/pairs)} / {round(100.0*i/pairs)}"
              f"  connected / stitchable / island")
    if worst:
        worst.sort(reverse=True)
        print("\nslowest stations (s, rid): " +
              ", ".join(f"{t:.1f}s #{r}" for t, r in worst[:5]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sweep platform connectivity (connected/stitchable/island) over every rail station.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Start with --limit 50. Resume with --resume. Read partial results with --report.",
    )
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"results JSONL, appended (default: {DEFAULT_OUT})")
    ap.add_argument("--state", default=DEFAULT_STATE, help=f"state/checkpoint file (default: {DEFAULT_STATE})")
    ap.add_argument("--resume", action="store_true", help="skip stations already in --out")
    ap.add_argument("--report", action="store_true", help="aggregate --out and exit (works mid-run)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N stations this run (0 = no limit)")
    ap.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK, help=f"checkpoint every N stations (default: {DEFAULT_CHUNK})")
    ap.add_argument("--region", choices=sorted(REGIONS), help="restrict to a rough lat/lon box (see REGIONS)")
    ap.add_argument("--dry-run", action="store_true", help="print how many stations would be swept, then exit")
    args = ap.parse_args()

    if args.report:
        return report(args.out)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    conn = connect()
    try:
        with conn.cursor() as cur:
            universe = fetch_universe(cur, args.region)
    except Exception as e:
        print(f"Could not read the station universe from the database: {e}", file=sys.stderr)
        print("This tool needs the transfr_eu (or transfr_kr) DB -- check PG* env vars / core/db.py.",
              file=sys.stderr)
        conn.close()
        return 2

    done = load_done(args.out) if args.resume else set()
    todo = [r for r in universe if r["rid"] not in done]
    if args.limit:
        todo = todo[:args.limit]

    scope = f"region={args.region}" if args.region else "all Europe"
    print(f"universe: {len(universe):,} stations ({scope})")
    if args.resume:
        print(f"already done: {len(done):,} -> skipping them")
    print(f"this run: {len(todo):,} stations, checkpoint every {args.chunk_size}")

    if args.dry_run:
        conn.close()
        return 0
    if not todo:
        print("Nothing to do. (--report to see the breakdown.)")
        conn.close()
        return 0

    _install_signal_handlers()

    state = {
        "out": args.out, "region": args.region, "universe": len(universe),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "done_before_run": len(done),
        "resume_command": resume_command(args),
    }
    t0 = time.monotonic()
    processed = errors = 0
    agg = {CONNECTED: 0, STITCHABLE: 0, ISLAND: 0}
    heal_tail(args.out)  # never append onto a row a hard kill left half-written
    results = open(args.out, "a")

    try:
        for row_meta in todo:
            if _STOP:
                break
            rid = row_meta["rid"]
            try:
                row = sweep_station(conn, rid)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # One broken station must not end an overnight run. Record it and
                # move on; --report counts these, and re-running without --resume
                # (or deleting the line) retries it.
                conn.rollback()
                row = {"rid": rid, "status": "error", "error": f"{type(e).__name__}: {e}"}
                errors += 1
            row["name"] = row_meta.get("name")

            results.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.flush()  # a SIGKILL now costs at most the station in flight
            processed += 1
            for k in agg:
                agg[k] += row.get(k, 0)

            if processed % args.chunk_size == 0 or processed == len(todo):
                os.fsync(results.fileno())
                el = time.monotonic() - t0
                rate = processed / max(el, 1e-6)
                eta = (len(todo) - processed) / rate if rate else 0
                state.update({
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "processed_this_run": processed, "todo_this_run": len(todo),
                    "total_done": len(done) + processed, "errors": errors,
                    "pairs": dict(agg), "rate_per_s": round(rate, 3),
                    "last_rid": rid,
                })
                write_state(args.state, state)
                print(f"  {processed:,}/{len(todo):,} stations "
                      f"({100.0*processed/len(todo):4.1f}%) "
                      f"{rate:5.2f} st/s  ETA {_fmt_eta(eta)}  "
                      f"pairs c/s/i {agg[CONNECTED]:,}/{agg[STITCHABLE]:,}/{agg[ISLAND]:,}"
                      f"{f'  errors {errors}' if errors else ''}", flush=True)
    except KeyboardInterrupt:
        print("\nHard interrupt.", flush=True)
    finally:
        results.flush()
        os.fsync(results.fileno())
        results.close()
        state.update({"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "processed_this_run": processed, "total_done": len(done) + processed,
                      "errors": errors, "pairs": dict(agg),
                      "stopped_early": bool(_STOP)})
        write_state(args.state, state)
        conn.close()

    el = time.monotonic() - t0
    print(f"\n{processed:,} stations in {_fmt_eta(el)} "
          f"({processed/max(el,1e-6):.2f} st/s); results -> {args.out}")
    if _STOP or processed < len(todo):
        print(f"Stopped early -- {len(done)+processed:,} stations are saved. Resume with:\n"
              f"    {resume_command(args)}")
        return 130
    print(f"Done. Breakdown:\n    .venv/bin/python core/tooling/connectivity_sweep.py --report"
          f"{f' --out ' + args.out if args.out != DEFAULT_OUT else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
