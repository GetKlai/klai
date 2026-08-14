#!/bin/sh
# Klai LibreChat entrypoint wrapper — apply safety guards and client polish for every tenant.
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
#   * Client-polish injection is fail-safe: if the index.html path moves in a
#     future version, or node/sed misbehaves, we log and boot normally (theme
#     just not forced — visible by the absent marker in logs).
#   * Meili tenant-index wiring is fail-loud: SEARCH=true without
#     MEILI_MESSAGES_INDEX and MEILI_CONVOS_INDEX would use upstream global
#     `messages`/`convos` indexes, so the container must not boot.
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

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const messageReplacements = [
  {
    label: 'message model indexName',
    search: /indexName:\s*['"]messages['"]/,
    replacement: `indexName: ${messageIndexExpr}`,
    already: new RegExp(`indexName:\\s*${escapeRegExp(messageIndexExpr)}`),
  },
];
const messageForbidden = [
  { label: 'global messages model indexName', pattern: /indexName:\s*['"]messages['"]/ },
];

const convoReplacements = [
  {
    label: 'conversation model indexName',
    search: /indexName:\s*['"]convos['"]/,
    replacement: `indexName: ${convoIndexExpr}`,
    already: new RegExp(`indexName:\\s*${escapeRegExp(convoIndexExpr)}`),
  },
];
const convoForbidden = [
  { label: 'global convos model indexName', pattern: /indexName:\s*['"]convos['"]/ },
];

const mongoMeiliReplacements = [
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
];
const mongoMeiliForbidden = [
  { label: 'global messages client index', pattern: /client\.index\(['"]messages['"]\)/ },
  { label: 'global convos client index', pattern: /client\.index\(['"]convos['"]\)/ },
];

const indexSyncReplacements = [
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
];
const indexSyncForbidden = [
  { label: 'global messages client index', pattern: /client\.index\(['"]messages['"]\)/ },
  { label: 'global convos client index', pattern: /client\.index\(['"]convos['"]\)/ },
];

// LibreChat's @librechat/data-schemas package switched its bundler to
// rolldown between v0.8.6 and v0.8.7: pre-rolldown, the Mongoose model
// definitions and the mongoMeili plugin lived in three separate files under
// dist/models/; on v0.8.7 those are inlined into one dist/index.cjs bundle
// and dist/models/ no longer exists (2026-08-13 incident: the un-updated
// per-model patch targets threw "required LibreChat Meili patch target is
// missing" and crashlooped the getklai canary). Both shapes are supported
// here — not just the current one — because the entrypoint script and the
// LibreChat image version can be out of lockstep for a window during a
// rolling upgrade: the bind-mounted entrypoint is synced to the host ahead
// of the container recreate, and an already-running tenant container on the
// OLD image can be `docker restart`ed (picking up the new script live)
// before it gets recreated onto the new image. Try the pre-rolldown
// per-model shape first, fall back to the bundled shape, and throw if
// neither is unambiguously present — never guess.
const legacyModelPaths = {
  message: '/app/packages/data-schemas/dist/models/message.cjs',
  convo: '/app/packages/data-schemas/dist/models/convo.cjs',
  mongoMeili: '/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs',
};
const bundledPath = '/app/packages/data-schemas/dist/index.cjs';
const indexSyncPath = '/app/api/db/indexSync.js';

const legacyPathList = Object.values(legacyModelPaths);
const legacyPresent = legacyPathList.filter((p) => existsSync(p));
const bundledPresent = existsSync(bundledPath);

let files;
if (legacyPresent.length === legacyPathList.length) {
  // Pre-rolldown shape (<= v0.8.6): three separate per-model files.
  files = [
    {
      path: legacyModelPaths.message,
      required: true,
      replacements: messageReplacements,
      forbidden: messageForbidden,
    },
    {
      path: legacyModelPaths.convo,
      required: true,
      replacements: convoReplacements,
      forbidden: convoForbidden,
    },
    {
      path: legacyModelPaths.mongoMeili,
      required: true,
      replacements: mongoMeiliReplacements,
      forbidden: mongoMeiliForbidden,
    },
  ];
} else if (legacyPresent.length === 0 && bundledPresent) {
  // Rolldown-bundled shape (>= v0.8.7): all three patch sites live in the
  // single dist/index.cjs bundle.
  files = [
    {
      path: bundledPath,
      required: true,
      replacements: [...messageReplacements, ...convoReplacements, ...mongoMeiliReplacements],
      forbidden: [...messageForbidden, ...convoForbidden, ...mongoMeiliForbidden],
    },
  ];
} else if (legacyPresent.length > 0) {
  throw new Error(
    `[klai-entrypoint] ambiguous LibreChat data-schemas dist shape: found ${legacyPresent.length}/${legacyPathList.length} pre-rolldown per-model files (missing: ${legacyPathList
      .filter((p) => !legacyPresent.includes(p))
      .join(', ')}), bundled ${bundledPath} present=${bundledPresent}; expected either the full pre-rolldown per-model set or the bundled file, not a partial mix`,
  );
} else {
  throw new Error(
    `[klai-entrypoint] required LibreChat Meili patch target is missing: neither pre-rolldown per-model files (${legacyPathList.join(', ')}) nor bundled ${bundledPath} were found`,
  );
}

files.push({
  path: indexSyncPath,
  required: true,
  replacements: indexSyncReplacements,
  forbidden: indexSyncForbidden,
});

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
NODE
fi

# SPEC-KB-015: forward thumbs up/down feedback to portal-api for KB quality
# scoring. Always attempted (like the Meili block above) -- the patch adds
# the forwarding CODE; whether it actually fires at request time is gated by
# PORTAL_INTERNAL_URL / PORTAL_INTERNAL_SECRET being configured in messages.js
# itself. Fail-loud on anchor drift, unlike the old standalone
# deploy/librechat/entrypoint.sh (removed): a LibreChat upgrade that reshapes
# the feedback route must not silently boot with forwarding dark again (see
# the 2026-08-13 review finding -- SPEC-KB-015 was wired nowhere for 5 weeks).
# If this throws after a LibreChat upgrade: extract the new messages.js
# (docker run --rm --entrypoint cat <image> /app/api/server/routes/messages.js),
# find the new insertion point (after updateFeedback's updateMessage call,
# after any upstream sendFeedbackScore/langfuse call, before res.json), and
# update the FIND string below IN BOTH this file and getklai/entrypoint.sh.
FEEDBACK_TARGET=/app/api/server/routes/messages.js
if grep -q "SPEC-KB-015" "$FEEDBACK_TARGET" 2>/dev/null; then
  echo "[klai-entrypoint] SPEC-KB-015 feedback-forward patch already applied, skipping"
else
  node <<'KB_FEEDBACK_NODE'
const { existsSync, readFileSync, writeFileSync } = require('fs');

const target = '/app/api/server/routes/messages.js';
if (!existsSync(target)) {
  throw new Error(`[klai-entrypoint] required SPEC-KB-015 feedback-forward patch target is missing: ${target}`);
}
const content = readFileSync(target, 'utf8');

// Unique insertion point: end of updateFeedback's updateMessage call, after
// LibreChat's own sendFeedbackScore (langfuse) call, just before res.json.
const FIND = "      { context: 'updateFeedback' },\n    );\n\n    // Best-effort: Assistants messages do not have deterministic AgentRun traces.\n    if (!isAssistantsEndpoint(updatedMessage.endpoint)) {\n      sendFeedbackScore({\n        traceId: traceIdForMessage(messageId),\n        feedback: updatedMessage.feedback,\n        metadata: {\n          messageId: updatedMessage.messageId ?? messageId,\n          parentMessageId: updatedMessage.parentMessageId,\n          conversationId: updatedMessage.conversationId ?? conversationId,\n          sessionId: updatedMessage.conversationId ?? conversationId,\n          userId: req?.user?.id,\n          endpoint: updatedMessage.endpoint,\n          sender: updatedMessage.sender,\n          isCreatedByUser: updatedMessage.isCreatedByUser,\n          tokenCount: updatedMessage.tokenCount,\n        },\n      }).catch((err) => logger.error('[langfuse] feedback score failed:', err));\n    }\n\n    res.json({";

const REPLACE = `      { context: 'updateFeedback' },
    );

    // Best-effort: Assistants messages do not have deterministic AgentRun traces.
    if (!isAssistantsEndpoint(updatedMessage.endpoint)) {
      sendFeedbackScore({
        traceId: traceIdForMessage(messageId),
        feedback: updatedMessage.feedback,
        metadata: {
          messageId: updatedMessage.messageId ?? messageId,
          parentMessageId: updatedMessage.parentMessageId,
          conversationId: updatedMessage.conversationId ?? conversationId,
          sessionId: updatedMessage.conversationId ?? conversationId,
          userId: req?.user?.id,
          endpoint: updatedMessage.endpoint,
          sender: updatedMessage.sender,
          isCreatedByUser: updatedMessage.isCreatedByUser,
          tokenCount: updatedMessage.tokenCount,
        },
      }).catch((err) => logger.error('[langfuse] feedback score failed:', err));
    }

    // SPEC-KB-015: Forward feedback to portal-api for KB quality scoring.
    // Fire-and-forget (REQ-KB-015-06) -- response is sent immediately below.
    // Logs on error (REQ-KB-015-07): never surfaces to user, but visible in VictoriaLogs.
    const feedbackMessage = await db
      .getMessage({ user: req.user?.id, messageId })
      .catch(() => null);

    const portalUrl = process.env.PORTAL_INTERNAL_URL;
    const portalSecret = process.env.PORTAL_INTERNAL_SECRET;
    if (portalUrl && portalSecret && feedback) {
      fetch(\`\${portalUrl}/internal/v1/kb-feedback\`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: \`Bearer \${portalSecret}\`,
        },
        body: JSON.stringify({
          conversation_id: conversationId,
          message_id: messageId,
          message_created_at:
            feedbackMessage?.createdAt?.toISOString?.() ?? new Date().toISOString(),
          rating: feedback.rating,
          tag: feedback.tag ?? null,
          text: feedback.text ?? null,
          model_alias: feedbackMessage?.model ?? updatedMessage?.model ?? null,
          librechat_user_id: req.user?.id ?? '',
          identity_user_id: req.user?.openidId ?? null,
          librechat_tenant_id: process.env.KLAI_ORG_SLUG ? \`librechat-\${process.env.KLAI_ORG_SLUG}\` : null,
        }),
      }).catch((err) => {
        // REQ-KB-015-07: never surface to user, but log so failures are visible
        logger.warn('SPEC-KB-015: kb-feedback forward failed', { error: err?.message });
      });
    }

    res.json({`;

if (!content.includes(FIND)) {
  throw new Error(`[klai-entrypoint] could not apply SPEC-KB-015 feedback-forward patch in ${target}: expected updateFeedback/sendFeedbackScore anchor not found (LibreChat upgrade likely changed messages.js; re-derive FIND against the new image)`);
}

writeFileSync(target, content.replace(FIND, REPLACE));
process.stdout.write(`[klai-entrypoint] SPEC-KB-015 feedback-forward patch applied: ${target}\n`);
KB_FEEDBACK_NODE
fi

# SPEC-STREAM-CLEANUP-001: keep completed generation jobs briefly instead of
# discarding them immediately, so a client that reconnects right as an
# answer completes can still retrieve it. GenerationJobManager resolves
# `services.cleanupOnComplete ?? true` (and its own constructor's
# `options?.cleanupOnComplete ?? true`), so createStreamServices() must
# literally return `cleanupOnComplete: false` in BOTH the Redis-backed and
# in-memory branches, or the upstream default `true` (discard immediately)
# silently wins.
#
# Measured 2026-08-14: the earlier deploy/librechat/patches/createStreamServices.ts
# bind-mount over the SOURCE file
# /app/packages/api/src/stream/createStreamServices.ts was INERT. The
# runtime loads @librechat/api via the workspace symlink
# node_modules/@librechat/api -> packages/api, whose package.json `main`
# points at the pre-built /app/packages/api/dist/index.cjs bundle; nothing
# recompiles the mounted .ts at container start, so the mount was silently
# ignored (confirmed by reading the running bundle: its createStreamServices
# returned no cleanupOnComplete key at all, resolving to the upstream
# default `true`). This patches the BUILT bundle in place instead -- same
# transform-at-boot pattern as the Meili and SPEC-KB-015 blocks above -- so
# the setting actually takes effect. The mounted-.ts patch, its manifest
# entries, and the old mount-pinning test were removed in the same change
# that added this block.
#
# Anchors: `isRedis: true` and `isRedis: false` are each exactly one
# occurrence in the built bundle (verified 2026-08-14 against
# ghcr.io/danny-avila/librechat:v0.8.7's /app/packages/api/dist/index.cjs),
# and both live inside the `//#region src/stream/createStreamServices.ts`
# ... `//#endregion` block. The transform below scopes its search to that
# region as a second line of defense even though the anchors are already
# globally unique. Fail-loud on anchor drift: a LibreChat upgrade that
# reshapes this code must not boot silently unpatched.
#
# If this throws after a LibreChat upgrade: extract the new bundle
# (docker run --rm --entrypoint cat <image> /app/packages/api/dist/index.cjs),
# search for `//#region src/stream/createStreamServices.ts`, find the two
# `return { jobStore, eventTransport, isRedis: <true|false> }` object
# literals inside it, and update the anchors below IN BOTH this file and
# getklai/entrypoint.sh.
STREAM_CLEANUP_TARGET=/app/packages/api/dist/index.cjs
# Two ways this can already be satisfied: this transform ran before (leaves
# its own marker), or the image was built from the source diff and carries
# CLEANUP_ON_COMPLETE. The second is the SPEC-LIBRECHAT-PATCH-MODEL-001
# model, and matching on it is what keeps the runtime transform from
# layering a duplicate cleanupOnComplete key on top of the baked-in one.
# The identifier is the marker, not a comment: the bundler strips comments.
if grep -qE "SPEC-STREAM-CLEANUP-001|CLEANUP_ON_COMPLETE" "$STREAM_CLEANUP_TARGET" 2>/dev/null; then
  echo "[klai-entrypoint] cleanup-on-complete already in place (runtime marker or baked-in CLEANUP_ON_COMPLETE), skipping"
else
  node <<'STREAM_CLEANUP_NODE'
const { existsSync, readFileSync, writeFileSync } = require('fs');

const target = '/app/packages/api/dist/index.cjs';
if (!existsSync(target)) {
  throw new Error(`[klai-entrypoint] required SPEC-STREAM-CLEANUP-001 patch target is missing: ${target}`);
}
let content = readFileSync(target, 'utf8');

const REGION_START = '//#region src/stream/createStreamServices.ts';
const regionStart = content.indexOf(REGION_START);
if (regionStart === -1) {
  throw new Error(`[klai-entrypoint] SPEC-STREAM-CLEANUP-001 could not locate '${REGION_START}' in ${target} (LibreChat upgrade likely renamed/moved the module)`);
}
const regionEnd = content.indexOf('//#endregion', regionStart);
if (regionEnd === -1) {
  throw new Error(`[klai-entrypoint] SPEC-STREAM-CLEANUP-001 could not locate closing '//#endregion' after ${REGION_START} in ${target}`);
}

let region = content.slice(regionStart, regionEnd);

const REPLACEMENTS = [
  { label: 'Redis-backed branch', find: 'isRedis: true', replace: 'isRedis: true, cleanupOnComplete: false /*SPEC-STREAM-CLEANUP-001*/' },
  { label: 'in-memory branch', find: 'isRedis: false', replace: 'isRedis: false, cleanupOnComplete: false /*SPEC-STREAM-CLEANUP-001*/' },
];

for (const { label, find, replace } of REPLACEMENTS) {
  const count = region.split(find).length - 1;
  if (count !== 1) {
    throw new Error(`[klai-entrypoint] SPEC-STREAM-CLEANUP-001 anchor for ${label} ('${find}') matched ${count} times inside the createStreamServices region of ${target} (expected exactly 1); LibreChat upgrade likely reshaped createStreamServices -- re-derive the anchor against the new image`);
  }
  region = region.replace(find, replace);
}

content = content.slice(0, regionStart) + region + content.slice(regionEnd);
writeFileSync(target, content);
process.stdout.write(`[klai-entrypoint] SPEC-STREAM-CLEANUP-001 cleanup-on-complete patch applied: ${target}\n`);
STREAM_CLEANUP_NODE
fi

INDEX=${KLAI_LIBRECHAT_INDEX:-/app/client/dist/index.html}
LIGHT_MARKER=klai-force-light
FOOTER_MARKER=klai-hide-librechat-footer-v1
KB_DISCLOSURE_MARKER=klai-kb-disclosure-v9

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
  injections.push(`<style id="klai-kb-disclosure-style">/*klai-kb-disclosure-v9*/
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
<script id="klai-kb-disclosure-script">/*klai-kb-disclosure-v9*/
(()=>{const T=t=>{t=(t||"").replace(/\\s+/g," ").trim().toLowerCase();return t==="bronnen"||t==="sources"?"sources":t==="agent activiteit"||t==="agent activity"?"activity":"";};const norm=t=>(t||"").replace(/\\s+/g," ").trim();const EN=(typeof navigator!=="undefined"&&navigator.language||"nl").toLowerCase().indexOf("en")===0;const title=e=>T(e?.textContent)?norm(e.textContent):"";const heading=e=>{if(!(e instanceof HTMLElement))return"";const tag=e.tagName;if(/^H[1-6]$/.test(tag)||["P","LI","STRONG","B"].includes(tag))return title(e);return""};const headingIn=e=>{if(!(e instanceof HTMLElement))return"";const direct=heading(e);if(direct)return direct;const c=e.querySelector("strong,b,h1,h2,h3,h4,h5,h6,p,li");return heading(c)};const block=e=>/^H[1-6]$/.test(e.tagName)||["P","LI"].includes(e.tagName)?e:e.closest("p,li,h1,h2,h3,h4,h5,h6")||e;const label=(type,n)=>type==="sources"?(EN?(n===1?"1 source":n+" sources"):(n===1?"1 bron":n+" bronnen")):(EN?(n===1?"1 step":n+" steps"):(n===1?"1 stap":n+" stappen"));const count=nodes=>{const l=nodes.find(n=>/^[UO]L$/.test(n.tagName));return l?l.querySelectorAll(":scope > li").length:nodes.filter(n=>norm(n.textContent)).length};const wrap=e=>{const name=heading(e);if(!name)return;const type=T(name);const head=block(e);if(!head||head.dataset.klaiKbDisclosure==="1"||head.closest(".klai-kb-disclosure"))return;const body=[];let next=head.nextElementSibling;while(next){if(next.classList?.contains("klai-kb-disclosure")||headingIn(next))break;if(!["SCRIPT","STYLE"].includes(next.tagName))body.push(next);next=next.nextElementSibling}if(body.length===0){if(next&&headingIn(next)){head.dataset.klaiKbDisclosure="1";head.style.display="none"}return}const d=document.createElement("details");d.className="klai-kb-disclosure klai-kb-disclosure--"+type;const summary=document.createElement("summary");const t=document.createElement("span");t.className="klai-kb-disclosure-title";t.textContent=name;const c=document.createElement("span");c.className="klai-kb-disclosure-count";c.textContent=label(type,count(body));summary.append(t,c);const inner=document.createElement("div");inner.className="klai-kb-disclosure-body";for(const node of body)inner.appendChild(node);d.append(summary,inner);head.dataset.klaiKbDisclosure="1";head.replaceWith(d)};const scan=root=>{if(!(root instanceof HTMLElement))return;const list=[root,...root.querySelectorAll("strong,b,h1,h2,h3,h4,h5,h6,p,li")];for(const e of list)wrap(e)};let pending=false;const run=()=>{pending=false;scan(document.body)};const schedule=()=>{if(pending)return;pending=true;(window.queueMicrotask||((fn)=>Promise.resolve().then(fn)))(run)};new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true,characterData:true});document.readyState==="loading"?document.addEventListener("DOMContentLoaded",schedule):schedule();})();</script>`);
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
