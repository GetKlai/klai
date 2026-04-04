---
name: codeindex-exploring
description: "Use when the user asks how code works, wants to understand architecture, trace execution flows, or explore unfamiliar parts of the codebase. Examples: \"How does X work?\", \"What calls this function?\", \"Show me the auth flow\""
---

# Exploring Codebases with CodeIndex

## When to Use

- "How does authentication work?"
- "What's the project structure?"
- "Show me the main components"
- "Where is the database logic?"
- Understanding code you haven't seen before

## Workflow

```
1. READ codeindex://repos                          → Discover indexed repos
2. READ codeindex://repo/{name}/context             → Codebase overview, check staleness
3. codeindex_query({query: "<what you want to understand>"})  → Find related execution flows
4. codeindex_context({name: "<symbol>"})            → Deep dive on specific symbol
5. READ codeindex://repo/{name}/process/{name}      → Trace full execution flow
```

> If step 2 says "Index is stale" → run `npx codeindex analyze` in terminal.

## Checklist

```
- [ ] READ codeindex://repo/{name}/context
- [ ] codeindex_query for the concept you want to understand
- [ ] Review returned processes (execution flows)
- [ ] codeindex_context on key symbols for callers/callees
- [ ] READ process resource for full execution traces
- [ ] Read source files for implementation details
```

## Resources

| Resource                                | What you get                                            |
| --------------------------------------- | ------------------------------------------------------- |
| `codeindex://repo/{name}/context`        | Stats, staleness warning (~150 tokens)                  |
| `codeindex://repo/{name}/clusters`       | All functional areas with cohesion scores (~300 tokens) |
| `codeindex://repo/{name}/cluster/{name}` | Area members with file paths (~500 tokens)              |
| `codeindex://repo/{name}/process/{name}` | Step-by-step execution trace (~200 tokens)              |

## Tools

**codeindex_query** — find execution flows related to a concept:

```
codeindex_query({query: "payment processing"})
→ Processes: CheckoutFlow, RefundFlow, WebhookHandler
→ Symbols grouped by flow with file locations
```

**codeindex_context** — 360-degree view of a symbol:

```
codeindex_context({name: "validateUser"})
→ Incoming calls: loginHandler, apiMiddleware
→ Outgoing calls: checkToken, getUserById
→ Processes: LoginFlow (step 2/5), TokenRefresh (step 1/3)
```

## Example: "How does payment processing work?"

```
1. READ codeindex://repo/my-app/context       → 918 symbols, 45 processes
2. codeindex_query({query: "payment processing"})
   → CheckoutFlow: processPayment → validateCard → chargeStripe
   → RefundFlow: initiateRefund → calculateRefund → processRefund
3. codeindex_context({name: "processPayment"})
   → Incoming: checkoutHandler, webhookHandler
   → Outgoing: validateCard, chargeStripe, saveTransaction
4. Read src/payments/processor.ts for implementation details
```
