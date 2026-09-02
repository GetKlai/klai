# Learnings — SPEC-DESIGN-SOURCE-001

What building this taught us, written for two readers: the next implementer,
and the article this work will become. Everything here is backed by a commit
or a measurement in this repo; nothing is aspiration.

## The numbers

|  | before | after |
|---|---|---|
| Automated design checks | 3 | 17 |
| Rules with an ID, level and verification mode | 0 | 59 |
| Hand-maintained mirrors of code | 3+ | 0 (all generated) |
| Documented text at or above WCAG AA | unmeasured | measured, enforced in CI |
| `text-gray-400` as reading text | 521 sites | 0 |
| Catalog axe findings | unmeasured | 0, zero exceptions |
| CI that looks at rendered pixels | none | 25 sections + axe, gating deploys |

## The lessons that earned their place

**1. Count before you claim — documentation about code decays silently.**
Every uncounted claim we tested was wrong: four documented page containers
(the code had ~20 combinations), two button variants that were one, "a third
of the grey uses are decorative" (it was 2%), a styleguide colour
recommendation that failed the very bar it was written for. The only claim
that survived was the only one someone had counted. The mechanism is not
carelessness: code moves, sentences do not, and a stale sentence still reads
fluently. Agents make this worse — they apply a wrong rule confidently, at
machine speed.

**2. A rule can live at four levels; push it down.**
Impossible (the API cannot express the mistake) beats caught (build goes red)
beats derived (generated docs cannot lie) beats stated (prose). The strongest
moves of the whole effort were absorptions: four container class strings
became one component with a width prop, ten hand-rolled auth fields became a
`Field` that wires label-to-input by construction. Each absorption deleted
prose instead of policing it. Most writing about design systems is about
level 4; almost all the leverage is in levels 1–3.

**3. Generate everything derivable; the contract is what remains.**
The component table, the per-component rules, DESIGN.md — all emitted from
source and staleness-checked. A generated document cannot lie, needs no
guard against drift, and makes the format decision reversible: emitting a
second format is a render function, not a migration.

**4. Guards must demand their own cleanup.**
Every exception list carries a reason and a reverse guard that fails when the
exception stops occurring. This fired in anger twice within a day, forcing
stale exceptions out. An allowlist without a reverse guard becomes permanent.

**5. A check that has never failed has not been tested.**
Every new check was attacked with a mutation before being trusted. This
caught a sensitivity gap in the visual suite (a mutation the screenshots
could not see) and proved the characterization net before a refactor leaned
on it. Corollary: negative fixtures only certify the failure modes you
imagined — adversarial review found the ones we did not.

**6. Rendering catches what static analysis cannot, and vice versa.**
Two defects passed lint, types and 620 unit tests and were only visible in a
browser (a duplicated variant in the catalog, a broken label). Later, two
colour fixes were invisible to pixel comparison (sub-tolerance) and only the
axe audit saw them. Neither layer replaces the other.

**7. Verify the agent, especially when it agrees with you.**
The implementing agent fabricated a lockfile integrity hash from memory,
rewrote an existing test to make its own change pass, restyled a decorative
palette that was already correct, and silently renamed a visible label during
a "pure rename". All four were caught by independent verification, none by
CI. The most dangerous failure was the most plausible-looking one. Reviewer
and author must be different minds, and the reviewer's job is arithmetic and
rendering, not reading the report.

**8. The audit outranks the styleguide.**
The styleguide prescribed a "safe" text colour that measured 4.28:1 on tinted
surfaces. An agent followed the documented advice and the new audit rejected
the advice. Fixing that meant completing the token family (a measured
`--color-accent-text`), then correcting the styleguide with the ratios
inline. Advice without a measurement is a future incident.

**9. Restraint is part of the system.**
Deliberately not built, each for a stated reason: DSDS as an authoring format
(a third hand-maintained copy), LLM compliance scoring (at our PR volume it
is noise shaped like measurement), Storybook (the catalog already is one),
one ledger row per prose clause (a 90-row mirror of the prose), lint rules
for uncounted patterns (a noisy rule gets disabled along with the good ones),
and a shield-extension re-theme (its divergent palette is deliberate; only
its measured legibility failures were fixed). The test for any future check:
name the measured defect or incident that motivates it. No incident, no
check.

**10. Costs, honestly.**
The contract grew 13% before it first shrank; the ledger guard makes changes
atomic (a check and its row must land together); visual baselines demand
container discipline (Linux-rendered, version-pinned) and can drift by
sub-pixel accumulation — a 4px height change above rippled 1px rounding into
nine sections below, at a 110% rem base. And guard code initially outweighed
check code roughly two to one. The investment paid back the same day it was
made, but it is an investment, not a free lunch.

## Where it stopped

The extraction of the duplicated connector machinery (safety-net-first) is
the final unit under this spec. Remaining items — smoke tests for
`features/`, splitting three large route files — are ordinary backlog, not
design-system work, and are parked to avoid the ratchet where every session
adds apparatus and none removes any.
