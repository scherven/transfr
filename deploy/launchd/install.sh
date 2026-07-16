#!/usr/bin/env bash
#
# Install (or reinstall) the transfr launchd agents:
#   com.transfr.api     -- uvicorn serving api.main, auto-restarted by launchd
#   com.transfr.tunnel  -- cloudflared quick tunnel exposing it to the iOS app
#
# Idempotent: safe to re-run after a code change or reboot. The API key is
# generated ONCE and cached at ~/.config/transfr/api_key, so reinstalls keep the
# same key and the shipped iOS build keeps working. Override the rate limit with
# TRANSFR_RATE_LIMIT=... in the environment before running.
#
# Requirements: the .venv is built, Postgres (transfr_eu) is running, and
# cloudflared is installed (`brew install cloudflared`).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/transfr"
KEY_FILE="$REPO/deploy/secrets/api_key"   # in-repo, gitignored (deploy/secrets/)

CLOUDFLARED="$(command -v cloudflared || true)"
if [[ -z "$CLOUDFLARED" ]]; then
    echo "!! cloudflared not found. Install it first:  brew install cloudflared" >&2
    exit 1
fi

# --- stable API key: generate once into the repo, reuse forever -------------
if [[ ! -s "$KEY_FILE" ]]; then
    mkdir -p "$(dirname "$KEY_FILE")"
    ( umask 077; openssl rand -hex 24 > "$KEY_FILE" )
    chmod 600 "$KEY_FILE"
    echo "Generated a new API key at $KEY_FILE (gitignored)"
fi
API_KEY="$(cat "$KEY_FILE")"

mkdir -p "$AGENTS" "$LOGS"

# The API plist carries no secret; only paths are substituted. TRANSFR_API_KEY_FILE
# points the app at deploy/secrets/api_key (gitignored), read at startup.
render() {  # render <template> <dest>
    sed -e "s|__REPO__|$REPO|g" \
        -e "s|__HOME__|$HOME|g" \
        -e "s|__USER__|$USER|g" \
        -e "s|__CLOUDFLARED__|$CLOUDFLARED|g" \
        "$1" > "$2"
}

GUI="gui/$(id -u)"
for label in api tunnel; do
    src="$REPO/deploy/launchd/com.transfr.$label.plist"
    dst="$AGENTS/com.transfr.$label.plist"
    render "$src" "$dst"
    # bootout is async; wait for the service to actually leave the domain before
    # bootstrapping, else bootstrap races it and fails with EIO (error 5).
    launchctl bootout "$GUI/com.transfr.$label" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        launchctl print "$GUI/com.transfr.$label" >/dev/null 2>&1 || break
        sleep 0.5
    done
    launchctl bootstrap "$GUI" "$dst"
    echo "loaded com.transfr.$label"
done

echo
echo "Done. API key (ship this in the iOS build as the X-API-Key header):"
echo "    $API_KEY"
echo
echo "Named tunnel serves the stable URL: https://api.trans-fr.com"
echo "    verify:  curl -s -o /dev/null -w '%{http_code}\\n' https://api.trans-fr.com/health"
echo "(No named tunnel configured? The plist's quick-tunnel fallback logs a random"
echo " URL to $LOGS/tunnel.err.log instead.)"
