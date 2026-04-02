# SPEC-CONFIDENCE-001 Research

**Date:** 2026-04-02
**Sources:**
- Vexa Conductor: https://github.com/Vexa-ai/vexa/tree/feature/agentic-runtime
- LLM instruction-following research (IFScale, Anthropic, academic papers)

---

## 1. Vexa Conductor — Samenvatting

Vexa bouwde een AI development framework ("Conductor") dat Claude Code autonoom laat werken aan een meeting-transcriptieplatform met 15 features en 12 services. Na $50 en 48 uur experimenteren documenteerden ze hun bevindingen in LEARNINGS.md.

### Kernarchitectuur

```
User beschrijft missie → Conductor maakt mission.md + bouwt prompt
    → claude --worktree {name} (dev agent werkt)
        → Stop hook blokkeert vroeg stoppen
        → check-completion.py verifieert DoD
    → claude --worktree {name} (evaluator beoordeelt)
    → User verifieert
```

### Kerncomponenten

| Component | Wat het doet | Vexa bron |
|---|---|---|
| Mission files | 5-regel taakdefinitie (Focus, Problem, Target, Stop-when, Constraint) | `conductor/missions/*.md` |
| Stop Hook | Bash script dat agent blokkeert van stoppen zonder bewijs | `conductor/hooks/confidence-check.sh` |
| Completion checker | Python script dat DoD verifieert tegen state.json | `conductor/check-completion.py` |
| Confidence framework | Bayesiaans model met gotcha memory en adversarial checks | `.claude/confidence-framework.md` |
| Gotcha catalog | Machine-leesbare failure patterns met severity en decay | In `.claude/CLAUDE.md` (G1-G11) |
| Evaluator agent | Skeptische reviewer die claims verifieert | `.claude/agents/evaluator.md` |

## 2. Vexa's Kernlessen — Adoptie-analyse

### Overgenomen voor Klai

| Les | Vexa bewijs | Klai toepassing |
|---|---|---|
| Mechanische enforcement > prompt instructies | Agent negeerde "read-only" regel, backgroundde processen, werkte op main ipv branch | Stop hook die confidence-rapportage afdwingt (REQ-1) |
| "Code looks correct" = 0 confidence | Evaluator ving 4+ bugs die dev agent niet zag | Evidence scoring tabel in confidence.md (REQ-3) |
| Adversarial check bij 80% verlaagt overconfidence 15pp | Kaddour et al. 2026; evaluator ving stale scores, false completion claims | Stop hook adversarial check (REQ-2) |
| Diagnose voor fixing — geen flailing | Agent probeerde 4 fixes zonder root cause; allemaal mislukt | Process rule `diagnose-before-fixing` (REQ-4) |
| System health verificatie | 8/8 Playwright tests passed, dashboard unreachable | Entry point verificatie in evidence tabel (REQ-3) |
| Gotcha memory als persistent geheugen | Severity + confirmation tracking maakt systeem slimmer over tijd | Geïntegreerd in process-rules via severity_map (REQ-4) |
| Vertel WHAT niet HOW | Micro-management regels killden werkende processen | Geen prescriptieve timing/polling regels |
| Search ALL case variants | camelCase missed in 107-file grep | Process rule `search-all-case-variants` (REQ-4) |
| Convention changes = unbounded blast radius | 14 stale refs in 9 unplanned files | Process rule `convention-change-blast-radius` (REQ-4) |

### Niet overgenomen voor Klai

| Les | Vexa aanpak | Waarom niet |
|---|---|---|
| Bayesiaans confidence model | Log-odds sigmoid met evidence_strength | Vexa checkt in praktijk alleen "is er een getal + is er bewijs" — de wiskunde wordt niet gerund. Onze binaire stop hook doet hetzelfde, eenvoudiger. |
| Conductor als orchestrator | Mission files → worktree → dev+validator team → evaluator | Wij hebben MoAI met SPEC-workflow. Zelfde concept, andere implementatie. |
| README als source of truth | Design/State split in feature READMEs | Vexa's eigen conclusie: "agent leest code, niet README." Ons SPEC-systeem werkt beter. |
| Multi-agent teams met coordinator | Dev + validator + coordinator | Vexa's conclusie: "coordinator deed niets nuttigs, 15+ ronden shutdown-onderhandeling." |
| Weighted DoD met ceiling mechanic | Risk-gewogen items, kritiek pad kapt totaalscore | Goed concept, te complex voor huidige schaal. Kan later als fase 2. |
| Evaluator als apart agent | Skeptische reviewer na elke delivery | Onze manager-quality agent doet dit al. |
| Gotchas als apart bestand | `.claude/CLAUDE.md` sectie met 11 gotchas | Onze pitfalls zijn al AI-fouten. Gotcha-entries geïntegreerd in process-rules (REQ-4). Eén systeem, niet twee. |

## 3. Vexa Gotchas — Mapping naar Klai

| Vexa ID | Patroon | Klai-bestemming |
|---|---|---|
| G1 | Test the system, not just the feature | → process rule `verify-system-not-just-feature` |
| G2 | CLAUDE.md changes don't reach running sessions | → known limitation, geen actie nodig |
| G3 | Don't flail — diagnose before fixing | → process rule `diagnose-before-fixing` |
| G4 | Instructions alone don't change behavior | → opgelost door stop hook (REQ-1) |
| G5 | Relative paths in hooks break from subdirectories | → meegenomen in hook implementatie |
| G6 | Search ALL case variants when renaming | → process rule `search-all-case-variants` |
| G7 | Convention changes need full-codebase consumer search | → process rule `convention-change-blast-radius` |
| G8 | Confidence must reflect the critical path | → evidence tabel in confidence.md |

---

## 4. LLM Instruction-Following Research

### 4.1 IFScale — Hoeveel instructies kan een LLM volgen?

**Paper:** Jaroslawicz et al., "IFScale: How Many Instructions Can LLMs Follow at Once?", Feb 2026
**URL:** https://arxiv.org/abs/2507.11538

| Model | 10 instructies | 500 instructies | Patroon |
|---|---|---|---|
| Claude Opus 4 | 100% | 44.6% | Lineair verval |
| Claude Sonnet 4 | 100% | 42.9% | Lineair verval |
| Claude Haiku 3.5 | 98% | 8.5% | Exponentieel verval |

**Bevindingen relevant voor deze SPEC:**
- Bij hoge instructie-dichtheid: modellen maken **omission errors** (regels compleet overslaan), niet modification errors. Ratio: 34.88:1.
- Primacy-effecten pieken rond 150-200 instructies, daarna verdwijnen ze
- Elke extra instructie kost lineair compliance — minder regels = hogere compliance per regel

**Implicatie:** process-rules.md moet zo min mogelijk entries hebben. 21 entries is ruim onder de degradatiegrens, maar elke onnodige entry kost compliance op de andere entries.

### 4.2 Anthropic — Officiele richtlijnen

**Bron:** https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

> "Aim to provide the minimal set of information that fully describes the expected behavior."

> "Would removing this cause Claude to make mistakes? If not, cut it."

**Compliance-plafond:** ~70% voor prompt-gebaseerde regels. Hooks = ~100%.

**Positie-effect:** Instructies aan het einde van de context worden tot 30% beter gevolgd (recency bias). Instructies in het midden krijgen minste aandacht ("lost in the middle").

### 4.3 Pink Elephant — Negatie is architecturaal moeilijk

**Paper:** "Do not think about pink elephant!", arXiv 2024
**URL:** https://arxiv.org/html/2404.15154

Transformers hebben architecturaal moeite met negatie. "Don't use markdown" activeert het concept "markdown" in de attention weights. Het model moet dan actief onderdrukken wat het net geactiveerd heeft.

**Implicatie:** Process rules positief framen. "Read logs first, form one hypothesis" in plaats van "Don't try random fixes."

**Anthropic bevestigt:** "Tell Claude what to do instead of what not to do."

### 4.4 SysBench — Systeeminstructie compliance

**Paper:** "Can Large Language Models Follow System Messages?", ICLR 2025
**URL:** https://arxiv.org/abs/2408.10943

Wanneer gebruikersinstructies conflicteren met systeeminstructies, presteren alle modellen significant slechter. Multi-turn conversaties verslechteren de compliance verder.

**Implicatie:** De stop hook (REQ-1) is essentieel voor de regels die 100% compliance nodig hebben. Prompt-instructies worden onder taakdruk behandeld als suggesties.

### 4.5 Format — Wat werkt voor Claude

| Format | Effectiviteit | Bron |
|---|---|---|
| Markdown met headers | Hoog | Anthropic officiele docs |
| XML-tags voor data/instructie scheiding | Hoog | Anthropic officiele docs |
| Compacte tabellen | Goed als referentie, slecht als instructie | IFScale + praktijkervaring |
| YAML/JSON | Goed voor gestructureerde data | CFPO paper (2025) |
| Severity labels (HIGH/CRIT) | Minimale impact op gedrag | Geen evidence dat modellen ernst-labels wegen |

### 4.6 Optimale detail per regel

| Tokens/regel | Effect |
|---|---|
| ~20 (compacte tabel) | Te vaag — vormt onvoldoende associaties voor activatie |
| ~50-100 (2-3 zinnen) | Sweet spot — concreet genoeg om te activeren, compact genoeg |
| ~150-200 (volledige gotcha) | Marginaal beter activatie, maar verdringt andere instructies |
| ~500+ (documentatie) | Slechter — context-vervuiling |

**Goldilocks zone:** 2-3 zinnen per regel met de trigger embedded in de beschrijving.

---

## 5. Academische Referenties

### Confidence & Calibratie

| Paper | Bevinding | URL |
|---|---|---|
| Kaddour et al., Feb 2026 | Agents 5.5x vaker confident-fout dan onzeker-over-iets-goeds | https://arxiv.org/abs/2602.06948 |
| Dunning-Kruger in LLMs, March 2026 | Slechtst presterende modellen = hoogste confidence | https://arxiv.org/html/2603.09985v1 |
| Adversarial framing | "Welke bugs?" ipv "is dit correct?" verlaagt overconfidence ~15pp | Kaddour et al. 2026 |
| Reflexion (Shinn et al., NeurIPS 2023) | Verbale zelfreflectie in episodisch geheugen verbetert prestatie | https://arxiv.org/abs/2303.11366 |

### Instruction Following

| Paper | Bevinding | URL |
|---|---|---|
| IFScale (Jaroslawicz et al., 2026) | Lineair compliance-verval; omission >> modification errors | https://arxiv.org/abs/2507.11538 |
| SysBench (ICLR 2025) | User-system conflict verslechtert compliance significant | https://arxiv.org/abs/2408.10943 |
| Pink Elephant (2024) | Negatie activeert het te vermijden concept in attention | https://arxiv.org/html/2404.15154 |
| CFPO (2025) | Format-keuze kan prestatie tot 40% beïnvloeden | https://arxiv.org/html/2502.04295v3 |
| Does Prompt Formatting Matter? (He et al., 2024) | Ja, tot 40% verschil bij zelfde inhoud | https://arxiv.org/abs/2411.10541 |

---

## 6. Bronbestanden Vexa (GitHub)

Alle bestanden op branch `feature/agentic-runtime` van `Vexa-ai/vexa`:

| Bestand | URL |
|---|---|
| LEARNINGS.md | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/conductor/LEARNINGS.md |
| Confidence Framework | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/.claude/confidence-framework.md |
| CLAUDE.md (gotchas) | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/.claude/CLAUDE.md |
| Evaluator agent | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/.claude/agents/evaluator.md |
| Stop hook (confidence) | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/conductor/hooks/confidence-check.sh |
| Stop hook (mission) | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/conductor/hooks/mission-check.sh |
| Completion checker | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/conductor/check-completion.py |
| Settings (hook config) | https://github.com/Vexa-ai/vexa/blob/feature/agentic-runtime/.claude/settings.json |
