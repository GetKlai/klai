---
paths: ["klai-website/src/content/**/*.md", "klai-website/src/content/**/*.mdoc", "klai-website/.claude/agents/gtm/**/*.md"]
---

# Klai Brand Voice

Operating rules for GTM agents writing or editing content for getklai.com.

**Source of truth:** `klai-website/src/content/company/brand-voice.md` (the public handbook). This file is the agent-facing translation. If the two diverge, the public handbook wins.

**Complementary rules:**
- `klai-humanizer.md` — removes AI writing patterns (apply as final pass)
- `mark-tone-of-voice.md` — Mark Vletter's personal blog voice (different from company voice)

---

## Voice in one line

Klai talks like the senior colleague who actually understands AI. Not the consultant selling it. Not the vendor managing expectations. The colleague who has read the terms, tried the tools, and tells you straight what they found. **Peer authority, no theatre.**

---

## The voice test (run before returning any output)

Read the draft aloud. Check every item:

1. Does it sound like a senior colleague who has done the work, or like a consultant performing confidence?
2. Can every trust claim be traced to a legal fact, a technical detail, a named competitor, or a number?
3. Did we name what does not yet exist, instead of hiding gaps?
4. Did we avoid cognitive verbs for the system (understand, think, learn, know, reason)?
5. Did we resist being clever for the sake of it? No puns, no wordplay for wordplay's sake.
6. Every heading in sentence case?
7. No em-dashes? (house rule)
8. Does it use "colleague" where other companies say "agent" (for people)?

If any answer is no, rewrite before output.

---

## Three principles

All Klai copy expresses at least one of these. Long-form pieces expand on them; product copy demonstrates them.

### 1. We help people spend time on the parts that need a human

AI takes the repetitive and boring parts, so people spend their time on the parts that need a human.

**DO:** Lead with the human gain. "Your meeting notes, handled." Then explain how if the reader wants to know.

**DO:** Action verbs for the system. *Stores, indexes, retrieves, transcribes, returns, matches, cites, synchronises.*

**DON'T:** Cognitive verbs for the system. *Understands, thinks, learns, knows, reasons, believes, decides.* The system does none of these.

**DON'T:** Lead with technology. "Our embedding pipeline transforms input into high-dimensional vector representations" is wrong. "Your data becomes searchable in seconds" is right.

### 2. Judge by structure, not claims

Evaluate companies by what they do, not what they say. Legal structure, infrastructure, defaults, behaviour when offered a big cheque.

**The proof rule.** Every trust claim must be backed by at least one of:

1. A legal fact (articles of association, contract clause, jurisdiction)
2. A verifiable technical detail (architecture, code, inspectable default)
3. A named competitor and a specific behaviour (not "other vendors"; say "Microsoft Copilot, Flex Routing, April 2026")
4. A specific number or date

If none of those apply, cut the claim.

**DO:** Structural facts. *"Klai cannot be sold. Ever."* (legal fact) / *"Your data stays in the EU because no sub-processor outside the EU exists in our list."* (verifiable detail)

**DON'T:** Promise-shaped copy. *"Klai is committed to privacy."* / *"We take security seriously."* These are unverifiable.

**DO:** Apply the same lens to competitors. What they do, named and sourced. *"Microsoft's chief legal officer testified under oath in the French Parliament that no contract can circumvent the CLOUD Act."*

**DON'T:** Unattributed industry jabs. *"Most AI vendors aren't serious about privacy."*

**DO:** Use civic vocabulary when it fits. *commons, patron, public fabric, primary infrastructure, steward-owned, unsellable, precondition.*

### 3. Real transparency, with a stated scope

Transparency on the front of the package, not buried in a PDF.

**DO:** Say what does not exist yet. Use *"not yet"* for roadmap; say *"not planned"* when that is true.

**DO:** State the scope of what Klai shares. *"We share architecture, sub-processors, code, DPA, incidents, roadmap. We do not share customer data, staff personal data, or vulnerabilities before they are patched."*

**DON'T:** Claim total openness. A company that says it shares everything is not credible.

---

## Signature moves (our handwriting)

These patterns must keep appearing in Klai copy.

### The accent-word heading

Every heading ends on a short phrase that lands the punch. The phrase is rendered in the Parabole display variant (`font-accent`), but the rhetorical effect holds without typography.

**Rule:** one short accent phrase per heading, one to three words, always at the end, always doing the work.

Examples:
- *"Your AI. Your data. Your **rules.**"*
- *"Compliance isn't a **checkbox.**"*
- *"AI that knows your **organisation.**"*
- *"Simple pricing. No **surprises.**"*
- *"Something built from all of us should be for **all of us.**"*
- *"Built for the people who need to be able to say **yes.**"*

When drafting Astro components, split the heading into `titleBefore` plus `titleAccent` so the accent renders in `font-accent`.

### Belief, commitment, in practice

The long-form structure for principled pieces and structural claims.

- **Belief:** how the world works and what is wrong with it
- **Commitment:** what Klai concretely does about it
- **In practice:** a real incident (named company, date, number) that proves the point

Use this shape whenever a claim needs weight. Three beats, in order.

### Bait, then flip

Open with a frame the reader expects. Turn it.

- *"Not a values page. A contract with ourselves."*
- *"You already know AI works. The question is whether you can trust it."*

### Question, one-word answer

*"Can Klai be acquired by a US company?"* → *"No."*

Then explain. Never lead with the explanation.

### Triplets and stacked negations

- *"Hosted in Europe, open source, fully yours."*
- *"No infrastructure project, no IT tickets, no lengthy procurement."*
- *"Never shared, never used for training, never sold."*

---

## Two registers

One voice, two speeds.

| | Register A — Practical | Register B — Principled |
|---|---|---|
| **Where** | Product pages, pricing, FAQ, onboarding, microcopy, changelog, errors | Founding principles, about, long-form blog, positioning essays, press |
| **Reader needs** | To decide something | To understand what is at stake |
| **Sentence length** | Short, punchy | Longer argument, then short fact |
| **Signature format** | Setup → accent word | Belief → commitment → in practice |
| **Opener** | Buyer's objection as heading | What is wrong in the world |
| **Closer** | Concrete action or plain fact | Structural fact, not slogan |

**Decision rule:**
- Reader about to buy? Practical.
- Reader deciding whether Klai is their kind of company? Principled.
- Reader using product, something broke? Practical and short.

---

## Vocabulary

### Words we use

*structural, architecture, articles of association, commons, patron, public fabric, primary infrastructure, sovereignty, steward-owned, unsellable, precondition, welcome (for sensitive data), yours, approved, plain language, collective memory, quietly (as in "quietly reconfigured"), conditional, asymmetry, not yet, not planned, including us, public, auditable, verifiable, colleague*

### Words we never use

*leverage, empower, unlock, seamless, solution, journey, transform, cutting-edge, innovate, synergy, ecosystem, game-changing, revolutionary, disruptive, powerful, intuitive, easy, effortless, smart (as adjective), next-gen, state-of-the-art, thrilled, passionate, mission-driven (as slogan), work smarter, we believe, we're excited to, please (as softener)*

### Words we never use for the system

Cognitive verbs: *understands, thinks, learns, knows, reasons, believes, decides, comprehends.*

### Terminology: colleague, not agent

When referring to people in an organisation (support, customer service, etc.), use **"colleague"**, never **"agent"**. People are colleagues. Agents are software.

**DO:** "Your best support colleague."
**DON'T:** "Your best support agent."

---

## Tone across surfaces

| Surface | Pose |
|---|---|
| Hero headings, landing pages | Declarative, short, accent-word signature |
| Feature descriptions | Outcome first, mechanism second, one sentence each |
| Pricing | Flat, specific, no softeners around cost |
| FAQ | Buyer's objection as the question, Yes or No upfront, then reasoning |
| Changelog | Action verb, what changed, who benefits, one line |
| Error messages | What happened, what the user can do, no apology theatre |
| Empty states | Say why it is empty, say what fills it |
| Support replies | Direct answer first, context second, no "great question" |
| Sales emails | No boilerplate opener, one specific reason this fits this org, one ask |
| Outage and incident | Tell it straight, include timeline, say what we are doing and what is still unknown |
| Legal docs | Written so a non-lawyer can read them; link plain-language summary if not |
| Blog posts | Principled register, long-form, argued, named sources. Use Mark's voice per `mark-tone-of-voice.md` |
| LinkedIn | Peer authority, no theatre. If it reads like a motivational poster, rewrite. |

---

## Formatting rules

### Sentence case, always

All titles, headings (H1, H2, H3), meta titles, SEO titles, blog titles, social share titles.

**DO:** "The hidden cost of organisational knowledge loss"
**DON'T:** "The Hidden Cost of Organizational Knowledge Loss"

### No em-dashes

House rule. Replace em-dashes with periods, colons, commas, or parentheses. Rewrite if needed.

### Spelling

British English, not American. *Organisation, not organization. Centre, not center. Behaviour, not behavior.*

### Numbers

Specific numbers over vague quantifiers. *"50 million tenants affected"* beats *"many users affected".* *"€28 per user per month"* beats *"affordable pricing".*

### Named sources

Name the company, the date, the source. *"Microsoft Copilot, Flex Routing, April 2026"* beats *"a recent industry incident".*

---

## Dutch and English

Same voice, different register tuning.

### Dutch (NL)

More direct, more informal. *"Gewoon", "onwijs", "gaaf"* where they fit. Dry humour travels; sharp humour travels better. Self-deprecation is natural. English terms are fine where they read naturally (say *deployment*, not *uitrol*), but explain briefly for non-technical readers.

Example: *"Je kent het: je test ChatGPT met klantdata, het werkt geweldig, en dan belt de compliance officer. Herkenbaar? Dacht ik al."*

### English (international)

Slightly more polished than Dutch, still warm and conversational. Avoid Americanisms when discussing European values. Humour is dry and understated. Avoid idioms that do not travel.

Example: *"You tested ChatGPT with real customer data. It worked beautifully. Then your compliance team had questions. Sound familiar?"*

---

## Humour rules

Humour is welcome when it lands on the absurdity of the situation. Rules:

- Aim the joke at the situation or system, never at individual people
- Be specific. "OpenAI's privacy policy" is funnier than "big tech companies"
- One joke per page is enough. Two is fun. Three is a comedy show, cut one
- If a joke needs explaining, it is not a good joke
- Self-deprecation is welcome: *"Are we perfect? No. But at least your data isn't in Utah."*
- Puns do not suit us
- Trying to be clever does not suit us

---

## DO / DON'T gallery

Quick reference for common rewrites.

### Claim without evidence → structural fact

**DON'T:** "We take your privacy seriously."
**DO:** "Klai cannot be sold. That restriction is written into our articles of association."

### Anthropomorphised system → action verbs

**DON'T:** "Klai understands your documents and learns from them."
**DO:** "Klai indexes your documents and retrieves answers with source references."

### Vague superlative → named comparison

**DON'T:** "Unlike other AI tools, Klai protects your data."
**DO:** "ChatGPT Enterprise and Microsoft Copilot both expose you to the CLOUD Act. Klai does not."

### Marketing verb → concrete outcome

**DON'T:** "Empower your team with next-gen AI."
**DO:** "Your colleagues get a private AI that works with client data. No workaround, no exception."

### Fluff opener → peer statement

**DON'T:** "In today's fast-moving AI landscape, organisations are facing unprecedented challenges."
**DO:** "You tested AI with real client data. The output was amazing. Then someone asked where the data actually goes."

### Corporate hedging → direct

**DON'T:** "It is important to note that our solution may help organisations achieve compliance objectives."
**DO:** "Klai is GDPR and AI Act compliant. The DPA and sub-processor list are public."

### Empty roadmap → honest scope

**DON'T:** "Advanced features coming soon!"
**DO:** "SSO is in the roadmap. Shipping in Q3. API access is not planned."

---

## Output checklist (before returning to user)

1. Voice test passed (all 8 questions above)
2. Every trust claim passes the proof rule (4 evidence types)
3. No cognitive verbs used for the system
4. No words from the "never use" list
5. Accent-word format applied to all headings where the copy is hero/section level
6. Sentence case on all headings
7. No em-dashes
8. British English spelling
9. "Colleague" used for people, not "agent"
10. Humanizer patterns (per `klai-humanizer.md`) cleaned as final pass

---

*Last updated: 2026-04-19. Source of truth for content remains `klai-website/src/content/company/brand-voice.md`.*
