# launchd agents (beta, macOS always-on host)

Two user LaunchAgents keep the beta up without a manual restart:

| Label | What it runs | Restart trigger |
|-------|--------------|-----------------|
| `com.transfr.api` | `uvicorn api.main:app` on `127.0.0.1:5001` | crash, DB blip, reboot/login |
| `com.transfr.tunnel` | `cloudflared tunnel run transfr` → the API | crash, reboot/login |

`KeepAlive` restarts each on exit; `RunAtLoad` starts them on login/boot. The API
binds to loopback only — cloudflared (also local) is the sole thing that reaches
it, so it's never exposed on the LAN.

## Install / update

```sh
brew install cloudflared          # one-time
# one-time named-tunnel setup (stable URL https://api.trans-fr.com):
cloudflared tunnel login                              # authorize the domain
cloudflared tunnel create transfr                     # creates <uuid>.json creds
cloudflared tunnel route dns transfr api.trans-fr.com # auto-creates the DNS CNAME
# then write ~/.cloudflared/config.yml (tunnel id + ingress -> http://localhost:5001)

deploy/launchd/install.sh         # idempotent; re-run after code changes or reboot
```

The tunnel agent runs `cloudflared tunnel run transfr`, serving the stable
`https://api.trans-fr.com`. (No domain? The plist documents a quick-tunnel
fallback with a random URL.)

The script generates the shared API key **once** into `deploy/secrets/api_key`
(gitignored) and reuses it on every reinstall, so the shipped iOS build keeps
working. It prints the key and how to read the current tunnel URL.

The key never enters the plist: the plist passes only `TRANSFR_API_KEY_FILE`
(a path), and the app reads the secret from that gitignored file at startup.
uvicorn is invoked directly rather than via a shell wrapper because macOS TCC
blocks launchd from executing a script under `~/Documents`.

## Day-to-day

```sh
# status
launchctl list | grep com.transfr

# the URL is stable: https://api.trans-fr.com
curl -s -o /dev/null -w '%{http_code}\n' https://api.trans-fr.com/health   # expect 200

# logs
tail -f ~/Library/Logs/transfr/api.err.log
tail -f ~/Library/Logs/transfr/tunnel.err.log

# stop / remove
launchctl bootout gui/$(id -u)/com.transfr.api
launchctl bootout gui/$(id -u)/com.transfr.tunnel
```

## Caveats

- **Named tunnel = stable URL.** `https://api.trans-fr.com` survives restarts and
  reboots, so it's safe to bake into an app build. Its creds live in
  `~/.cloudflared/` (`config.yml` + `<uuid>.json`) — **outside the repo, secret**.
  The quick-tunnel fallback (random URL, no domain) is documented inline in the plist.
- **Postgres is a separate dependency.** Make sure it starts on boot too — e.g.
  `brew services start postgresql@<v>`. The API tolerates Postgres coming up late
  (lazy pool), but the data routes need it eventually.
- The installed plists in `~/Library/LaunchAgents/` contain **no secret** (only a
  path to the gitignored key file). Edit the **templates here** and re-run
  `install.sh`, not the installed copies.
