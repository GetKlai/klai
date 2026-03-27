# Klai Landing Page — Conversion-Centered Design Analyse

**Avatar:** Eline Vermeer — Compliance & Risk Manager, Dutch private wealth management
**Awareness stage:** Problem Aware
**Business model:** B2B — financial services, legal, healthcare (NL/BE)
**Primary CTA:** Book a demo

---

## 1. Navigation / Header

**CCD Principles:** Focus, Consistency

**Structure:**
```
[Logo: Klai]    Why Klai   Product   Ownership   Start    [CTA: See how it works]
```

**Strategic rationale:**
The nav is deliberately sparse. "Why Klai / Product / Ownership" maps directly to the three decision axes of a compliance buyer: legitimacy, capability, and structural safety. "Ownership" as a nav item is unusual — that's intentional. It signals differentiation before the visitor reads a word of copy. The CTA "See how it works" is low-commitment for a Problem Aware visitor who hasn't yet decided anything needs solving.

**Aandachtspunt:** The copy notes "See how it works" is passive. Consider A/B testing against "See where your data stays" — it activates the anxiety that drives this avatar.

**Visual execution:**
- Sticky, transparent-to-solid on scroll
- Background: `--purple-primary` (#2D1B69) or near-black — authority, not friendliness
- CTA button: `--purple-accent` (#7C6AFF) with shimmer on hover
- No megamenu. Single-level navigation only.

**Magic UI component:** `Shimmer Button` (primary CTA)

---

## 2. Hero — Above the Fold

**CCD Principles:** Focus, Attention, Benefits, Trust

**Layout (Z-pattern):**
```
HEADLINE (left, large)                    HERO VISUAL (right)
SUBHEADLINE (left, medium)
[Primary CTA: Book a demo]
[Secondary: or read how it works →]
─────────────────────────────────────────────────────────────
Used by compliance teams at financial services firms...      (1 regel social proof)
```

**Headline:**
> Stop choosing between staying competitive and staying compliant.

**Strategic rationale:**
This headline works because it names the exact tension Eline lives with — not a solution, not a feature, just the problem she already knows. For a Problem Aware audience this is the correct entry point.

The subheadline does three things in two sentences: (1) names the category cleanly — private AI infrastructure, (2) provides the proof mechanism — hosted in Europe, open source, yours to inspect, (3) resolves the tension from the headline.

**Hero visual:**
Abstract schematic of Europe with contained dataflow — no server racks. A clean SVG diagram that communicates the data does not move is more credible than any stock photo.

**Magic UI components:**
- `Animated Beam` (hero visual — data flow within EU borders)
- `Blur Fade` (headline/subheadline entrance animation)
- `Shimmer Button` (primary CTA)

---

## 3. Value Proposition — Four Blocks

**CCD Principles:** Benefits, Attention, Trust

**Sequencing rationale:**

| Block | Tension addressed | Why this order |
|---|---|---|
| 1. Datalocatie | "Where does this go?" | First question any compliance officer asks |
| 2. Eigenaarschap | "What if they get acquired?" | Second-order risk once data location is settled |
| 3. Black box | "Can I actually verify this?" | The evidence question |
| 4. Compliance by design | "What if something goes wrong?" | Operational reassurance |

**Visual execution:**
- Each block: `--sand-light` (#F5F0E8) / white alternating
- Pull-quote styling for opening hook line of each block
- No icons. No bullet lists. Flowing prose only.
- `Border Beam` on block containers on hover

**Magic UI components:** `Border Beam`, `Blur Fade`

---

## 4. Feature Overview / Demo

**CCD Principles:** Structure, Benefits, Friction Reduction

Toggle: `Use AI` / `Build with AI`

**USE mode:** Chat, Focus, Scribe
**BUILD mode:** Connect, Vault, Shield, Scribe (API), Flow (early access), Voice (early access)

**Magic UI components:** `Bento Grid`, `Animated Beam`

---

## 5. Social Proof & Results

**CCD Principles:** Trust, Consistency

**Metric strip (4 numbers):**
- 100% data processed on EU servers
- 0 third-country data transfers
- Audit log: 1 day — 10 years configurable
- Sub-processor list: full, public, no request required

**Testimonial ordering:**
1. Head of IT (technical credibility)
2. Compliance Manager (ban lifted — avatar's dream outcome)
3. Privacy Officer (verifiability — ends on the hardest, most important claim)

**Magic UI components:** `Number Ticker`, `Marquee`

---

## 6. Feature Deep Dive — Comparison Table

**CCD Principles:** Trust, Benefits, Friction Reduction

Columns: ChatGPT Enterprise / Azure OpenAI / **Klai**

**Critical rows to visually accent:**
- CLOUD Act exposure
- Can be sold or acquired
- DPA available before contract
- Open source

Below table: `Download the data processing agreement →`

---

## 7. Use Cases / Who It's For

**CCD Principles:** Structure, Focus, Benefits

| Card | Audience | CTA |
|---|---|---|
| Compliance managers | "You said no to every AI tool" | See what's included → |
| IT leads | "One deployment, OpenAI-compatible" | See the technical documentation → |
| Management | "Your team wants AI. Your compliance officer says no." | Book a 30-minute demo → |

**Note:** Management gets the demo CTA — they are the economic buyer.

**Magic UI component:** `Border Beam` (card hover)

---

## 8. Pricing

**CCD Principles:** Friction Reduction, Trust, Focus

Tiers: Team / Business / Enterprise

Below-table trust bar:
> EU data residency · Data processing agreement · Full sub-processor list · Audit logging · Cancel anytime

**Magic UI component:** `Shimmer Button` (recommended tier)

---

## 9. Risk Reversal & Objection Handling

**CCD Principles:** Trust, Friction Reduction

**Objection sequencing:**

| Order | Objection |
|---|---|
| 1 | "Every vendor says GDPR compliant. Why believe Klai?" |
| 2 | "What if Klai gets acquired?" |
| 3 | "Implementation takes 12 months" |
| 4 | "What happens to data if we cancel?" |
| 5 | "We tried banning AI tools. It didn't work." |
| 6 | "Is there a free trial?" |

Layout: accordion, no icons, collapsed by default.

---

## 10. Final CTA

**CCD Principles:** Focus, Friction Reduction, Benefits

Three-option segmentation:
1. I want to use Klai → Start free trial
2. I want to run my own stack → Let's talk
3. I want to talk to someone → Book 30 minutes

"No pitch. No pressure. Thirty minutes, your questions, honest answers." — highest-converting text on the page for this avatar.

**Magic UI component:** `Meteors` (subtle background)

---

## 11. Footer

**CCD Principles:** Trust, Consistency

Footer legal line: `© 2026 Klai — Steward-owned. Not for sale.`

DPA and sub-processor list linked directly from footer — not buried.

---

## Component Summary

| Section | Magic UI Component | Purpose |
|---|---|---|
| Header CTA | Shimmer Button | Draws attention without aggression |
| Hero visual | Animated Beam | EU data flow — contained, directional |
| Hero headline | Blur Fade | Entrance animation |
| Value props | Border Beam | Card hover |
| Metrics | Number Ticker | Quantitative proof, animated on scroll |
| Sector strip | Marquee | Social proof loop |
| Features | Bento Grid | Modular feature layout |
| Pricing CTA | Shimmer Button | Recommended tier |
| Final CTA bg | Meteors | Subtle background motion |
