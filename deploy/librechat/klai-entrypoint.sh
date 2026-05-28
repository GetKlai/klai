#!/bin/sh
# Klai LibreChat entrypoint wrapper — force light theme for every tenant.
#
# LibreChat has NO server-side config to force a default/locked theme
# (enhancement danny-avila/LibreChat#5389 is still open as of v0.8.5). The
# theme lives client-side in localStorage['color-theme'] and the SPA's
# getInitialTheme() falls back to the OS `prefers-color-scheme` when no value
# is stored — so OS-dark users (and anyone who once toggled dark) land in
# dark mode.
#
# This wrapper injects a tiny script as the FIRST child of <head> in the
# built SPA index.html. It runs before LibreChat's own theme-boot <script>
# and before the React bundle, writing color-theme=light on every page load,
# so every Klai tenant is always in light mode regardless of OS preference or
# a previously-stored choice. (Per the explicit "altijd light" request the
# dark toggle still renders but is effectively reset to light on each load —
# there is no config to hide it without patching the hashed bundle.)
#
# Design constraints (why this exact shape):
#   * Additive insert — it never rewrites the hashed /assets/index-<hash>.js
#     reference, so it survives LibreChat image upgrades automatically. A
#     whole-file bind-mount of index.html would freeze that hash and white-
#     screen on the next upgrade (see platform/librechat.md).
#   * Idempotent via the `klai-force-light` marker. `docker restart` keeps the
#     writable layer (so the marker is already present); `up -d --force-recreate`
#     resets it to the image and this re-runs once.
#   * Fail-safe: nothing here may stop LibreChat from booting. If the index.html
#     path moves in a future version, or node/sed misbehaves, we log and boot
#     normally (theme just not forced — visible by the absent marker in logs).
#
# Wired identically by two deployment paths (keep them in lockstep):
#   * deploy/docker-compose.yml          — librechat-getklai (compose-managed)
#   * provisioning/infrastructure.py     — _start_librechat_container (per tenant)
# Synced to the host (/opt/klai/librechat/klai-entrypoint.sh) by
# .github/workflows/deploy-compose.yml's sync_and_recreate so the bind-mount
# source exists BEFORE the container is (re)created (no empty-dir race).

set -e

INDEX=/app/client/dist/index.html
MARKER=klai-force-light

if [ -f "$INDEX" ]; then
  if grep -q "$MARKER" "$INDEX" 2>/dev/null; then
    echo "[klai-entrypoint] light-theme already injected, skipping"
  else
    node - "$INDEX" <<'NODE' || echo "[klai-entrypoint] light-theme inject failed (non-fatal), booting anyway"
const { readFileSync, writeFileSync } = require('fs');
const target = process.argv[2];
const html = readFileSync(target, 'utf8');
const idx = html.indexOf('<head>');
if (idx === -1) {
  process.stderr.write('[klai-entrypoint] <head> not found; skipping light-theme force\n');
  process.exit(0);
}
const inject =
  "<script>/*klai-force-light*/try{localStorage.setItem('color-theme','light');}catch(e){}</script>";
const out = html.slice(0, idx + '<head>'.length) + inject + html.slice(idx + '<head>'.length);
writeFileSync(target, out);
process.stdout.write('[klai-entrypoint] light-theme injected\n');
NODE
  fi
else
  echo "[klai-entrypoint] $INDEX not found; skipping light-theme force"
fi

# Hand off to the stock LibreChat boot. The image has no ENTRYPOINT and a
# CMD of ["npm","run","backend"]; both deployment paths pass that command
# through explicitly, so "$@" == `npm run backend`. The node base image
# normally provides docker-entrypoint.sh on PATH — fall back to exec'ing the
# command directly if it is ever absent, so boot can never be blocked here.
if command -v docker-entrypoint.sh >/dev/null 2>&1; then
  exec docker-entrypoint.sh "$@"
else
  exec "$@"
fi
