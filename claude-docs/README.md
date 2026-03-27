# claude-docs

Auto-synced mirror of `klai-claude/docs/`. **Do not edit directly.**

Edit in `klai-claude/docs/` and run `sync-to-root.sh` to push changes here.

## Structure

| Directory | Contents |
|-----------|----------|
| `patterns/` | Copy-paste solutions — devops, frontend, platform, infrastructure, logging, testing, code-quality |
| `pitfalls/` | Mistakes to avoid — process, devops, infrastructure, platform, git, code-quality |
| `architecture/` | Architecture decision records and compatibility reviews |
| `research/` | GTM research, competitor analysis, product positioning, CRO analyses |
| `specs/` | Archived SPEC documents (SPEC-KB-001 through KB-012) |
| `styleguide.md` | UI design system — color tokens, typography, component conventions |
| `patterns.md` | Index of all patterns |
| `pitfalls.md` | Index of all pitfalls |

## Workflow

```bash
# Edit in klai-claude
vim klai-claude/docs/patterns/some-pattern.md

# Push to monorepo
cd klai-claude && bash scripts/sync-to-root.sh
```
