---
paths: "**"
---

# No AskUserQuestion

Never use the AskUserQuestion tool. Discuss everything in chat as plain text.

When a workflow requires user approval or a decision:
- Present the information and options as markdown text in the chat
- Wait for the user's text reply
- Do NOT call AskUserQuestion

This applies to all MoAI workflow phases including plan approval, quality gate decisions, and next step options.
