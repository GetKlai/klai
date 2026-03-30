# Third-Party Notices

Klai is licensed under the MIT License. This file documents third-party dependencies
with licenses that differ from MIT or that require attribution.

---

## Mozilla Public License 2.0 (MPL-2.0)

The following packages are licensed under MPL-2.0:

| Package | Version | Used in |
|---------|---------|---------|
| `@blocknote/core` | ^0.47.1 | klai-portal/frontend |
| `@blocknote/mantine` | ^0.47.1 | klai-portal/frontend |
| `@blocknote/react` | ^0.47.1 | klai-portal/frontend |

**Source:** https://github.com/TypeCellOS/BlockNote

### What MPL-2.0 means for this project

MPL-2.0 is a **file-level** copyleft license. The copyleft obligation applies only to
modifications of the MPL-2.0 files themselves — not to the larger application that uses them.

Klai uses BlockNote as an unmodified dependency. We do not distribute modified copies of
BlockNote's source files. Therefore:

- Klai's own code remains MIT-licensed.
- If you fork Klai and modify BlockNote's source files (the `@blocknote/*` packages),
  those modified files must be released under MPL-2.0.
- Using Klai as a self-hosted platform without modifying BlockNote creates no MPL-2.0 obligations.

Full license text: https://www.mozilla.org/en-US/MPL/2.0/

---

## SIL Open Font License 1.1 (OFL-1.1)

The following font packages are licensed under OFL-1.1:

| Package | Font | Used in |
|---------|------|---------|
| `@fontsource-variable/inter` | Inter | klai-portal/frontend |
| `@fontsource-variable/manrope` | Manrope | klai-portal/frontend |
| `@fontsource/libre-baskerville` | Libre Baskerville | klai-portal/frontend |

OFL-1.1 permits free use, modification, and distribution of the fonts. The fonts may not
be sold on their own. No action is required when using them as web fonts in an application.

---

## Apache License 2.0

The following packages are licensed under Apache-2.0:

| Package | Used in |
|---------|---------|
| `class-variance-authority` | klai-portal/frontend |
| `oidc-client-ts` | klai-portal/frontend |
| `asyncpg` | klai-portal/backend |
| `python-multipart` | klai-portal/backend |
| `docker` (Python SDK) | klai-portal/backend |

Apache-2.0 is compatible with MIT. No additional obligations beyond attribution, which is
satisfied by this file.

---

## BSD Licenses

| Package | License | Used in |
|---------|---------|---------|
| `uvicorn` | BSD-3-Clause | klai-portal/backend |
| `httpx` | BSD-3-Clause | klai-portal/backend |

---

*This file was last reviewed against `klai-portal/frontend/package.json` and
`klai-portal/backend/pyproject.toml`. Update after adding new dependencies.*
