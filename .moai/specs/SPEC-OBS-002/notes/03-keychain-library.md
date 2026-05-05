# Note 03 — Keychain library decision

**Status:** ⏳ Deferred to M3 (launcher implementation)

## Question

Which library does `klai-login` and `victorialogs-launcher.mjs` use to
write/read the refresh-token in the OS keychain?

## Options

### Option A — `keytar` (npm package)

- Battle-tested across Atom/VSCode/Slack/etc.
- One API, three platforms (macOS Keychain, Windows Cred Manager, libsecret).
- Pros: cleanest code, well-known.
- Cons: native build per Node version; `keytar` is in maintenance mode
  (last release 2022). Replacement candidates:
  `@napi-rs/keyring` (Rust-based, actively maintained),
  `node-keytar` forks.

### Option B — Shell out to platform CLI

- macOS: `/usr/bin/security add-generic-password -s klai-vlogs-refresh ...`
- Linux: `secret-tool store --label='klai-vlogs-refresh' service klai-vlogs-refresh`
- Windows: PowerShell `Set-Secret` (PowerShell 7+ with `Microsoft.PowerShell.SecretStore`)

Pros: zero external deps, no native compilation.
Cons: 3× platform code paths. Argument-passing tokens via process
arguments leaks them to `ps -ef` momentarily — must use stdin pipe.

## Recommendation (provisional)

**Option A with `@napi-rs/keyring` if `keytar` proves stale at M3 time.**
The launcher is already a small Node module; one more dep is acceptable
in exchange for a single code path.

If we want zero native deps, Option B with stdin-pipe for the token
value is feasible. Shell quoting becomes a security concern — token
characters (base64+`-_`) shouldn't break a quoted shell string but I'd
rather not bet on it under all locales / shells.

## Decision criterion at M3

Pick whichever results in fewer lines of code that we'd need to maintain
ourselves. If `keytar` install is non-blocking (no native-build issues)
on Klai-issued laptops, use it. If it breaks the install, switch to
Option B.

## Out of scope for this note

- Linux developer laptops without libsecret. SPEC explicitly assumes
  libsecret is available (mainstream desktop Linux). If a future Klai
  Linux user lacks it, that's a per-laptop fix.
