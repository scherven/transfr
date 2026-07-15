# launchd agents (beta, macOS always-on host)

Two user LaunchAgents keep the beta up without a manual restart:

| Label | What it runs | Restart trigger |
|-------|--------------|-----------------|
| `com.transfr.api` | `uvicorn api.main:app` on `127.0.0.1:5001` | crash, DB blip, reboot/login |
| `com.transfr.tunnel` | `cloudflared` quick tunnel → the API | crash, reboot/login |

`KeepAlive` restarts each on exit; `RunAtLoad` starts them on login/boot. The API
binds to loopback only — cloudflared (also local) is the sole thing that reaches
it, so it's never exposed on the LAN.

## Install / update

```sh
brew install cloudflared          # one-time
deploy/launchd/install.sh         # idempotent; re-run after code changes or reboot
```

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

# current quick-tunnel URL
grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' ~/Library/Logs/transfr/tunnel.err.log | tail -1

# logs
tail -f ~/Library/Logs/transfr/api.err.log
tail -f ~/Library/Logs/transfr/tunnel.err.log

# stop / remove
launchctl bootout gui/$(id -u)/com.transfr.api
launchctl bootout gui/$(id -u)/com.transfr.tunnel
```

## Caveats

- **Quick-tunnel URL changes on every restart.** Fine for testing; read it from
  the log. For a URL stable enough to ship in a TestFlight build, register a
  **named tunnel** on a Cloudflare domain and change the tunnel plist's
  `ProgramArguments` to `cloudflared tunnel run <name>` (see comments in the plist).
- **Postgres is a separate dependency.** Make sure it starts on boot too — e.g.
  `brew services start postgresql@<v>`. The API tolerates Postgres coming up late
  (lazy pool), but the data routes need it eventually.
- The installed plists in `~/Library/LaunchAgents/` contain **no secret** (only a
  path to the gitignored key file). Edit the **templates here** and re-run
  `install.sh`, not the installed copies.
