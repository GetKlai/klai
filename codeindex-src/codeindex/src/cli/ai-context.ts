/**
 * AI Context Generator
 * 
 * Creates AGENTS.md and CLAUDE.md with full inline CodeIndex context.
 * AGENTS.md is the standard read by Cursor, Windsurf, OpenCode, Cline, etc.
 * CLAUDE.md is for Claude Code which only reads that file.
 */

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

interface RepoStats {
  files?: number;
  nodes?: number;
  edges?: number;
  communities?: number;
  clusters?: number;       // Aggregated cluster count (what tools show)
  processes?: number;
}

const CODEINDEX_START_MARKER = '<!-- codeindex:start -->';
const CODEINDEX_END_MARKER = '<!-- codeindex:end -->';

/**
 * Generate the full CodeIndex context content.
 * 
 * Design principles (learned from real agent behavior):
 * - AGENTS.md is the ROUTER — it tells the agent WHICH skill to read
 * - Skills contain the actual workflows — AGENTS.md does NOT duplicate them
 * - Bold **IMPORTANT** block + "Skills — Read First" heading — agents skip soft suggestions
 * - One-line quick start (read context resource) gives agents an entry point
 * - Tools/Resources sections are labeled "Reference" — agents treat them as lookup, not workflow
 */
function generateCodeIndexContent(projectName: string, stats: RepoStats): string {
  return `${CODEINDEX_START_MARKER}
# CodeIndex MCP

This project is indexed by CodeIndex as **${projectName}** (${stats.nodes || 0} symbols, ${stats.edges || 0} relationships, ${stats.processes || 0} execution flows).

## Rules (MUST follow)

- **Before ANY code modification**: call \`impact\` on the symbol(s) you will change
- **Before searching code**: try \`query\` first — only use Grep/Glob if CodeIndex returns nothing useful
- **"How does X work?" questions**: use \`query\` or \`context\` — do NOT start with Grep
- **Skip CodeIndex only for**: non-code conversations, config edits, or single known-file changes

## Always Start Here

1. **Read \`codeindex://repo/{name}/context\`** — codebase overview + check index freshness
2. **Match your task to a skill below** and **read that skill file**
3. **Follow the skill's workflow and checklist**

> If step 1 warns the index is stale, run \`codeindex update\` in the terminal first.

## Skills

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | \`.claude/skills/codeindex/codeindex-exploring/SKILL.md\` |
| Blast radius / "What breaks if I change X?" | \`.claude/skills/codeindex/codeindex-impact-analysis/SKILL.md\` |
| Trace bugs / "Why is X failing?" | \`.claude/skills/codeindex/codeindex-debugging/SKILL.md\` |
| Rename / extract / split / refactor | \`.claude/skills/codeindex/codeindex-refactoring/SKILL.md\` |
| Tools, resources, schema reference | \`.claude/skills/codeindex/codeindex-guide/SKILL.md\` |
| Index, status, clean, wiki CLI commands | \`.claude/skills/codeindex/codeindex-cli/SKILL.md\` |

${CODEINDEX_END_MARKER}`;
}


/**
 * Check if a file exists
 */
async function fileExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Create or update CodeIndex section in a file
 * - If file doesn't exist: create with CodeIndex content
 * - If file exists without CodeIndex section: append
 * - If file exists with CodeIndex section: replace that section
 */
async function upsertCodeIndexSection(
  filePath: string,
  content: string
): Promise<'created' | 'updated' | 'appended' | 'unchanged'> {
  const exists = await fileExists(filePath);

  if (!exists) {
    await fs.writeFile(filePath, content, 'utf-8');
    return 'created';
  }

  const existingContent = await fs.readFile(filePath, 'utf-8');

  // Check if CodeIndex section already exists
  const startIdx = existingContent.indexOf(CODEINDEX_START_MARKER);
  const endIdx = existingContent.indexOf(CODEINDEX_END_MARKER);

  if (startIdx !== -1 && endIdx !== -1) {
    // Replace existing section — but skip write if content is identical
    const before = existingContent.substring(0, startIdx);
    const after = existingContent.substring(endIdx + CODEINDEX_END_MARKER.length);
    const newContent = (before + content + after).trim() + '\n';
    if (newContent === existingContent) return 'unchanged';
    await fs.writeFile(filePath, newContent, 'utf-8');
    return 'updated';
  }

  // Append new section
  const newContent = existingContent.trim() + '\n\n' + content + '\n';
  await fs.writeFile(filePath, newContent, 'utf-8');
  return 'appended';
}

/**
 * Install CodeIndex skills to .claude/skills/codeindex/
 * Works natively with Claude Code, Cursor, and GitHub Copilot
 */
async function installSkills(repoPath: string): Promise<string[]> {
  const skillsDir = path.join(repoPath, '.claude', 'skills', 'codeindex');
  const installedSkills: string[] = [];

  // Skill definitions bundled with the package
  const skills = [
    {
      name: 'codeindex-exploring',
      description: 'Use when the user asks how code works, wants to understand architecture, trace execution flows, or explore unfamiliar parts of the codebase. Examples: "How does X work?", "What calls this function?", "Show me the auth flow"',
    },
    {
      name: 'codeindex-debugging',
      description: 'Use when the user is debugging a bug, tracing an error, or asking why something fails. Examples: "Why is X failing?", "Where does this error come from?", "Trace this bug"',
    },
    {
      name: 'codeindex-impact-analysis',
      description: 'Use when the user wants to know what will break if they change something, or needs safety analysis before editing code. Examples: "Is it safe to change X?", "What depends on this?", "What will break?"',
    },
    {
      name: 'codeindex-refactoring',
      description: 'Use when the user wants to rename, extract, split, move, or restructure code safely. Examples: "Rename this function", "Extract this into a module", "Refactor this class", "Move this to a separate file"',
    },
    {
      name: 'codeindex-guide',
      description: 'Use when the user asks about CodeIndex itself — available tools, how to query the knowledge graph, MCP resources, graph schema, or workflow reference. Examples: "What CodeIndex tools are available?", "How do I use CodeIndex?"',
    },
    {
      name: 'codeindex-cli',
      description: 'Use when the user needs to run CodeIndex CLI commands like analyze/index a repo, check status, clean the index, generate a wiki, or list indexed repos. Examples: "Index this repo", "Reanalyze the codebase", "Generate a wiki"',
    },
  ];

  for (const skill of skills) {
    const skillDir = path.join(skillsDir, skill.name);
    const skillPath = path.join(skillDir, 'SKILL.md');

    try {
      // Create skill directory
      await fs.mkdir(skillDir, { recursive: true });

      // Try to read from package skills directory
      const packageSkillPath = path.join(__dirname, '..', '..', 'skills', `${skill.name}.md`);
      let skillContent: string;

      try {
        skillContent = await fs.readFile(packageSkillPath, 'utf-8');
      } catch {
        // Fallback: generate minimal skill content
        skillContent = `---
name: ${skill.name}
description: ${skill.description}
---

# ${skill.name.charAt(0).toUpperCase() + skill.name.slice(1)}

${skill.description}

Use CodeIndex tools to accomplish this task.
`;
      }

      // Only write if new or changed
      let existingSkill = '';
      try { existingSkill = await fs.readFile(skillPath, 'utf-8'); } catch {}
      if (existingSkill !== skillContent) {
        await fs.writeFile(skillPath, skillContent, 'utf-8');
        installedSkills.push(skill.name);
      }
    } catch (err) {
      // Skip on error, don't fail the whole process
      console.warn(`Warning: Could not install skill ${skill.name}:`, err);
    }
  }

  return installedSkills;
}

/**
 * Generate AI context files after indexing
 */
export async function generateAIContextFiles(
  repoPath: string,
  _storagePath: string,
  projectName: string,
  stats: RepoStats
): Promise<{ files: string[] }> {
  const content = generateCodeIndexContent(projectName, stats);
  const createdFiles: string[] = [];

  // Create AGENTS.md (standard for Cursor, Windsurf, OpenCode, Cline, etc.)
  const agentsPath = path.join(repoPath, 'AGENTS.md');
  const agentsResult = await upsertCodeIndexSection(agentsPath, content);
  if (agentsResult === 'created') {
    createdFiles.push('AGENTS.md');
  }

  // Create CLAUDE.md (for Claude Code)
  const claudePath = path.join(repoPath, 'CLAUDE.md');
  const claudeResult = await upsertCodeIndexSection(claudePath, content);
  if (claudeResult === 'created') {
    createdFiles.push('CLAUDE.md');
  }

  // Install skills to .claude/skills/codeindex/
  const installedSkills = await installSkills(repoPath);
  if (installedSkills.length > 0) {
    createdFiles.push(`.claude/skills/codeindex/ (${installedSkills.length} skills)`);
  }

  return { files: createdFiles };
}

