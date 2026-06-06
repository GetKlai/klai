#!/usr/bin/env node
/**
 * Gated CodeIndex hook for Klai.
 *
 * Keep Claude's hook available for high-signal symbol searches, but avoid
 * invoking CodeIndex for literal UI/config/script greps where local search is
 * faster and more accurate.
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

function readInput() {
  try {
    return JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch {
    return {};
  }
}

function isKlaiPath(cwd) {
  const normalized = path.resolve(cwd || process.cwd());
  return normalized.includes('/Klai/') || normalized.endsWith('/Klai');
}

function extractPattern(toolName, toolInput) {
  if (toolName === 'Grep') {
    return String(toolInput.pattern || '').trim();
  }

  if (toolName !== 'Bash') return null;

  const command = String(toolInput.command || '');
  if (!/\b(?:rg|grep)\b/.test(command)) return null;

  const tokens = command.match(/"[^"]*"|'[^']*'|\S+/g) || [];
  const flagsWithValues = new Set([
    '-e', '-f', '-m', '-A', '-B', '-C', '-g', '--glob', '-t', '--type',
    '--include', '--exclude',
  ]);

  let sawSearchCommand = false;
  let skipNext = false;
  for (const token of tokens) {
    const cleanedToken = token.replace(/^['"]|['"]$/g, '');
    if (skipNext) {
      skipNext = false;
      continue;
    }
    if (!sawSearchCommand) {
      if (/^(?:rg|grep)$/.test(cleanedToken) || /\/(?:rg|grep)$/.test(cleanedToken)) {
        sawSearchCommand = true;
      }
      continue;
    }
    if (cleanedToken.startsWith('-')) {
      if (flagsWithValues.has(cleanedToken)) skipNext = true;
      continue;
    }
    return cleanedToken.trim();
  }

  return null;
}

function shouldAugment(input, pattern) {
  const toolName = input.tool_name || '';
  if (toolName !== 'Grep' && toolName !== 'Bash') return false;
  if (!pattern || pattern.length < 4 || pattern.length > 80) return false;

  const command = String((input.tool_input || {}).command || '');
  const searchText = `${pattern} ${command}`.toLowerCase();

  if (/\.(md|css|json|ya?ml|txt|png|jpe?g|svg|gif|ico|lock|env|sh|xml|html|csv|pdf|woff2?|eot|ttf)\b/i.test(searchText)) {
    return false;
  }

  // Regex/literal UI searches are better served by local grep output.
  if (/[^A-Za-z0-9_.$:-]/.test(pattern)) return false;

  const genericTerms = new Set([
    'select', 'button', 'input', 'dialog', 'switch', 'card', 'label', 'table',
    'tabs', 'icon', 'form', 'settings', 'classname', 'aria', 'role', 'data',
    'testid', 'frontend', 'backend',
  ]);
  if (genericTerms.has(pattern.toLowerCase())) return false;

  // Prefer CodeIndex only for symbol-like names that could map to graph nodes.
  return /^[A-Za-z_][A-Za-z0-9_.$:-]*$/.test(pattern);
}

function emitAdditionalContext(text) {
  console.log(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      additionalContext: text,
    },
  }));
}

function main() {
  const input = readInput();
  const cwd = input.cwd || process.cwd();
  if (input.hook_event_name !== 'PreToolUse') return;
  if (!isKlaiPath(cwd)) return;

  const pattern = extractPattern(input.tool_name || '', input.tool_input || {});
  if (!shouldAugment(input, pattern)) return;

  const child = spawnSync('codeindex', ['augment', '--', pattern], {
    encoding: 'utf8',
    timeout: 5000,
    cwd,
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  if (!child.error && child.status === 0) {
    const output = (child.stderr || child.stdout || '').trim();
    if (output) emitAdditionalContext(output);
  }
}

main();
