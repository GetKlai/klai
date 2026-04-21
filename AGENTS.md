<!-- codeindex:start -->
# CodeIndex MCP

This project is indexed by CodeIndex as **klai** (7475 symbols, 16741 relationships, 300 execution flows).

## Rules (MUST follow)

- **Before ANY code modification**: call `impact` on the symbol(s) you will change
- **Before searching code**: try `query` first — only use Grep/Glob if CodeIndex returns nothing useful
- **"How does X work?" questions**: use `query` or `context` — do NOT start with Grep
- **Skip CodeIndex only for**: non-code conversations, config edits, or single known-file changes

## Always Start Here

1. **Read `codeindex://repo/{name}/context`** — codebase overview + check index freshness
2. **Match your task to a skill below** and **read that skill file**
3. **Follow the skill's workflow and checklist**

> If step 1 warns the index is stale, run `codeindex update` in the terminal first.

## Skills

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/codeindex/codeindex-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/codeindex/codeindex-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/codeindex/codeindex-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/codeindex/codeindex-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/codeindex/codeindex-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/codeindex/codeindex-cli/SKILL.md` |

<!-- codeindex:end -->
