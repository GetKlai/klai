#!/bin/bash
# Wrapper: runs codeindex analyze + enrichment in one command.
# Usage: ./scripts/codeindex-analyze-and-enrich.sh [--force]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

disable_claude_codeindex_hooks() {
  local settings="$HOME/.claude/settings.json"
  if [ ! -f "$settings" ]; then
    return 0
  fi

  node - "$settings" <<'NODE'
const fs = require('fs');
const settingsPath = process.argv[2];
const isCodeIndexHook = (hook) => {
  const command = String(hook && hook.command ? hook.command : '');
  return /(^|\/)codeindex-hook\.cjs(?:["'\s]|$)/.test(command) ||
    /(^|\/)codeindex-prompt-hook\.cjs(?:["'\s]|$)/.test(command) ||
    /(^|\/)session-start\.sh(?:["'\s]|$)/.test(command);
};

let settings;
try {
  settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
} catch {
  process.exit(0);
}

if (!settings || typeof settings !== 'object' || !settings.hooks) {
  settings = settings && typeof settings === 'object' ? settings : {};
  settings.hooks = {};
}

for (const [eventName, entries] of Object.entries(settings.hooks)) {
  if (!Array.isArray(entries)) continue;
  const keptEntries = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== 'object' || !Array.isArray(entry.hooks)) {
      keptEntries.push(entry);
      continue;
    }
    const keptHooks = entry.hooks.filter((hook) => !isCodeIndexHook(hook));
    if (keptHooks.length > 0) {
      keptEntries.push({ ...entry, hooks: keptHooks });
    }
  }
  if (keptEntries.length > 0) {
    settings.hooks[eventName] = keptEntries;
  } else {
    delete settings.hooks[eventName];
  }
}

const gatedCommand = 'bash -lc \'script="${CLAUDE_PROJECT_DIR:-}/.claude/scripts/codeindex-gated-hook.cjs"; [ -f "$script" ] || exit 0; exec node "$script"\'';
settings.hooks.PreToolUse = Array.isArray(settings.hooks.PreToolUse)
  ? settings.hooks.PreToolUse
  : [];

const alreadyRegistered = settings.hooks.PreToolUse.some((entry) =>
  entry && Array.isArray(entry.hooks) &&
  entry.hooks.some((hook) => String(hook && hook.command ? hook.command : '') === gatedCommand)
);

if (!alreadyRegistered) {
  settings.hooks.PreToolUse.push({
    matcher: 'Grep|Bash',
    hooks: [{
      type: 'command',
      command: gatedCommand,
      timeout: 6000,
      statusMessage: 'Checking whether CodeIndex graph context is useful...',
    }],
  });
}

fs.writeFileSync(settingsPath, `${JSON.stringify(settings, null, 2)}\n`);
NODE
}

echo ""
echo "  CodeIndex + Enrichment Pipeline"
echo "  ================================"
echo ""

# Phase 1: CodeIndex analyze
if [ "$1" = "--force" ]; then
  codeindex analyze --force
else
  codeindex analyze
fi
disable_claude_codeindex_hooks

# Phase 2: Enrichment
echo ""
echo "  Running enrichment layer..."
echo ""
node "$SCRIPT_DIR/codeindex-enrich.mjs" --repo-path "$REPO_DIR"
