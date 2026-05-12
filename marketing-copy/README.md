# Klai launch copy — centraal bewerkbaar

> Alle launch-teksten leven hier als markdown. Jantine, Mark en Steven kunnen ze rechtstreeks op GitHub editen — geen branches, geen review-flow, gewoon "edit + commit".

## Hoe edit je een post

1. Open de file op github.com (link hieronder per post)
2. Klik de pencil ✏️ icoon rechtsboven
3. Edit de markdown
4. Onderaan "Commit changes" — direct naar main
5. Klaar. Iedereen ziet 'm direct, history zit in git.

**Voor mobile:** zelfde stappen, GitHub iOS/Android app werkt prima.

## Files

- [`plan.md`](https://github.com/GetKlai/klai/edit/main/marketing-copy/plan.md) — volledig launch-plan (narratieve arc, voice, brand DNA snapshot, schedule)
- [`posts/`](https://github.com/GetKlai/klai/tree/main/marketing-copy/posts) — 24 social posts, één per file

## Post-files (klik om te editen)

### Week 1 — Plant the flag

- [2026-05-12 LinkedIn launch hero](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-12-linkedin-launch-hero.md)
- [2026-05-12 X launch hero](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-12-x-launch-hero.md)
- [2026-05-13 LinkedIn — Jantine, Why we exist](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-13-linkedin-jantine-why.md)
- [2026-05-14 X — Steward-owned](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-14-x-steward-owned.md)
- [2026-05-15 LinkedIn — EU-only](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-15-linkedin-eu-only.md)
- [2026-05-16 X — Cited sources](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-16-x-cited-sources.md)
- [2026-05-17 LinkedIn — Knowledge base](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-17-linkedin-knowledge-base.md)
- [2026-05-18 X — Connectors](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-18-x-connectors.md)
- [2026-05-19 LinkedIn — Mark, everyday user](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-19-linkedin-mark-everyday.md)

### Week 2 — Comparison

- [2026-05-20 X — vs ChatGPT](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-20-x-vs-chatgpt.md)
- [2026-05-21 LinkedIn — vs Copilot](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-21-linkedin-vs-copilot.md)
- [2026-05-22 X — CLOUD Act](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-22-x-cloud-act.md)
- [2026-05-23 LinkedIn — Open source](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-23-linkedin-open-source.md)
- [2026-05-24 X — Steven, legal said yes](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-24-x-steven-legal.md)

### Week 3 — Social proof + verticals

- [2026-05-26 LinkedIn — Banking](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-26-linkedin-banking.md)
- [2026-05-27 X — Healthcare](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-27-x-healthcare.md)
- [2026-05-28 LinkedIn — Legal](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-28-linkedin-legal.md)
- [2026-05-29 X — Pricing](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-29-x-pricing.md)
- [2026-05-30 LinkedIn — First month](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-05-30-linkedin-first-month.md)

### Week 4 — Closing + cohort 2

- [2026-06-02 X — Self-hosted](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-06-02-x-self-hosted.md)
- [2026-06-03 LinkedIn — Accountancy](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-06-03-linkedin-accountancy.md)
- [2026-06-04 X — First 100 closing](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-06-04-x-first-100-closing.md)
- [2026-06-05 LinkedIn — Cohort 2](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-06-05-linkedin-cohort-2.md)
- [2026-06-08 X — Compliance principle](https://github.com/GetKlai/klai/edit/main/marketing-copy/posts/2026-06-08-x-compliance-principle.md)

## Wat doet de rendered preview op getklai.com dan?

`https://getklai.com/_launch-internal-2026-05-12/index.html` is een **statische snapshot** van de marketing-generator output van 11 mei. De inline-edit-functie in die HTML schrijft naar `localStorage` (per-browser per-apparaat) — dat is **NIET** wat wij gebruiken voor team-edits.

**Workflow voor copy-editing:**

1. Edit `.md` files hier in `marketing-copy/` via GitHub web UI
2. Iedereen ziet edits direct in git
3. Voor het posten op LinkedIn/X: kopieer de **Body** sectie uit de .md
4. Voor visuals: de `posts/<date>/variants/variant_1.png` op getklai.com is gerendered uit de oorspronkelijke generator-output. Wil je nieuwe variant na een copy-edit? Vraag Claude/Jantine de generator opnieuw te draaien.

## Voor de techneuten

- De .md-bestanden zijn een 1-op-1 kopie uit `marketing/campaigns/klai-launch/output/run-2026-05-11T11-49-15/`
- Edits hier laten de bron-bundle onaangeroerd — bedoeld zo (de source-bundle is een snapshot, de markt-copy hier is de levende versie)
- Bij volgende generator-run: edits hier vergelijken met nieuwe bron-output, mergen, opnieuw committen
