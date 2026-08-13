#!/bin/sh
# SPEC-KB-015: Apply Klai feedback forwarding patch to LibreChat messages.js at startup.
# Uses node.js string replacement -- 'patch' binary is not available in the LibreChat image.
# Fails safe: if insertion point not found (LibreChat upgrade), LibreChat starts without forwarding.
#
# If the patch fails after a LibreChat upgrade:
#   1. Extract original: docker exec klai-core-librechat-klai-1 cat /app/api/server/routes/messages.js > upstream.js
#   2. Find new insertion point (after updateFeedback context, before res.json)
#   3. Update the FIND string below to match the new context lines
#   4. Redeploy

set -e

TARGET=/app/api/server/routes/messages.js

echo "[klai-entrypoint] Checking SPEC-KB-015 feedback patch..."

if grep -q "SPEC-KB-015" "$TARGET" 2>/dev/null; then
    echo "[klai-entrypoint] Patch already applied (SPEC-KB-015 marker found), skipping."
else
    node - << 'EOF'
const { readFileSync, writeFileSync } = require('fs');

const target = '/app/api/server/routes/messages.js';
const content = readFileSync(target, 'utf8');

// Unique insertion point: end of updateMessage call in feedback route, just before res.json
const FIND = "      { context: 'updateFeedback' },\n    );\n\n    res.json({";

const REPLACE = `      { context: 'updateFeedback' },
    );

    // SPEC-KB-015: Forward feedback to portal-api for KB quality scoring.
    // Fire-and-forget (REQ-KB-015-06) -- response is sent immediately below.
    // Logs on error (REQ-KB-015-07): never surfaces to user, but visible in VictoriaLogs.
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
            updatedMessage?.createdAt?.toISOString?.() ?? new Date().toISOString(),
          rating: feedback.rating,
          tag: feedback.tag ?? null,
          text: feedback.text ?? null,
          model_alias: updatedMessage?.model ?? null,
          librechat_user_id: req.user?.id ?? '',
          librechat_tenant_id: req.user?.tenantId ?? null,
        }),
      }).catch((err) => {
        // REQ-KB-015-07: never surface to user, but log so failures are visible
        logger.warn('SPEC-KB-015: kb-feedback forward failed', { error: err?.message });
      });
    }

    res.json({`;

if (!content.includes(FIND)) {
  process.stderr.write('[klai-entrypoint] WARNING: Insertion point not found. LibreChat was probably upgraded.\n');
  process.stderr.write('[klai-entrypoint] See deploy/librechat/entrypoint.sh for re-apply instructions.\n');
  process.stderr.write('[klai-entrypoint] Starting LibreChat WITHOUT kb-feedback forwarding (SPEC-KB-015 inactive).\n');
  process.exit(0);
}

writeFileSync(target, content.replace(FIND, REPLACE));
process.stdout.write('[klai-entrypoint] Patch applied successfully.\n');
EOF
fi

# Hand off to original LibreChat entrypoint + command
exec docker-entrypoint.sh npm run backend
