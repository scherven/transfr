# systemd services (beta, Linux always-on host)

The Linux equivalent of `../launchd/` (macOS). Two system services keep the beta
up without a manual restart:

| Unit | What it runs | Restart trigger |
|------|--------------|-----------------|
| `transfr-api.service` | `uvicorn api.main:app` on `127.0.0.1:5001` | crash, DB blip, boot |
| `transfr-tunnel.service` | `cloudflared` quick tunnel → the API | crash, boot |

`Restart=always` restarts each on exit; `WantedBy=multi-user.target` + `enable`
starts them at boot (no login needed). The API binds to loopback only — cloudflared
(also local) is the sole thing that reaches it.

## Install / update

```sh
# one-time: build .venv, run Postgres (transfr_eu), install cloudflared, then
sudo deploy/systemd/install.sh
```

Idempotent — re-run after code changes or a reboot. It generates the shared API
key **once** into `../secrets/api_key` (gitignored, shared with the launchd
setup) and reuses it, so the shipped iOS build keeps working. It prints the key
and how to read the current tunnel URL.

The services run as **your user** (`$SUDO_USER`, or `TRANSFR_SERVICE_USER=<user>`),
not root — so they can reach Postgres as that user. The key never enters the unit
file: the unit passes only `TRANSFR_API_KEY_FILE` (a path), and the app reads the
secret from that gitignored file at startup.

## Day-to-day

```sh
systemctl status transfr-api transfr-tunnel
journalctl -u transfr-api -f
journalctl -u transfr-tunnel -f

# current quick-tunnel URL
journalctl -u transfr-tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1

# stop / disable
sudo systemctl disable --now transfr-api transfr-tunnel
```

## Caveats

- **Quick-tunnel URL changes on every restart.** Fine for testing (read it from
  the journal). For a URL stable enough to ship in TestFlight, register a **named
  tunnel** on a Cloudflare domain and change the tunnel unit's `ExecStart` to
  `cloudflared tunnel run <name>` (documented inline).
- **Postgres must start at boot too** (`sudo systemctl enable --now postgresql`).
  The API tolerates it coming up late (lazy pool); the data routes need it.
- **Prefer user services?** If you can't use root, copy the units to
  `~/.config/systemd/user/`, drop the `User=` lines, use `systemctl --user
  enable --now`, and run `loginctl enable-linger $USER` so they survive logout.
- Edit the **templates here** and re-run `install.sh`, not the installed copies
  in `/etc/systemd/system/`.
