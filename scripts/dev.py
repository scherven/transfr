#!/usr/bin/env python3
"""
dev.py -- one command for the local iOS + API dev loop.

Runs two watchers so neither needs babysitting:

  * the API server, via `uvicorn --reload`, which restarts on any edit under
    api/ or core/;
  * `xcodegen generate`, re-run whenever the iOS file tree changes *shape*
    (a file added / removed / renamed, or project.yml / Package.swift edited)
    so the generated ios/TransfrApp.xcodeproj stays in sync. Plain content
    edits to existing Swift files are ignored -- Xcode/SPM already see those.

The dev API key (deploy/secrets/api_key) is loaded once into the environment:
xcodegen bakes it into the run scheme, and the server reads it for X-API-Key
auth -- both from the same file, so the app<->server handshake matches.

    .venv/bin/python scripts/dev.py                # server + xcodegen watch
    .venv/bin/python scripts/dev.py --no-server    # only regen the .xcodeproj
    .venv/bin/python scripts/dev.py --no-xcodegen  # only run the API server

Ctrl-C stops everything cleanly.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IOS = REPO / "ios"
SECRET = REPO / "deploy" / "secrets" / "api_key"

# The iOS inputs that shape the generated project. Editing the *contents* of an
# existing file here doesn't need a regen; adding/removing one does.
WATCH_PATHS = [IOS / "project.yml", IOS / "App", IOS / "TransfrApp", IOS / "TransfrCore"]

# Spec files: any change to one of these (even a content edit) means regenerate.
SPEC_NAMES = {"project.yml", "Package.swift"}

# Dirs xcodegen writes into or that hold build noise. Watching these would make
# `xcodegen generate` re-trigger itself forever, so the filter drops them.
IGNORE_DIR_NAMES = {".git", "__pycache__", ".build", ".build_tmp", ".swiftpm", "DerivedData"}
IGNORE_DIR_SUFFIXES = (".xcodeproj", ".xcworkspace")


def reexec_in_venv() -> None:
    """Re-run under the project venv if uvicorn/watchfiles aren't importable here."""
    try:
        import uvicorn  # noqa: F401
        import watchfiles  # noqa: F401
        return
    except ImportError:
        pass
    venv_py = REPO / ".venv" / "bin" / "python"
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        os.execv(str(venv_py), [str(venv_py), *sys.argv])
    sys.exit("error: uvicorn/watchfiles not importable and no .venv found -- "
             "install deps or run with the project venv python.")


def load_api_key() -> None:
    if os.environ.get("TRANSFR_API_KEY", "").strip():
        return
    if SECRET.exists():
        os.environ["TRANSFR_API_KEY"] = SECRET.read_text().strip()
        print(f"[dev] loaded TRANSFR_API_KEY from {SECRET.relative_to(REPO)}")
    else:
        print(f"[dev] warning: {SECRET.relative_to(REPO)} missing -- xcodegen will bake "
              "an empty key and the server will run open.", file=sys.stderr)


def free_port(port: int) -> None:
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                             capture_output=True, text=True).stdout
    except FileNotFoundError:
        return
    for pid in (p for p in out.split() if p):
        try:
            os.kill(int(pid), signal.SIGTERM)
            print(f"[dev] freed port {port} (killed pid {pid})")
        except (ProcessLookupError, ValueError):
            pass


def run_xcodegen(reason: str) -> None:
    print(f"[xcodegen] regenerating ({reason}) ...", flush=True)
    code = subprocess.run(["xcodegen", "generate"], cwd=IOS).returncode
    print("[xcodegen] done." if code == 0 else f"[xcodegen] FAILED (exit {code})",
          file=sys.stderr if code else sys.stdout, flush=True)


def start_server(host: str, port: int) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uvicorn", "api.main:app",
           "--host", host, "--port", str(port),
           "--reload", "--reload-dir", "api", "--reload-dir", "core"]
    print(f"[server] uvicorn on http://{host}:{port}  (reload: api/, core/)", flush=True)
    return subprocess.Popen(cmd, cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)})


def is_ignored(p: Path) -> bool:
    if p.name == ".DS_Store":
        return True
    return any(part in IGNORE_DIR_NAMES or part.endswith(IGNORE_DIR_SUFFIXES)
               for part in p.parts)


def keep(_change, path: str) -> bool:
    """watchfiles filter: True to keep an event, False to drop it."""
    return not is_ignored(Path(path))


def source_snapshot() -> frozenset[str]:
    """The set of source files under the watched roots -- xcodegen's inputs.

    macOS FSEvents can't reliably tell a content edit from a new file (both
    surface as `added`), so we don't trust the event *type*. Instead we compare
    this snapshot before vs after each batch: the project only needs regenerating
    when a path actually appears or disappears, not when a file's bytes change.
    """
    files: set[str] = set()
    for root in WATCH_PATHS:
        if root.is_file():
            if not is_ignored(root):
                files.add(str(root))
        elif root.is_dir():
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames
                               if d not in IGNORE_DIR_NAMES
                               and not d.endswith(IGNORE_DIR_SUFFIXES)]
                files.update(os.path.join(dirpath, f) for f in filenames if f != ".DS_Store")
    return frozenset(files)


def watch_xcodegen(stop: threading.Event) -> None:
    from watchfiles import watch

    paths = [str(p) for p in WATCH_PATHS if p.exists()]
    rels = ", ".join(str(Path(p).relative_to(REPO)) for p in paths)
    print(f"[xcodegen] watching {rels}", flush=True)
    snapshot = source_snapshot()
    for changes in watch(*paths, watch_filter=keep, stop_event=stop,
                         rust_timeout=1000, raise_interrupt=False):
        current = source_snapshot()
        added = {Path(p).name for p in current - snapshot}
        removed = {Path(p).name for p in snapshot - current}
        specs = sorted({Path(pth).name for _, pth in changes if Path(pth).name in SPEC_NAMES})
        if not (added or removed or specs):
            continue  # bytes changed inside existing files -- Xcode/SPM handle that
        reason = "; ".join(filter(None, [
            f"spec {', '.join(specs)}" if specs else "",
            f"added {', '.join(sorted(added))}" if added else "",
            f"removed {', '.join(sorted(removed))}" if removed else "",
        ]))
        snapshot = current
        run_xcodegen(reason)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--no-server", action="store_true", help="skip the API server")
    ap.add_argument("--no-xcodegen", action="store_true",
                   help="skip xcodegen watching (run the API server only)")
    args = ap.parse_args()

    load_api_key()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    server: subprocess.Popen | None = None
    try:
        if not args.no_server:
            free_port(args.port)
            server = start_server(args.host, args.port)

        if not args.no_xcodegen:
            run_xcodegen("startup")  # fresh project + validates spec/key up front
            watch_xcodegen(stop)     # blocks until `stop` is set
        elif server is not None:
            while not stop.is_set() and server.poll() is None:
                stop.wait(0.5)       # server-only: idle until signalled or it exits
    finally:
        stop.set()
        if server is not None and server.poll() is None:
            print("\n[dev] stopping server ...", flush=True)
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        print("[dev] bye.")
    return 0


if __name__ == "__main__":
    reexec_in_venv()
    raise SystemExit(main())
