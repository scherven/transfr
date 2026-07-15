#!/usr/bin/env bash
#
# Install (or reinstall) the transfr systemd services on a Linux host:
#   transfr-api.service     -- uvicorn serving api.main, auto-restarted by systemd
#   transfr-tunnel.service  -- cloudflared quick tunnel exposing it to the iOS app
#
# System services under /etc/systemd/system (run at boot, no login needed), so
# this must run as root:  sudo deploy/systemd/install.sh
#
# Idempotent: safe to re-run after a code change or reboot. The API key is
# generated ONCE into deploy/secrets/api_key (gitignored) and reused, so the
# shipped iOS build keeps working. The services run as the repo's owner (or
# $SUDO_USER, or TRANSFR_SERVICE_USER=... if you set it), not root.
#
# Requirements: the .venv is built, Postgres (transfr_eu) is running, and
# cloudflared is installed (see your distro's Cloudflare package instructions).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "!! run as root:  sudo $0" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_DIR="/etc/systemd/system"
KEY_FILE="$REPO/deploy/secrets/api_key"
SERVICE_USER="${TRANSFR_SERVICE_USER:-${SUDO_USER:-root}}"

if [[ "$SERVICE_USER" == "root" ]]; then
    echo "!! refusing to run the services as root. Re-run with sudo (so \$SUDO_USER" >&2
    echo "   is set), or pass TRANSFR_SERVICE_USER=<user>." >&2
    exit 1
fi

CLOUDFLARED="$(command -v cloudflared 2>/dev/null || true)"
if [[ -z "$CLOUDFLARED" ]]; then
    for p in /usr/local/bin/cloudflared /usr/bin/cloudflared /opt/cloudflared/cloudflared; do
        [[ -x "$p" ]] && CLOUDFLARED="$p" && break
    done
fi
if [[ -z "$CLOUDFLARED" ]]; then
    echo "!! cloudflared not found. Install it first (see Cloudflare's Linux docs)." >&2
    exit 1
fi

# --- stable API key: generate once into the repo, reuse forever -------------
if [[ ! -s "$KEY_FILE" ]]; then
    mkdir -p "$(dirname "$KEY_FILE")"
    ( umask 077; openssl rand -hex 24 > "$KEY_FILE" )
    echo "Generated a new API key at $KEY_FILE (gitignored)"
fi
# The service user must be able to read the key file.
chown "$SERVICE_USER" "$KEY_FILE"
chmod 600 "$KEY_FILE"
API_KEY="$(cat "$KEY_FILE")"

# The units carry no secret; only paths are substituted. TRANSFR_API_KEY_FILE
# points the app at deploy/secrets/api_key, read at startup.
render() {  # render <template> <dest>
    sed -e "s|__REPO__|$REPO|g" \
        -e "s|__USER__|$SERVICE_USER|g" \
        -e "s|__CLOUDFLARED__|$CLOUDFLARED|g" \
        "$1" > "$2"
}

for unit in transfr-api transfr-tunnel; do
    render "$REPO/deploy/systemd/$unit.service" "$UNIT_DIR/$unit.service"
done

systemctl daemon-reload
systemctl enable --now transfr-api.service transfr-tunnel.service
systemctl restart transfr-api.service transfr-tunnel.service   # pick up code changes on re-run

echo
echo "Done, running as user: $SERVICE_USER"
echo "API key (ship this in the iOS build as the X-API-Key header):"
echo "    $API_KEY"
echo
echo "Current tunnel URL (may take a few seconds to appear):"
echo "    journalctl -u transfr-tunnel | grep -o 'https://[a-z0-9-]*\\.trycloudflare\\.com' | tail -1"
