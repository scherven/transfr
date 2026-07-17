#!/usr/bin/env bash
#
# Regenerate TransfrApp.xcodeproj with the dev API key baked into the run scheme.
#
# Use this INSTEAD of a bare `xcodegen generate`. project.yml injects the live
# base URL and `${TRANSFR_API_KEY}` into the scheme's run environment, and
# xcodegen expands `${TRANSFR_API_KEY}` from the *shell* at generation time. Run
# bare xcodegen without that variable exported and the scheme bakes the literal
# string `${TRANSFR_API_KEY}` -- the app then sends a garbage `X-API-Key` header
# and every protected endpoint 401s. This wrapper sources the key from the
# gitignored secret first, so a regenerate never silently breaks auth and you
# never have to remember the export.
#
# The generated .xcodeproj is gitignored, so the baked key never lands in git.
#
# You do NOT need to run this after ordinary code edits: TransfrUI and TransfrCore
# are SwiftPM packages (Xcode globs their Sources live). Only re-run it after
# editing project.yml or adding/removing a file under ios/App/.
#
# Usage:  ./generate.sh            # from ios/
#         TRANSFR_USE_SAMPLE=1 ... # any env you set is passed through to the run
set -euo pipefail
cd "$(dirname "$0")"

key_file="../deploy/secrets/api_key"
if [[ -f "$key_file" ]]; then
  # Match the server's `f.read().strip()` (api/config.py) exactly; the key is a
  # single whitespace-free token, so stripping all whitespace is byte-identical.
  TRANSFR_API_KEY="$(tr -d '[:space:]' < "$key_file")"
  export TRANSFR_API_KEY
  if [[ -z "$TRANSFR_API_KEY" ]]; then
    echo "warning: $key_file is empty -- protected endpoints will 401." >&2
  fi
else
  echo "warning: $key_file not found -- generating without an API key." >&2
  echo "         The app will 401 against the live service; set TRANSFR_USE_SAMPLE=1" >&2
  echo "         in the scheme to run the bundled offline tier instead." >&2
fi

exec xcodegen generate "$@"
