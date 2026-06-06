#!/bin/sh
# Klai LibreChat entrypoint wrapper — getklai canary variant.
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

case "${SEARCH:-}" in
  true|TRUE|True|1|yes|YES|on|ON)
    if [ -z "${MEILI_MESSAGES_INDEX:-}" ] || [ -z "${MEILI_CONVOS_INDEX:-}" ]; then
      echo "[klai-entrypoint] SEARCH=true requires MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX; refusing unsafe global Meili indexes" >&2
      exit 1
    fi
    ;;
esac

if [ -n "${MEILI_MESSAGES_INDEX:-}" ] || [ -n "${MEILI_CONVOS_INDEX:-}" ]; then
  if [ -z "${MEILI_MESSAGES_INDEX:-}" ] || [ -z "${MEILI_CONVOS_INDEX:-}" ]; then
    echo "[klai-entrypoint] both MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX are required for tenant-scoped search" >&2
    exit 1
  fi

  node <<'NODE'
const { existsSync, readFileSync, writeFileSync } = require('fs');

const messageIndexExpr = "process.env.MEILI_MESSAGES_INDEX || 'messages'";
const convoIndexExpr = "process.env.MEILI_CONVOS_INDEX || 'convos'";

const files = [
  {
    path: '/app/packages/data-schemas/dist/models/message.cjs',
    required: true,
    forbidden: [{ label: 'global messages model indexName', pattern: /indexName:\s*['"]messages['"]/ }],
    replacements: [
      {
        label: 'message model indexName',
        search: /indexName:\s*['"]messages['"]/,
        replacement: `indexName: ${messageIndexExpr}`,
        already: new RegExp(`indexName:\\s*${escapeRegExp(messageIndexExpr)}`),
      },
    ],
  },
  {
    path: '/app/packages/data-schemas/dist/models/convo.cjs',
    required: true,
    forbidden: [{ label: 'global convos model indexName', pattern: /indexName:\s*['"]convos['"]/ }],
    replacements: [
      {
        label: 'conversation model indexName',
        search: /indexName:\s*['"]convos['"]/,
        replacement: `indexName: ${convoIndexExpr}`,
        already: new RegExp(`indexName:\\s*${escapeRegExp(convoIndexExpr)}`),
      },
    ],
  },
  {
    path: '/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs',
    required: true,
    forbidden: [
      { label: 'global messages client index', pattern: /client\.index\(['"]messages['"]\)/ },
      { label: 'global convos client index', pattern: /client\.index\(['"]convos['"]\)/ },
    ],
    replacements: [
      {
        label: 'mongoMeili hard-coded messages index',
        search: /client\.index\(['"]messages['"]\)/g,
        replacement: `client.index(${messageIndexExpr})`,
        already: new RegExp(`client\\.index\\(${escapeRegExp(messageIndexExpr)}\\)`),
      },
      {
        label: 'mongoMeili hard-coded convos index',
        search: /client\.index\(['"]convos['"]\)/g,
        replacement: `client.index(${convoIndexExpr})`,
        already: new RegExp(`client\\.index\\(${escapeRegExp(convoIndexExpr)}\\)`),
      },
    ],
  },
  {
    path: '/app/api/db/indexSync.js',
    required: true,
    forbidden: [
      { label: 'global messages client index', pattern: /client\.index\(['"]messages['"]\)/ },
      { label: 'global convos client index', pattern: /client\.index\(['"]convos['"]\)/ },
    ],
    replacements: [
      {
        label: 'indexSync hard-coded messages index',
        search: /client\.index\(['"]messages['"]\)/g,
        replacement: `client.index(${messageIndexExpr})`,
        already: new RegExp(`client\\.index\\(${escapeRegExp(messageIndexExpr)}\\)`),
      },
      {
        label: 'indexSync hard-coded convos index',
        search: /client\.index\(['"]convos['"]\)/g,
        replacement: `client.index(${convoIndexExpr})`,
        already: new RegExp(`client\\.index\\(${escapeRegExp(convoIndexExpr)}\\)`),
      },
    ],
  },
];

for (const file of files) {
  if (!existsSync(file.path)) {
    if (file.required) {
      throw new Error(`[klai-entrypoint] required LibreChat Meili patch target is missing: ${file.path}`);
    }
    continue;
  }

  let content = readFileSync(file.path, 'utf8');
  let changed = false;
  for (const replacement of file.replacements) {
    if (replacement.already.test(content)) {
      continue;
    }
    if (!replacement.search.test(content)) {
      throw new Error(`[klai-entrypoint] could not apply ${replacement.label} in ${file.path}`);
    }
    content = content.replace(replacement.search, replacement.replacement);
    changed = true;
  }
  if (changed) {
    writeFileSync(file.path, content);
    process.stdout.write(`[klai-entrypoint] tenant-scoped Meili patch applied: ${file.path}\n`);
  }
  for (const forbidden of file.forbidden || []) {
    if (forbidden.pattern.test(content)) {
      throw new Error(`[klai-entrypoint] unsafe global Meili reference remains in ${file.path}: ${forbidden.label}`);
    }
  }
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
NODE
fi


INDEX=/app/client/dist/index.html
LIGHT_MARKER=klai-force-light
FOOTER_MARKER=klai-hide-librechat-footer-v1
KB_DISCLOSURE_MARKER=klai-kb-disclosure-v7

if [ -f "$INDEX" ]; then
  node - "$INDEX" "$LIGHT_MARKER" "$FOOTER_MARKER" "$KB_DISCLOSURE_MARKER" <<'NODE' || echo "[klai-entrypoint] client polish inject failed (non-fatal), booting anyway"
const { readFileSync, writeFileSync } = require('fs');
const target = process.argv[2];
const lightMarker = process.argv[3];
const footerMarker = process.argv[4];
const disclosureMarker = process.argv[5];
let html = readFileSync(target, 'utf8');
if (!html.includes(disclosureMarker)) {
  html = html
    .replace(/<style id="klai-kb-disclosure-style">[\s\S]*?<\/style>\s*/g, '')
    .replace(/<script id="klai-kb-disclosure-script">[\s\S]*?<\/script>\s*/g, '');
}
const idx = html.indexOf('<head>');
if (idx === -1) {
  process.stderr.write('[klai-entrypoint] <head> not found; skipping client polish inject\n');
  process.exit(0);
}
const injections = [];
if (!html.includes(lightMarker)) {
  injections.push("<script>/*klai-force-light*/try{localStorage.setItem('color-theme','light');}catch(e){}</script>");
}
if (!html.includes(footerMarker)) {
  injections.push(`<style id="klai-hide-librechat-footer-style">/*klai-hide-librechat-footer-v1*/
[role="contentinfo"]{display:none!important}
</style>`);
}
if (!html.includes(disclosureMarker)) {
  injections.push(`<style id="klai-kb-disclosure-style">/*klai-kb-disclosure-v7*/
.klai-kb-disclosure{margin:.9rem 0 0;max-width:38rem;border:0;border-radius:.375rem;background:transparent;overflow:visible;color:#19191880}
.klai-kb-disclosure+.klai-kb-disclosure{margin-top:.125rem}
.klai-kb-disclosure[open]{background:transparent}
.klai-kb-disclosure summary{min-height:1.75rem;display:inline-flex;max-width:100%;align-items:center;gap:.35rem;padding:.125rem .25rem;cursor:pointer;list-style:none;border-radius:.375rem;color:#19191880;font-size:.8125rem;line-height:1.2rem}
.klai-kb-disclosure summary::-webkit-details-marker{display:none}
.klai-kb-disclosure summary:before{content:"";width:.3rem;height:.3rem;border-right:1.25px solid currentColor;border-bottom:1.25px solid currentColor;transform:rotate(-45deg);transition:transform .15s ease;flex:0 0 auto;color:#1919184d}
.klai-kb-disclosure[open] summary:before{transform:rotate(45deg)}
.klai-kb-disclosure summary:hover{background:#f5f4ef99;color:#191918}
.klai-kb-disclosure-title{font-weight:500;min-width:0;flex:0 1 auto;color:#19191880}
.klai-kb-disclosure-count{font-size:.75rem;line-height:1rem;font-weight:400;color:#1919184d;white-space:nowrap}
.klai-kb-disclosure-count:before{content:"·";margin-right:.35rem;color:#19191833}
.klai-kb-disclosure-body{border:0;padding:.15rem 0 .45rem 1.15rem;color:#19191880;font-size:.8125rem;line-height:1.38}
.klai-kb-disclosure-body ul,.klai-kb-disclosure-body ol{margin:0;padding-left:1rem}
.klai-kb-disclosure-body li{margin:.2rem 0}
</style>
<script id="klai-kb-disclosure-script">/*klai-kb-disclosure-v7*/
(()=>{const H=new Set(["Bronnen","Agent activiteit"]);const norm=t=>(t||"").replace(/\\s+/g," ").trim();const title=e=>H.has(norm(e?.textContent))?norm(e.textContent):"";const heading=e=>{if(!(e instanceof HTMLElement))return"";const tag=e.tagName;if(/^H[1-6]$/.test(tag)||["P","LI","STRONG","B"].includes(tag))return title(e);return""};const headingIn=e=>{if(!(e instanceof HTMLElement))return"";const direct=heading(e);if(direct)return direct;const c=e.querySelector("strong,b,h1,h2,h3,h4,h5,h6,p,li");return heading(c)};const block=e=>/^H[1-6]$/.test(e.tagName)||["P","LI"].includes(e.tagName)?e:e.closest("p,li,h1,h2,h3,h4,h5,h6")||e;const label=(name,n)=>name==="Bronnen"?(n===1?"1 bron":n+" bronnen"):(n===1?"1 stap":n+" stappen");const count=nodes=>{const l=nodes.find(n=>/^[UO]L$/.test(n.tagName));return l?l.querySelectorAll(":scope > li").length:nodes.filter(n=>norm(n.textContent)).length};const wrap=e=>{const name=heading(e);if(!name)return;const head=block(e);if(!head||head.dataset.klaiKbDisclosure==="1"||head.closest(".klai-kb-disclosure"))return;const body=[];let next=head.nextElementSibling;while(next){if(next.classList?.contains("klai-kb-disclosure")||headingIn(next))break;if(!["SCRIPT","STYLE"].includes(next.tagName))body.push(next);next=next.nextElementSibling}if(body.length===0){if(next&&headingIn(next)){head.dataset.klaiKbDisclosure="1";head.style.display="none"}return}const d=document.createElement("details");d.className="klai-kb-disclosure klai-kb-disclosure--"+(name==="Bronnen"?"sources":"activity");const summary=document.createElement("summary");const t=document.createElement("span");t.className="klai-kb-disclosure-title";t.textContent=name;const c=document.createElement("span");c.className="klai-kb-disclosure-count";c.textContent=label(name,count(body));summary.append(t,c);const inner=document.createElement("div");inner.className="klai-kb-disclosure-body";for(const node of body)inner.appendChild(node);d.append(summary,inner);head.dataset.klaiKbDisclosure="1";head.replaceWith(d)};const scan=root=>{const list=[];if(root instanceof HTMLElement)list.push(root);list.push(...(root.querySelectorAll?.("strong,b,h1,h2,h3,h4,h5,h6,p,li")||[]));for(const e of list)wrap(e)};let pending=false;const run=()=>{pending=false;scan(document.body)};const schedule=()=>{if(pending)return;pending=true;(window.queueMicrotask||((fn)=>Promise.resolve().then(fn)))(run)};new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true,characterData:true});document.readyState==="loading"?document.addEventListener("DOMContentLoaded",schedule):schedule();})();</script>`);
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
