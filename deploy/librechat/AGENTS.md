# Klai LibreChat Instructions

Before changing LibreChat client UI polish in this directory, read:

`klai-portal/frontend/docs/ui-standards.md`

Hard rules:

- Keep LibreChat-specific chrome close to LibreChat's native UI; do not copy
  dashboard cards into chat message metadata.
- Source, citation, retrieval, and agent-activity UI must follow the
  `Chat Disclosure Rows` pattern in the portal UI standards.
- Provenance below answers is secondary: compact, muted, closed by default,
  and never rendered as plain bold headings.
- If `deploy/librechat/klai-entrypoint.sh` injects CSS or DOM transforms, bump
  the marker version so running containers replace older injected blocks.
- Verify injected output with a string check and, for visual UI changes, a
  browser computed-style check before shipping.
