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
LIGHT_MARKER=klai-force-light
KB_DISCLOSURE_MARKER=klai-kb-disclosure

if [ -f "$INDEX" ]; then
  node - "$INDEX" "$LIGHT_MARKER" "$KB_DISCLOSURE_MARKER" <<'NODE' || echo "[klai-entrypoint] client polish inject failed (non-fatal), booting anyway"
const { readFileSync, writeFileSync } = require('fs');
const target = process.argv[2];
const lightMarker = process.argv[3];
const disclosureMarker = process.argv[4];
const html = readFileSync(target, 'utf8');
const idx = html.indexOf('<head>');
if (idx === -1) {
  process.stderr.write('[klai-entrypoint] <head> not found; skipping client polish inject\n');
  process.exit(0);
}
const injections = [];
if (!html.includes(lightMarker)) {
  injections.push("<script>/*klai-force-light*/try{localStorage.setItem('color-theme','light');}catch(e){}</script>");
}
if (!html.includes(disclosureMarker)) {
  injections.push(`<style id="klai-kb-disclosure-style">/*klai-kb-disclosure*/
.klai-kb-disclosure{margin:.5rem 0 0;border:1px solid rgb(17 24 39/.08);border-radius:.75rem;background:rgb(249 250 251/.72);overflow:hidden;max-width:42rem}
.klai-kb-disclosure[open]{background:rgb(249 250 251/.9)}
.klai-kb-disclosure summary{min-height:2.25rem;display:flex;align-items:center;gap:.5rem;padding:.5rem .75rem;cursor:pointer;list-style:none;color:rgb(75 85 99/.88);font-size:.8125rem;line-height:1.25rem}
.klai-kb-disclosure summary::-webkit-details-marker{display:none}
.klai-kb-disclosure summary:before{content:"";width:.4rem;height:.4rem;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(-45deg);transition:transform .15s ease;flex:0 0 auto}
.klai-kb-disclosure[open] summary:before{transform:rotate(45deg)}
.klai-kb-disclosure summary:hover{color:rgb(17 24 39);background:rgb(0 0 0/.025)}
.klai-kb-disclosure-title{font-weight:600;min-width:0;flex:1}
.klai-kb-disclosure-count{font-size:.75rem;color:rgb(107 114 128/.78);white-space:nowrap}
.klai-kb-disclosure-body{padding:0 .75rem .75rem 1.8rem;color:rgb(75 85 99);font-size:.8125rem}
.klai-kb-disclosure-body ul,.klai-kb-disclosure-body ol{margin:.15rem 0 0 1rem;padding:0}
.klai-kb-disclosure-body li{margin:.15rem 0}
</style>
<script id="klai-kb-disclosure-script">/*klai-kb-disclosure*/
(()=>{const H=new Set(["Bronnen","Agent activiteit"]);const label=(name,n)=>name==="Bronnen"?(n===1?"1 bron":n+" bronnen"):(n===1?"1 stap":n+" stappen");const block=e=>{let n=e;while(n&&n.parentElement&&!["P","DIV","LI"].includes(n.tagName))n=n.parentElement;return n||e};const count=nodes=>{const l=nodes.find(n=>/^[UO]L$/.test(n.tagName));return l?l.querySelectorAll(":scope > li").length:0};const wrap=s=>{if(!s||s.dataset.klaiKbDisclosure==="1")return;const name=(s.textContent||"").trim();if(!H.has(name))return;const head=block(s);if(head.closest(".klai-kb-disclosure"))return;const parent=head.parentElement;if(!parent)return;const body=[];let next=head.nextElementSibling;while(next){const strong=next.querySelector?.("strong");const text=(strong?.textContent||"").trim();if(H.has(text))break;if(next.classList?.contains("klai-kb-disclosure"))break;body.push(next);next=next.nextElementSibling}if(body.length===0)return;const d=document.createElement("details");d.className="klai-kb-disclosure klai-kb-disclosure--"+(name==="Bronnen"?"sources":"activity");const summary=document.createElement("summary");const title=document.createElement("span");title.className="klai-kb-disclosure-title";title.textContent=name;const c=document.createElement("span");c.className="klai-kb-disclosure-count";c.textContent=label(name,count(body));summary.append(title,c);const inner=document.createElement("div");inner.className="klai-kb-disclosure-body";for(const node of body)inner.appendChild(node);d.append(summary,inner);head.replaceWith(d);s.dataset.klaiKbDisclosure="1"};const scan=root=>{for(const s of root.querySelectorAll?.("strong")||[])wrap(s)};const run=()=>scan(document.body);new MutationObserver(m=>{for(const x of m)for(const n of x.addedNodes)n.nodeType===1&&scan(n)}).observe(document.documentElement,{childList:true,subtree:true});document.readyState==="loading"?document.addEventListener("DOMContentLoaded",run):run();})();</script>`);
}
if (!injections.length) {
  process.stdout.write('[klai-entrypoint] client polish already injected, skipping\n');
  process.exit(0);
}
const out = html.slice(0, idx + '<head>'.length) + injections.join('') + html.slice(idx + '<head>'.length);
writeFileSync(target, out);
process.stdout.write(`[klai-entrypoint] client polish injected (${injections.length} block(s))\n`);
NODE
else
  echo "[klai-entrypoint] $INDEX not found; skipping client polish inject"
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
