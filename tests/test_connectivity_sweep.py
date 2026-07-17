"""
Tests for the resume contract of core/tooling/connectivity_sweep.py (issue #29).

The sweep is meant to survive an overnight kill -- an OOM, a reboot, a Ctrl-C --
without losing or double-counting completed stations. That contract lives in
three small pure functions, so it is tested here offline (no DB): `load_done`
(what --resume trusts), `heal_tail` (what makes appending after a hard kill
safe) and `report` (which must stay readable on a partial file).

The `heal_tail` tests are a regression net for a real bug: a SIGKILL leaves the
last row unterminated, and appending then glued the next row onto the fragment,
producing one line holding two records -- silently losing a station and redoing
it on every later resume. Caught by SIGKILLing an actual run.
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core", "tooling"))

from connectivity_sweep import heal_tail, load_done, report  # noqa: E402


def _row(rid, **kw):
    d = {"rid": rid, "status": "ok", "platform_count": 2, "pairs": 1,
         "connected": 1, "stitchable": 0, "island": 0, "elapsed_s": 0.1}
    d.update(kw)
    return json.dumps(d)


def _write(tmp_path, text, name="sweep.jsonl"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# ---------------------------------------------------------------------------
# load_done -- the resume authority
# ---------------------------------------------------------------------------

def test_load_done_reads_every_rid(tmp_path):
    path = _write(tmp_path, "\n".join(_row(r) for r in (1, 2, 3)) + "\n")
    assert load_done(path) == {1, 2, 3}


def test_load_done_on_missing_file_is_empty(tmp_path):
    assert load_done(str(tmp_path / "nope.jsonl")) == set()


def test_load_done_ignores_blank_lines(tmp_path):
    path = _write(tmp_path, _row(1) + "\n\n" + _row(2) + "\n")
    assert load_done(path) == {1, 2}


def test_load_done_drops_a_torn_final_row(tmp_path):
    """What a SIGKILL actually leaves. The half-written station must NOT count as
    done -- better to redo it than to lose it."""
    torn = _row(3)[:40]
    path = _write(tmp_path, _row(1) + "\n" + _row(2) + "\n" + torn)
    assert load_done(path) == {1, 2}


# ---------------------------------------------------------------------------
# heal_tail -- safe appending after a hard kill
# ---------------------------------------------------------------------------

def test_heal_tail_terminates_a_partial_row(tmp_path):
    path = _write(tmp_path, _row(1) + "\n" + _row(2)[:30])
    heal_tail(path)
    assert open(path).read().endswith("\n")


def test_heal_tail_leaves_a_clean_file_untouched(tmp_path):
    text = _row(1) + "\n"
    path = _write(tmp_path, text)
    heal_tail(path)
    assert open(path).read() == text


def test_heal_tail_tolerates_missing_and_empty_files(tmp_path):
    heal_tail(str(tmp_path / "nope.jsonl"))          # must not raise
    path = _write(tmp_path, "")
    heal_tail(path)
    assert open(path).read() == ""


def test_appending_after_heal_never_merges_two_records(tmp_path):
    """The regression: torn row + append must yield two lines, not one line with
    two records in it. Line 1 stays garbage (skipped); the new row is readable."""
    path = _write(tmp_path, _row(7)[:35])            # torn, unterminated
    heal_tail(path)
    with open(path, "a") as f:
        f.write(_row(8) + "\n")

    lines = [l for l in open(path).read().split("\n") if l.strip()]
    assert len(lines) == 2
    assert all(l.count('"rid"') == 1 for l in lines)  # no line holds two records
    assert load_done(path) == {8}                     # torn one dropped, new one kept


# ---------------------------------------------------------------------------
# report -- must stay readable on a partial/corrupt file
# ---------------------------------------------------------------------------

def _report_text(path, capsys):
    report(path)
    return capsys.readouterr().out


def test_report_aggregates_buckets(tmp_path, capsys):
    path = _write(tmp_path, "\n".join([
        _row(1, pairs=10, connected=8, stitchable=1, island=1),
        _row(2, pairs=10, connected=6, stitchable=1, island=3),
    ]) + "\n")
    out = _report_text(path, capsys)
    assert "platform pairs      : 20" in out
    assert "14" in out and "70.0%" in out          # connected 14/20
    assert "headline" in out

def test_report_counts_no_platform_and_error_rows_separately(tmp_path, capsys):
    path = _write(tmp_path, "\n".join([
        _row(1, pairs=2, connected=2),
        json.dumps({"rid": 2, "status": "no_platforms", "platform_count": 1, "pairs": 0}),
        json.dumps({"rid": 3, "status": "error", "error": "boom"}),
    ]) + "\n")
    out = _report_text(path, capsys)
    assert "stations recorded   : 3" in out
    assert "classified (ok)   : 1" in out
    assert "no platforms      : 1" in out
    assert "errors            : 1" in out


def test_report_survives_a_torn_row(tmp_path, capsys):
    """Partial progress must stay analysable -- that's the whole point of being
    able to --report mid-run."""
    path = _write(tmp_path, _row(1, pairs=4, connected=4) + "\n" + _row(2)[:20])
    out = _report_text(path, capsys)
    assert "stations recorded   : 1" in out


def test_report_on_missing_file_is_a_clean_failure(tmp_path, capsys):
    assert report(str(tmp_path / "nope.jsonl")) == 1
    assert "nothing to report" in capsys.readouterr().out
