# CodeIndex — Positioning & Messaging Framework
## Target Market: The Netherlands | Senior Developers 50+ | ZZP'ers

**Document type:** GTM Positioning Framework
**Status:** Draft v1.0
**Scope:** NL market launch — primary and secondary persona

---

## 1. Positioning Canvas

### Audience

**Primary — Senior developer / tech lead at Dutch company (enterprise, consultancy, overheid)**
- Age 50+, 25+ years of experience
- Responsible for codebases they did not write
- Frustrated by AI tools that generate code they cannot understand or trust
- Privacy-conscious, GDPR/AVG-aware, skeptical of cloud dependency
- Used to desktop tools; no desire to share proprietary code with a cloud provider
- Dutch work culture: direct, pragmatic, no tolerance for marketing hyperbole

**Secondary — ZZP'er (freelance senior developer)**
- Works across 3–8 client codebases simultaneously
- No institutional IT department; full autonomy over tooling
- Billable by the hour — time spent understanding a new codebase is dead cost
- Data sovereignty is a professional obligation to clients, not just a preference

---

### Problem (as the persona experiences it)

Codebases grow. Documentation rots. The developer who knew how it all fit together left three years ago. A codebase is not a collection of files — it is a web of relationships, intentions, and history that lives nowhere except in the heads of people who may no longer be available.

Text search finds where a symbol appears. It does not explain why it exists, what depends on it, or what will break if you change it. AI code assistants make the problem worse: they generate plausible-looking code without understanding the context, then upload your proprietary code to a cloud service in the process.

---

### Solution

CodeIndex builds a **knowledge graph** of your codebase — every function, class, relationship, execution flow, and community of related symbols — and stores it **entirely on your machine**. It surfaces that knowledge to you and to your AI assistant, so that questions like "what calls this?", "what breaks if I rename this?", and "how does this process actually work?" have precise, graph-derived answers rather than grep results or hallucinated guesses.

It also **remembers across sessions**: decisions, bugs found, patterns observed, preferences — all stored as typed observations linked to the actual symbols in the graph.

---

### Differentiation (one sentence)

CodeIndex is the only tool that builds a local, relationship-aware knowledge graph of your codebase — so your AI assistant understands your code the way you do, without sending a single line to the cloud.

---

## 2. Core Positioning Statement

### English

> CodeIndex gives senior developers a complete, private knowledge graph of any codebase — so they can understand complex code, assess change risk, and refactor safely, without sending proprietary code to the cloud.

### Nederlands

> CodeIndex geeft senior developers een volledig, privaat kennisgraaf van elke codebase — zodat ze complexe code kunnen begrijpen, het risico van wijzigingen kunnen inschatten en veilig kunnen refactoren, zonder dat er ook maar een regel code naar de cloud gaat.

---

## 3. Message Pillars

### Pillar 1 — Your code never leaves your machine

**Headline (EN):** The knowledge graph lives on your disk. Full stop.

**Headline (NL):** De kennisgraaf staat op jouw schijf. Punt uit.

**The message:**
Every indexed codebase is stored under `~/.codeindex/` on your local machine. The graph database, the memory store, the embeddings — all of it runs locally. CodeIndex has no cloud backend, no account requirement, no telemetry pipeline that phones home with your symbol names. When you close the app, your code stays where it was.

**Proof points (from source code):**
- Storage layout is `~/.codeindex/{ProjectName}/` — a plain directory on disk (README.md, storage layout section)
- Graph database (LadybugDB, migrated from KuzuDB) is an embedded database — no server, no network socket
- Memory system uses a separate embedded DB alongside the code graph (memory/types.ts: `MEMORY_DB_DIR = 'memory'`)
- The wiki generator calls a configurable LLM endpoint — the developer controls which model and where it runs
- No authentication layer, no user account, no usage telemetry in the codebase

**Resonance for NL market:**
- AVG/GDPR: client code is personal data in many contexts; "code stays local" is a compliance argument, not just a preference
- Dutch enterprise and overheid IT procurement actively blocks cloud-only tools for sensitive systems
- ZZP'ers contractually cannot share client code with third-party services; local-only is a prerequisite, not a feature

**Objection this pre-empts:** "I cannot use a cloud AI tool — my client's code is confidential."

---

### Pillar 2 — Understanding, not search results

**Headline (EN):** Stop searching your code. Start understanding it.

**Headline (NL):** Stop met zoeken in je code. Begin met begrijpen.

**The message:**
Text search tells you where a string appears. A knowledge graph tells you how code is connected. CodeIndex indexes every function, class, method, interface, and the relationships between them — who calls what, what imports what, what inherits from what — and surfaces this as queryable execution flows, blast-radius analysis, and 360-degree symbol context. When you ask "what will break if I change this?", you get a ranked dependency list with confidence scores, not a list of grep matches.

**Proof points (from source code):**
- Graph schema: nodes are `Function, Class, Interface, Method, Community, Process`; edges are `CALLS, IMPORTS, EXTENDS, IMPLEMENTS, DEFINES, MEMBER_OF, STEP_IN_PROCESS` (codeindex-guide SKILL.md)
- `impact` tool returns depth-ranked dependents: d=1 (WILL BREAK), d=2 (LIKELY AFFECTED), d=3 (MAY NEED TESTING) with confidence scores (codeindex-impact-analysis SKILL.md)
- `query` tool returns process-grouped execution flows, not flat file matches — ranked by hybrid BM25 + semantic RRF scoring (hybrid-search.ts: "same approach used by Elasticsearch, Pinecone")
- `detect_changes` maps git-diff changes to affected execution flows in real time (codeindex-impact-analysis SKILL.md)
- Community detection groups symbols into functional clusters with cohesion scores (cluster-enricher.ts, community-processor.ts)
- Language resolvers cover TypeScript, Python, Go, Java/Kotlin (JVM), C#, Ruby, PHP, Rust, Swift, C/C++ (resolvers/ directory)
- `wiki` command generates full documentation from the graph structure (wiki/generator.ts)

**Resonance for NL market:**
- Senior developers with 25+ years of experience have built and maintained complex systems; they know the limits of grep
- Legacy codebases in NL enterprise (often Java/.NET/PHP, sometimes decades old) are exactly where relationship-aware understanding adds the most value
- "Understanding" is the differentiator against code generators: this tool makes existing code legible, not new code automatic

**Objection this pre-empts:** "I already have full-text search in my IDE." / "GitHub Copilot answers my questions."

---

### Pillar 3 — Memory that survives the session

**Headline (EN):** Your AI assistant should remember what you told it last week.

**Headline (NL):** Jouw AI-assistent zou moeten onthouden wat je vorige week hebt verteld.

**The message:**
Every interaction with an AI assistant starts from zero. The decision you documented, the bug you found and resolved, the architectural pattern you established — gone the moment the chat window closes. CodeIndex has a typed, searchable memory store, linked directly to the symbols in your code graph. Observations (decisions, bugs, patterns, preferences, do/don't rules) persist across sessions and across the entire team if shared. Your AI agent comes to every session knowing what it needs to know.

**Proof points (from source code):**
- Typed observation categories: `learning, preference, do, dont, decision, bug, pattern, note` (memory/types.ts)
- Observations are linked to code symbols via `ObservationRef` — a memory item can be pinned to a function, file, process, or cluster (memory/types.ts: `refType: 'symbol' | 'file' | 'process' | 'cluster'`)
- Dual scope: `global` (all projects) and `repo` (project-specific) — a preference carries across all repos; a bug note stays with the project (memory/types.ts: `ObservationScope`)
- Recency filter (`days` parameter) enables surfacing only recent observations — no noise from two-year-old notes
- Memory is stored in the same local `~/.codeindex/` directory — it travels with the index, not with a cloud account

**Resonance for NL market:**
- Tech leads at large Dutch companies often act as institutional memory for legacy systems; giving AI that same institutional memory is the right frame
- ZZP'ers returning to a client codebase after three months of absence need to rebuild context fast; saved observations are a professional accelerator
- The "team knowledge" angle resonates with consultancy culture where multiple people work the same codebase over time

**Objection this pre-empts:** "AI tools are stateless — I have to re-explain everything every time." / "I don't want to lose the context I've built up."

---

## 4. Objection Handling

### "My company/client doesn't allow code to leave our premises."

**Response:** That is exactly why CodeIndex exists. The knowledge graph is built and stored entirely on your local machine under `~/.codeindex/`. No registration, no cloud sync, no telemetry. The MCP server that your AI assistant connects to runs as a local process. You can use CodeIndex in an airgapped environment. If your organization requires it, you can point the wiki generator at a self-hosted LLM. Nothing leaves unless you choose to send it.

---

### "I already have GitHub Copilot / Cursor. Why do I need this?"

**Response:** Copilot and Cursor are generation tools. CodeIndex is an understanding tool. They are not competing — CodeIndex makes Copilot and Cursor better by giving them accurate, graph-derived context about your specific codebase, rather than making them guess from file content alone. The Claude Code hook in CodeIndex automatically enriches every file search your AI assistant performs with knowledge graph context. The result is more accurate answers and fewer hallucinations, because the assistant knows how your code is actually connected.

---

### "I'm skeptical of AI tools. They generate plausible-sounding nonsense."

**Response:** That skepticism is correct, and it is exactly the problem CodeIndex solves. CodeIndex does not generate code. It builds a factual, graph-derived map of what your code actually does — derived from AST parsing and relationship analysis, not from a language model guessing. When you ask "what depends on this function?", the answer comes from the graph (which is deterministic), not from an LLM (which is probabilistic). The AI integration is optional: you can use the CLI and query tools directly without any AI involvement at all.

---

### "I'm a ZZP'er — I can't afford enterprise software."

**Response:** CodeIndex is a local tool with no per-seat licensing, no cloud subscription, and no usage-based billing. You install it once, and it works across as many client codebases as you maintain. The open-core license (PolyForm Noncommercial 1.0.0) covers non-commercial and evaluation use. For commercial ZZP use, the pricing model reflects individual practitioner economics, not enterprise seat counts.

---

### "This sounds complicated to set up."

**Response:** `codeindex analyze ProjectName ~/path/to/repo` — that is the entire setup for the first project. From any subsequent location inside that repo or any of its git worktrees, `codeindex analyze` detects the project automatically and only re-indexes what has changed since the last commit. Integration with Claude Code or Cursor is a single JSON block in a config file, which `codeindex setup` generates for you.

---

### "What languages does it support?"

**Response:** TypeScript, JavaScript, Python, Java, Kotlin, Go, C#, PHP, Ruby, Rust, Swift, and C/C++. Framework-aware entry point scoring is built in for Next.js, Django, Rails, Spring, Laravel, and others. For most Dutch enterprise stacks — Java/.NET backend, TypeScript/React frontend, PHP legacy — CodeIndex indexes all layers of the codebase into a single unified knowledge graph.

---

## 5. Competitive Differentiation

### vs. GitHub Copilot

| Dimension | GitHub Copilot | CodeIndex |
|---|---|---|
| Primary capability | Code generation | Code understanding |
| Code leaves machine | Yes — sent to GitHub/OpenAI | Never |
| Codebase model | File-level context window | Full relationship graph |
| Cross-session memory | None | Typed observations linked to symbols |
| Change impact analysis | None | Depth-ranked blast radius with confidence |
| Dependency graph | None | Full CALLS/IMPORTS/EXTENDS/IMPLEMENTS graph |
| Works without internet | No | Yes |

**Positioning line:** Copilot writes new code. CodeIndex understands the code you already have.

**Dutch market angle:** GitHub is a Microsoft/American cloud product. Procurement teams in Dutch overheid and financial services already have Copilot blocked or restricted. CodeIndex has no such barrier.

---

### vs. Cursor

| Dimension | Cursor | CodeIndex |
|---|---|---|
| Primary capability | AI-augmented editor | Standalone knowledge graph tool |
| Code leaves machine | Yes — sent to Cursor AI | Never |
| IDE lock-in | Full IDE replacement required | Works alongside any editor via MCP or CLI |
| Codebase understanding | File-level retrieval (RAG) | Graph-based relationship model |
| Cross-session memory | None | Persistent typed observations |
| Dutch data compliance | Unclear, US-hosted | Fully local, zero cloud dependency |

**Positioning line:** Cursor replaces your editor. CodeIndex enhances your AI, regardless of which editor you use.

**Dutch market angle:** Many senior Dutch developers have used the same IDE for 15–20 years (IntelliJ, Eclipse, VS Code). Asking them to replace their editor to get AI features is a non-starter. CodeIndex works with what they already use.

---

### vs. Sourcegraph

| Dimension | Sourcegraph | CodeIndex |
|---|---|---|
| Primary capability | Enterprise code search + nav | Local knowledge graph + AI memory |
| Deployment | Cloud or self-hosted server | Single user, local machine |
| Team size fit | 50–5000 developers | Individual and small team |
| Price point | Enterprise contract | Individual practitioner |
| Relationship model | Cross-repo search index | Deep single-repo graph with execution flows |
| AI memory | None | Typed observation store linked to graph |
| Offline / airgapped | Self-hosted only (complex) | Yes, by default |

**Positioning line:** Sourcegraph is a fleet management tool for large engineering teams. CodeIndex is a personal knowledge tool for the developer who needs to understand a codebase deeply.

**Dutch market angle:** The typical CodeIndex user is a tech lead or ZZP'er working intensely on one or two codebases. Sourcegraph's value is organizational scale. CodeIndex's value is individual depth.

---

## 6. Value Propositions by Persona

### Primary Persona: Senior Developer / Tech Lead at Dutch Company

**Core job to be done:** Make safe decisions about legacy code without introducing regressions.

**Top 3 value propositions:**

1. **Change safety before you commit.** Run `codeindex impact` on any symbol before touching it. Get a depth-ranked list of what will break — direct callers at d=1, indirect dependents at d=2/3 — with confidence scores. Know before you change, not after the build breaks.

2. **AVG-compliant AI assistance.** Use AI tools on client or internal code without violating data processing agreements. The knowledge graph stays on your machine. Your AI assistant queries a local MCP server. No code crosses the organizational perimeter.

3. **Institutional memory that outlives the team.** The developer who knew this codebase left. You inherit the system. CodeIndex reconstructs the relationships from the code itself — who calls what, what processes exist, how data flows — and lets you add your own observations that persist for the next person who inherits it from you.

**Tone for this persona:** Direct, technical, evidence-based. No marketing language. Lead with the mechanism, not the benefit. They have been lied to by software vendors before.

**Dutch cultural note:** "Doe maar gewoon, dan doe je al gek genoeg." This persona respects restraint. Do not over-claim. Let the tool demonstrate its value.

---

### Secondary Persona: ZZP'er (Freelance Senior Developer)

**Core job to be done:** Get productive on a new client codebase as fast as possible, without compromising the previous client's confidentiality.

**Top 3 value propositions:**

1. **Onboarding in hours, not weeks.** Index a new client codebase once. Run `codeindex query "authentication flow"` and get the execution paths, the symbols involved, the files. Ask your AI assistant about the codebase with full graph context. Go from "I've never seen this code" to "I understand how it works" in a morning, not a sprint.

2. **Client data isolation, by design.** Each codebase is stored in its own named project under `~/.codeindex/ClientName/`. Switching clients is switching directories. There is no shared cloud store that could accidentally surface client A's symbols when working on client B. This is an audit trail argument you can make to a privacy-conscious client.

3. **No recurring cost, no per-seat, no contract.** You bill by the hour. Your tools should not invoice you by the month per project. CodeIndex is a local install. Add as many client projects as you have disk space for.

**Tone for this persona:** Practical, ROI-framed, time-conscious. The ZZP'er thinks in billable hours. Frame everything as time saved or risk reduced. They are their own procurement department — so price/value clarity matters more than enterprise compliance language.

**Dutch cultural note:** The ZZP economy in the Netherlands is large (1.2M+ ZZP'ers) and technically sophisticated. Many senior ZZP'ers are ex-corporate developers who left to gain autonomy. They respond to tools that respect that autonomy — local, configurable, no lock-in.

---

## 7. Soundbites and Headlines

For use in web copy, pitch decks, event materials, and social.

**Short form (EN):**
- "Your code. Your machine. Your graph."
- "Understand code. Don't just search it."
- "AI that knows how your code actually works."
- "The knowledge graph your documentation never was."
- "Change with confidence. Not with guesswork."

**Short form (NL):**
- "Jouw code. Jouw machine. Jouw graaf."
- "Begrijp code. Zoek er niet alleen in."
- "AI die begrijpt hoe jouw code echt werkt."
- "De kennisgraaf die jouw documentatie nooit was."
- "Wijzigen met vertrouwen. Niet op de gok."

**One-liner for conference badge / social bio (EN):**
> Graph-powered code intelligence. Runs local. Remembers everything.

**One-liner (NL):**
> Grafiek-aangedreven code-intelligentie. Lokaal. Blijft onthouden.

---

## 8. Demo Story Beats

A structured narrative for live demos or recorded walkthroughs. Runs approximately 12 minutes.

**Beat 1 — The problem (2 min)**
Open a real-world legacy codebase. Ask: "Where is the authentication logic?" Do a full-text search. Show 47 results across 23 files. "This is what every tool gives you. Now let me show you something different."

**Beat 2 — Index and query (3 min)**
`codeindex analyze`. Show the graph being built. `codeindex query "authentication flow"`. Show the execution flows: `LoginFlow → validateUser → checkToken → getUserById`. One query. The full path. "The graph knows how it fits together."

**Beat 3 — Change safety (3 min)**
`codeindex impact validateUser`. Show the depth-ranked output: loginHandler and apiMiddleware at d=1 (WILL BREAK), authRouter at d=2 (LIKELY AFFECTED). "Before I touch this function, I know exactly what I'm risking. Not from documentation. From the graph."

**Beat 4 — Memory (2 min)**
`codeindex remember "validateUser uses a non-standard token format — see RFC-4892-internal for the spec. Do not change the token structure without updating the mobile client."`. One week later, new session. AI assistant is asked about validateUser. It retrieves the observation. "It remembered. Your AI assistant now knows what you knew."

**Beat 5 — The privacy close (2 min)**
Point to the terminal. `ls ~/.codeindex/`. Show the directory. "Everything you just saw is right here. It did not go anywhere. No API call to a cloud provider. No account. No data processing agreement to sign. Just your code, on your machine, understood."

---

## 9. Narrative Brief for Content and Creative Teams

**The overarching story:**

Software is accumulation. Every year a codebase grows more complex than any single person can hold in their head. The tools we use to manage this complexity — documentation (always behind), search (finds text, not meaning), AI assistants (stateless, cloud-dependent, generating without understanding) — are not equal to the problem.

CodeIndex starts from a different premise: that a codebase is a graph, not a collection of files. Every relationship between symbols is a fact. Every execution path is a story. Every decision a developer made is a memory worth keeping. The job is not to generate more code. The job is to make the code that already exists legible — to the developer who inherited it, to the AI assistant augmenting that developer, and to the team that will maintain it long after everyone in the room today has moved on.

For a senior Dutch developer working on a system that predates their tenure, CodeIndex is not an AI toy. It is a professional instrument.

**Tone of voice:**
- Precise and honest. Never claim more than the tool delivers.
- Technically credible. The audience includes people with 30 years of experience; they will notice vague claims.
- Respectful of the audience's skepticism. They have evaluated many tools. Show the mechanism, not just the outcome.
- Dutch-pragmatic: direct, without the American marketing register of "revolutionary" or "game-changing".

**What to avoid:**
- "AI-powered" as a standalone claim — be specific about what the AI does and what the graph does
- Anything that implies the tool writes code for you — it does not, and the persona does not want that
- Superlatives without proof points
- American SaaS register ("unlock your potential", "supercharge your workflow")

---

## 10. Enablement Packet Checklist

Items to produce from this framework:

- [ ] Homepage hero copy (EN + NL variants)
- [ ] Feature page: Privacy & local-first (AVG angle)
- [ ] Feature page: Knowledge graph vs. text search
- [ ] Feature page: Memory system
- [ ] One-page PDF for Dutch enterprise/overheid procurement conversations
- [ ] ZZP'er landing page (Dutch, ROI-framed)
- [ ] Objection handling card for sales/partner conversations (printable)
- [ ] Demo script (based on story beats above)
- [ ] LinkedIn post series: 5 posts, one per proof point (NL language)
- [ ] Conference talk abstract for Dutch developer events (DevDays, JFall, Devoxx BE)
- [ ] Pitch deck: 8 slides, investor/partner version

---

*Framework prepared by: Product Narrative Lead*
*Based on: Direct codebase analysis of CodeIndex pattaya-v1 workspace*
*Source files reviewed: README.md, codeindex-guide SKILL.md, codeindex-exploring SKILL.md, codeindex-impact-analysis SKILL.md, codeindex-refactoring SKILL.md, memory/types.ts, hybrid-search.ts, framework-detection.ts, wiki/generator.ts*
