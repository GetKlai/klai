---
id: SPEC-PRIVACY-PII-POLICY-ADMIN-001
version: "0.7.0"
status: draft
created: 2026-08-21
updated: 2026-08-21
author: Mark Vletter
priority: high
related:
  - SPEC-PRIVACY-MISTRAL-PII-001 (owns the detection/masking pipeline this SPEC configures; its RETURN_SET/NEVER_RESTORE split is the contract)
  - SPEC-PLATFORM-ADMIN-001 (owns the Klai-staff console this SPEC extends — do not build a second one)
  - SPEC-PRIVACY-QUERY-SHADOW-001 (telemetry_level is the closest per-org setting; its DB→service→endpoint→hook→tab chain is the template)
  - SPEC-SEC-CORS-001 (portal admin endpoint conventions)
roadmap: docs/architecture/knowledge-rag-improvement-plan.md
---

# HISTORY

| Version | Date       | Author       | Change |
|---------|------------|--------------|--------|
| 0.7.0   | 2026-08-21 | Mark Vletter | Adds D8 on end-user disclosure, which contradicts the obvious design and says why. An icon with hover text is what the evidence argues against: SOUPS 2021 (n=683) found plain text beat every alternative and icons had a **negative** effect on perceived security versus no icon; browser padlock comprehension measures 5-11%. Worse for our intent, telling people they are protected is documented to make them share **more** sensitive data — which would work against the data minimisation this SPEC exists for. Salesforce's Einstein Trust Layer, architecturally identical (mask before the model, unmask after), shows the end user nothing. So: no icon, no per-message badge; one line of plain text once per session, naming what happens rather than reassuring, linking once to the help centre; kept separate from the AI Act Art. 50 disclosure. Records the thing this does not fix — a user whose answer is subtly worse still has no way to know why — and names the conditional, answer-attached signal that would, as its own decision rather than smuggling it in. |
| 0.6.0   | 2026-08-21 | Mark Vletter | Product answers folded in. Rights: `ProfileRole.ADMIN` confirmed, no new `Capability`. No confirmation dialog on toggling — it is a settings page and a modal per switch trains people to dismiss modals. Adds **D6**: the UI groups entities (contact, financial, company, location, plus a locked always-on row) instead of listing them, because seven toggles becomes twenty once BE/DE land and nobody reasons in `NL_BTW` versus `NL_KVK`. Storage stays per entity — grouping is presentational, and baking it into the model would make regrouping a migration and lose the mixed state. A collapsed per-entity view stays for the real-but-rare cases a group cannot express. Adds **D7**: the allow-list exists at both levels, unioned, with the platform list audited as heavily as a version publish and shown as inherited in the tenant UI — otherwise a tenant files a detection bug that is actually a platform exclusion. |
| 0.5.0   | 2026-08-21 | Mark Vletter | REQ-13 reframed. 0.4.0 wrote the limitations defensively, which implies the platform needed this feature to be compliant — untrue, and it sells the existing position short. Klai's GDPR position rests on EU-only processing, the DPA, telemetry modes and retention; this is **voluntary data minimisation**, done because it is decent, not because something was missing. The factual points stay — names are not detected, only postcode and city, the context around a masked value remains, none of it touches what Klai stores — because an admin choosing what to enable needs to know where detection ends. Two hard rules survive the reframe for non-tone reasons: "anonymous" is factually wrong for pseudonymised data (Art. 4(5)) in any register, and the page must not claim a toggle makes anyone compliant — not because compliance is in doubt, but because that is not what compliance rests on, in either direction. |
| 0.4.0   | 2026-08-21 | Mark Vletter | Adds REQ-13: the settings page must state what the system cannot do, as specific required copy rather than an instruction to be honest. Eight named limitations, including the three most likely to be misread — names are not detected at all, context around a masked value is not masked, and none of this touches what Klai stores. Forbids "anonymous", "removed", "safe" and "GDPR-compliant" in describing the result, because each overstates it and an admin who concludes their tenant is now compliant has been misled by us. Same copy for tenants and for Klai staff: there is no version that is honest for one audience and not the other. |
| 0.3.0   | 2026-08-21 | Mark Vletter | **Country dropped as a policy axis.** D1 keyed platform defaults on country; Voys is the counter-example — one tenant across several countries, no single country to key on, and `portal_orgs` has no `country` column because the question has no answer. Every recogniser now runs for every tenant: a checksum-anchored recogniser that finds nothing costs nothing, so scoping bought complexity and no accuracy. Country survives as a UI grouping label only. Per-tenant configuration becomes **subtractive**: start from the platform default and exclude — an entity type, a specific value, a pattern, or a keyword. Presidio supports this natively via `allow_list` / `allow_list_match`, so it is plumbing rather than new detection machinery. This also answers D5's homonym problem better than a global exclusion list could: a tenant called *Best Solutions* excludes `Best` themselves. Adds the concrete schema and resolution order REQ-3 was missing, and settles REQ-5's open question — tenants do not pin a version; versioning buys audit and rollback, not per-tenant divergence. |
| 0.2.0   | 2026-08-21 | Mark Vletter | Four product decisions taken. **Names stay off** for now, but a per-country export of common names is available — recorded in REQ-8 as a *secondary* signal raising confidence on a candidate, never as a hard list, and as a possible cheaper alternative to a 500-750 MB NER model. **Email stays on.** **IBAN is default-on**, and the argument I had made against it is withdrawn in D2: IBAN is in the return set, so the agent sees it in their own message and in the restored answer — only Mistral does not, so there is no workflow cost. **Street-level address is dropped** in favour of postcode plus city (new D5): both are closed format or closed vocabulary, which keeps every entity in the same class and avoids matching street names, the one thing the research argued hardest against. `NL_CITY` is a deny-list recogniser with a case-sensitivity requirement and a homonym gate — `Best`, `Ede`, `Nes` are municipalities and ordinary words. |
| 0.1.0   | 2026-08-21 | Mark Vletter | Initial draft. Written after three research passes (competitive config-UX, Dutch address/name detection, codebase admin surfaces) against a request for: a generic default-on entity set for all customers, names and addresses added, a configurable frontend at both platform and tenant level, per language and per country. Two of those four turn out to be blocked on evidence rather than engineering, and this SPEC says which. |

---

# SPEC-PRIVACY-PII-POLICY-ADMIN-001: Configurable PII policy, platform and tenant

## Summary

`SPEC-PRIVACY-MISTRAL-PII-001` masks PII before it reaches Mistral and restores part of it
in the response. Which entity types a tenant gets is a `text[]` column with **no write path
and no UI** — set by an operator with SQL. This SPEC gives it a policy model and two admin
surfaces: Klai staff setting defaults, tenant admins overriding within them.

**Why this exists.** Not to close a compliance gap — Klai's GDPR position rests on EU-only
processing, the DPA, the telemetry modes and the retention limits, and stands without this.
This is voluntary **data minimisation**: sending a model provider less than we are entitled
to send. That intent should shape the work, because it changes what "good" looks like — an
over-eager detector that degrades answers is a worse outcome here than a conservative one
that catches less, which would not be true if this were a compliance control.

It also answers, with evidence rather than preference, the two questions the request raised
that are not really UI questions: whether person names and street addresses can be turned on
by default, and whether policy should be scoped per country at all — it should not, and Voys
is the reason.

## What exists today

Established by inventory, not assumption. Every claim is `file:line`-anchored in Sources.

| | State |
|---|---|
| Detection | 9 entity types live. `NEVER_RESTORE = {SECRET, NL_BSN}` unconditional; `RETURN_SET` = 7 types, per-org, default **off** |
| Storage | `portal_orgs.pii_masked_entities text[]`, CHECK-constrained to the return set |
| Write path | **Does not exist.** Operator SQL only |
| Validation | `validate_entity_selection()` exists and is unwired. Its own docstring says every future write path must call it |
| Platform admin console | **Exists** (`SPEC-PLATFORM-ADMIN-001`): `require_platform_admin()`, `/admin/platform/*`, cross-tenant writes in `platform_manage.py` |
| Write-endpoint template | `PATCH /api/admin/orgs/{slug}/platform-unlocks` — slug-scoped, staff-gated, validates against a known key set, replaces the full array, audit-logged |
| Two-level UI template | `-ExtensionsSettingsSection.tsx` — one component, `is_platform_admin` branches editable-checkbox vs read-only badge |
| Platform-wide settings store | **Does not exist.** Everything is per-org. This is the one genuinely new data layer |
| `PERSON` detector | **Does not exist.** spaCy disabled, no NER model loaded, analyzer holds no model at all |
| Address detector | **Does not exist** |

So: the tenant half is plumbing into an existing chain. The platform-default half is new
schema. The two new entity types are new detection work with real evidence gaps.

## Motivation

### What was asked

A generic set on by default for every customer — phone, email, IBAN, KvK, BTW, postcode,
address — plus names. A frontend to configure it, with room for customer-defined entries.
Two levels: Klai staff setting platform-wide values per language and per country, and a
tenant-level surface with its own admin rights.

### Three findings that change the answer

**1. "Names on by default" is not currently possible, and the evidence to justify it does
not exist.** No PERSON detector is deployed. Adding one is not a toggle: the analyzer
currently loads **no model at all**, and a Dutch NER model is a net-new memory floor of
roughly 500–750 MB on a container capped at 512 MB today.

Worse, the accuracy case cannot be made from published data. **No benchmark anywhere reports
PERSON precision/recall for Dutch specifically.** The closest figures are spaCy
`nl_core_news_lg` at P 0.785 / R 0.750 — but that is *aggregate across all entity types*,
scored on Wikipedia-derived silver-standard annotations, and its own model card warns it
performs inconsistently on genres like social media text. Klai's input is meeting transcripts
and chat. GLiNER2-PII (Apache-2.0, Dutch among its 7 training languages) self-reports average
F1 0.471 with precision as low as 0.35 on some document types — heavy over-detection in the
vendor's own benchmark.

**2. My own reversibility argument has a catch I had not accounted for.** I have argued that
under restore, a false positive round-trips invisibly, so precision stops being binding. The
literature agrees on the privacy half and contradicts the quality half: if `Bakker` is masked
inside *"de bakker leverde het brood te laat"*, the model sees a placeholder where an ordinary
word belongs. The user gets their text back intact, but **the answer is quietly worse**, and
nothing alarms. A leaked BSN is loud; a degraded sentence about bread is not.

That does not kill the argument — it bounds it. Precision is non-binding for *privacy* and
still binding for *answer quality*, and the second is unmeasured.

**3. Street address should not be detected by matching street names.** Presidio has no
first-party address recogniser and never shipped a rule-based one — a revealed preference,
given how readily "capitalised word + number" collides with invoice numbers, room references
and product codes. Google's own `STREET_ADDRESS` carries the warning *"Not recommended for use
during latency sensitive operations."*

The Dutch language offers a better anchor than any other country gets: **postcode plus house
number is a unique key into the BAG**, nationwide, and the postcode format is closed and
checkable — the same class of anchor as the elfproef and mod-97 already in production. The
street name becomes low-confidence context, not the detection decision.

### And one thing that is cheaper than expected

The platform admin console already exists, the write-endpoint shape already exists, the
validation function already exists and is unwired, and the two-level UI pattern already
exists in `-ExtensionsSettingsSection.tsx`. The configurable-policy half of this request is
mostly wiring, not invention.

## Decisions

### D1 — Detection is country-agnostic; tenants subtract, they do not scope

An earlier draft made country the first axis of policy: platform defaults per country, per
language. **That was wrong, and Voys is the counter-example** — one tenant operating across
several countries. There is no single country to key their policy on, and
`portal_orgs` has no `country` column precisely because the question has no answer.

So the country axis is dropped entirely:

**Every recogniser runs for every tenant.** A BSN is a BSN wherever the organisation
operates. A tenant that never sees Belgian numbers simply never triggers the Belgian
recogniser — there is no cost to it being enabled, because every entity in the pack is
checksum- or format-anchored and a recogniser that finds nothing costs nothing.

Country survives only as a **grouping label in the UI**, so an admin reading a list of
entity types can tell that `NL_BSN` is a Dutch identifier. It is documentation, not scoping.

Language survives as a real axis, but for exactly one thing: `PERSON` needs a language model.
Nothing else does — which is what made the pipeline's REQ-2 language-agnostic in the first
place.

**Per-tenant configuration is subtractive.** A tenant does not compose a policy from
scratch; they start from the platform default and take things away:

| Exclusion | Example | Mechanism |
|---|---|---|
| An entity type | "we do not want phone numbers masked" | Remove from the enabled set, subject to the floor (D4) |
| A specific value | "our own company IBAN is not a customer's IBAN" | Allow-list, exact match |
| A pattern | "our ticket numbers look like BSNs" | Allow-list, regex match |
| A keyword | "`Best` is our product name, not a city" | Allow-list, exact match |

This is also the answer to the homonym problem D5 raises: a tenant called *Best Solutions*
excludes `Best` themselves, instead of Klai trying to guess a global exclusion list that is
right for everyone.

**Presidio supports this natively.** `AnalyzerEngine.analyze()` takes `allow_list` and
`allow_list_match` (`"exact"` or `"regex"`), so allowed values are dropped before results are
returned. No new detection machinery is needed — this is plumbing a per-tenant list into an
existing parameter.

### D2 — Default-on, but not for everything

Splitting the request by what the evidence supports:

| Set | Default | Reasoning |
|---|---|---|
| `SECRET`, `NL_BSN` | **On, unconditional, not configurable** | Unchanged. A credential must not reach a provider; a BSN is not Klai's to process without a statutory basis |
| `EMAIL_ADDRESS`, `PHONE_NUMBER`, `IBAN_CODE`, `CREDIT_CARD`, `NL_KVK`, `NL_BTW`, `NL_POSTCODE` | **On by default**, tenant may disable | All checksum/format-anchored, all restored, so a false positive round-trips |
| `NL_CITY` | **On by default** once REQ-6 ships | Closed vocabulary (deny-list), restored. See REQ-6 for the homonym problem |
| `NL_ADDRESS` (street-level) | **Not built** | Replaced by postcode + city — see D5 |
| `PERSON` | **Off, and not selectable**, until REQ-8 is satisfied | No detector deployed; no Dutch accuracy data; unmeasured answer-quality cost |

**On `IBAN_CODE` specifically.** Earlier in the design I argued *against* default-on using
the example of a support agent asking "klopt IBAN NL91 ABNA 0417 1643 00?". That argument was
weak and is withdrawn: `IBAN_CODE` is in the **return set**, so the agent still sees the IBAN
in their own message and again in the restored answer — only Mistral does not. There is no
workflow cost, and the inverse is real: a platform that cannot keep bank details away from a
model provider is not usable for the work Klai is sold for.

Flipping the six to default-on is the single most user-visible change here, and it inverts
the pipeline SPEC's REQ-7 default. That is deliberate and it is the request. Two documented
2026 studies (arXiv 2508.05545, 2509.14464) measure LLM redactors over-redacting — *"all
models redacted much more content than necessary"* — so REQ-7's rollout is staged rather than
flipped for everyone at once.

### D3 — Policy is a versioned object, not a mutable row

Copied from AWS Bedrock Guardrails, which is the cleanest model found: a mutable **draft**,
snapshotted into immutable numbered **versions**, with consumers pinned to a version. Rollback
is repointing, not a migration, and "what changed since v3" is answerable.

The alternative — mutate the array in place, keep an audit log — cannot answer "what was
Voys actually running last Tuesday", which is exactly the question a privacy incident asks.

### D4 — Inheritance is two-tier with an explicit platform floor

Modelled on Tonic Textual's `generator_default` + per-entity `generator_config`, and Google
DLP's org-level `storedInfoType` referenced by project templates.

- **Platform default** (Klai staff): the value a tenant gets with no override. Not scoped by
  country (D1); scoped by language only for `PERSON`.
- **Platform floor** (Klai staff): entity types a tenant **may not** disable. `SECRET` and
  `NL_BSN` are in the floor by construction, not by configuration.
- **Tenant override**: within what the floor allows.

The open question no vendor answers — may a controller weaken a processor's default, and who
carries the risk — is resolved here by making the floor exist at all, and by keeping the
never-restore set structurally outside tenant reach rather than merely unchecked.

### D5 — postcode and city, not street addresses

Street-level detection is dropped. Instead the address signal is carried by two entities that
are both closed-vocabulary or closed-format:

- **`NL_POSTCODE`** — already live, format-anchored, default-on per D2.
- **`NL_CITY`** — new, a **deny-list recogniser** over the Dutch place-name list. Presidio
  supports this natively (`PatternRecognizer` with `deny_list`, compiled to a word-boundary
  regex), so it needs no model and no external call.

This keeps every address-adjacent entity in the same class as the rest of the pack — closed
format or closed vocabulary — and avoids the one thing the research argued hardest against:
matching street names, which are an open set of capitalised noun phrases and collide with
invoice numbers, room references and product codes.

Postcode plus city is also, in practice, most of the privacy value: it locates a person to a
town, which is the identifying part. The house number without a street name is not.

**The honest cost.** A Dutch place-name list contains entries that are ordinary words:
`Best`, `Ede`, `Nes`, `Hem`, `Ee`, `Oss`, `Bunde`. A naive deny-list masks *"dat is de best
mogelijke oplossing"*. REQ-6 handles this with case sensitivity plus a curated exclusion list,
and — like every other new entity here — makes the measurement the gate rather than a
follow-up.

### D6 — group in the UI, store per entity

Per-entity toggles do not scale as an interface. Today there are seven configurable types;
with `NL_CITY` and a BE/DE pack that becomes fifteen or twenty, and no admin thinks in
"`NL_BTW` versus `NL_KVK`".

**THE UI SHALL** present **groups**, not entities:

| Group | Contains | Default |
|---|---|---|
| Contact details | `EMAIL_ADDRESS`, `PHONE_NUMBER` | On |
| Financial | `IBAN_CODE`, `CREDIT_CARD` | On |
| Company identifiers | `NL_KVK`, `NL_BTW` (+ future BE/DE equivalents) | On |
| Location | `NL_POSTCODE`, `NL_CITY` | On |
| Always on | `SECRET`, `NL_BSN` | Locked, with the reason |

Four switches instead of seven, and — the property that matters — **four instead of twenty**
once more countries land. New entity types join an existing group rather than adding a row.

**THE STORAGE SHALL** stay per entity. `pii_masked_entities` already holds individual types
and **SHALL NOT** be changed to hold group names. Grouping is presentational: baking it into
the data model would turn "move `NL_BTW` into another group" into a migration, and would lose
the ability to express a mixed state.

**THE UI SHALL** offer a collapsed per-entity view for the cases groups cannot express. Those
are real but rare — a debt-collection tenant may need `IBAN_CODE` visible to the model while
`CREDIT_CARD` stays masked, and both are "financial". A group whose entities disagree renders
as indeterminate rather than silently rounding to on or off.

**No confirmation dialog** on toggling. It is a settings page, the change is reversible, and a
modal on every switch trains people to dismiss modals.

### D7 — exclusions exist at both levels

The allow-list mirrors the entity policy: a platform list Klai maintains for everyone, and a
tenant list the customer maintains for themselves. **THE effective allow-list SHALL** be the
**union** of the two.

| Level | Who maintains it | For |
|---|---|---|
| Platform | Klai staff | Cases that are wrong for everyone — documentation IBANs, the `Best`/`Ede`/`Nes` homonyms from D5, well-known test numbers |
| Tenant | Customer admin | Their own values — the company's own IBAN, ticket formats that look like BSNs, a product name that is also a place |

A platform entry stops masking for **every tenant at once**, which makes it the most powerful
control in this SPEC and the easiest to get wrong. **IT SHALL** therefore be audited with the
same weight as a policy version publish, and **SHALL** be visible in the tenant UI as
inherited — a tenant admin who cannot see why something is not being masked will file a bug
against detection that is actually a platform exclusion.

### D8 — no icon, no per-message badge; one line of text and a link

The obvious design — a small icon on the chat input, hover text saying pseudonymisation is
on, linking to the help centre — is the one the evidence argues against. Two independent
lines, converging:

**Icons underperform and can backfire.** Stransky et al., SOUPS 2021 (five studies, n=683):
a plain sentence outperformed every alternative phrasing, and icons — envelope, shield,
lock — had a **negative** effect on perceived security compared to no icon at all, strongest
among technical users. Padlock comprehension in browsers measures at 5-11%; Chrome removed it
as a positive trust signal for exactly this reason.

**Telling people they are protected can make them share more.** Documented for encrypted chat
and repeated in 2025 criticism of AI safety classifiers. For Klai this maps directly and
badly: a support agent who reads "personal data is protected" may paste *more* customer detail
into a chat, not less. An ambient reassurance would work against the data-minimisation intent
of this whole SPEC.

And the closest architectural precedent shows nothing at all: **Salesforce's Einstein Trust
Layer masks before the model call and unmasks the response — the same shape as ours — with
zero end-user indication.** Not proof it is right, but evidence that a compliance-sensitive
vendor weighed this exact trade and chose silence.

**THE product SHALL** therefore:

1. **Not** place a persistent icon or badge in the chat UI for masking.
2. Show **one line of plain text, once per session**, in the register the research supports —
   naming what happens, not reassuring: *"Klai vervangt persoonsgegevens zoals telefoonnummers
   en e-mailadressen voordat je vraag naar het AI-model gaat, en zet ze daarna terug."*
3. Link that line **once** to a help-centre article. That is the WhatsApp "tap to learn more"
   pattern: cheap for the majority, sufficient for the minority who want detail.
4. **Not** merge this with the AI Act Art. 50 "you are talking to an AI" disclosure. They are
   different signals with different obligations, and bundling them dilutes both.

**The real problem this does not solve, stated rather than hidden.** The failure mode that
actually costs a user something is not "I did not know masking existed" — it is "this answer
is slightly off and I have no idea why". An ambient indicator does nothing for that. The
useful signal is **conditional and attached to the answer**: when masking measurably shaped a
response, say so on that response. That needs the enforcement side to report which entity
types it masked per request, which it already does for telemetry — so the data exists, and
this is deliberately left as its own decision rather than smuggled in here.

## Scope

### In scope

- Policy data model: platform defaults, platform floor, tenant override, per-tenant
  allow-list, versioning.
- Tenant admin UI on the existing `privacy` tab of `/admin/settings`.
- Platform admin UI under the existing `/admin/platform` console.
- Write endpoints for both levels, reusing `validate_entity_selection()`.
- **Two-level allow-list** (D7): platform-wide and per-tenant, unioned, wired into Presidio's native `allow_list` parameter.
- Custom tenant-defined *detection* entities (regex + word list), with the safety envelope in REQ-9.
- Policy preview against the real pipeline.
- `NL_CITY` deny-list recogniser (D5), gated on REQ-6's homonym measurement.

### Out of scope

- **`PERSON` detection.** It gates on REQ-8 and is its own SPEC once the measurement exists.
  Nothing here ships a NER model.
- **Street-level address detection** (D5). Postcode and city cover it; street names are an
  open set and the research argued against them specifically.
- BAG/PDOK online validation in the request path. The dataset is public domain and the API is
  free, but it has no documented SLA and a history of transient outages — an external
  dependency in the masking hot path is a chat outage waiting to happen.
- BE/DE identifier packs. The taxonomy makes room; the recognisers are separate work.
- Changing the masking/restore mechanics themselves.

## Functional Requirements (EARS)

### Phase 1 — policy model and tenant write path

#### REQ-1 — the tenant write path reuses what exists (ubiquitous)

**THE tenant write endpoint SHALL** follow the shape of
`PATCH /api/admin/orgs/{slug}/platform-unlocks`: full-set replacement, validated against a
known key set, audit-logged, and **SHALL** call `validate_entity_selection()` rather than
reimplementing the domain check.

**IT SHALL** be gated on `get_caller_at_least(ProfileRole.ADMIN)` — tenant admin, the same
bar as `telemetry_level`, which is the closest existing privacy setting. A new `Capability`
is **not** introduced: the codebase has no per-feature RBAC finer than admin for org-wide
settings, and inventing one here would be the only instance.

#### REQ-2 — the DB CHECK stays the last line of defence (ubiquitous)

**THE CHECK constraint SHALL** be widened in step with the return set, and **SHALL** continue
to reject `PERSON`, `SECRET` and `NL_BSN` at the database layer.

Application validation and a DB constraint are not redundant here: the column is currently
operator-writable by SQL, and will remain so for support purposes.

### Phase 2 — platform defaults and the floor

#### REQ-3 — platform defaults are a new, versioned store (ubiquitous)

**THE platform default SHALL** be stored in a new table, because none exists — every setting
in the portal today is per-org, and `platform_org_slug` is an env value, not a row.

**IT SHALL NOT** be keyed on country (D1). Concretely:

```
pii_policy_version        id, version_no, published_at, published_by, notes
pii_policy_entity         version_id -> pii_policy_version
                          entity_type            e.g. NL_BSN, IBAN_CODE
                          default_enabled        bool   -- the platform default
                          in_floor               bool   -- tenant may not disable
                          language               null = all; only PERSON uses this
                          country_label          null = global; UI grouping ONLY, never
                                                 used in resolution (D1)
```

Per-tenant state stays where it already is and gains one sibling:

```
portal_orgs.pii_masked_entities      text[]   -- existing: the enabled set
portal_orgs.pii_allow_list           jsonb    -- new: [{value, match: exact|regex, note}]
```

**THE resolution for one request SHALL** be, in order:

1. Start from the active `pii_policy_version`'s `default_enabled` set.
2. Apply the tenant's `pii_masked_entities` as the override, if present.
3. Union in every entity where `in_floor` is true — the floor wins over both.
4. Pass the tenant's `pii_allow_list` to Presidio as `allow_list` / `allow_list_match`.
5. `PERSON` is removed at every step regardless, until REQ-8 is satisfied.

Step 3 after step 2 is deliberate: the floor is applied last so no override order can
subtract it.

#### REQ-4 — the floor is structural (state-driven)

**WHILE** an entity type is in the platform floor, **THE tenant write endpoint SHALL** reject
an attempt to disable it, and **THE tenant UI SHALL** render it as a locked row with the
reason, not as an unchecked box.

`SECRET` and `NL_BSN` are in the floor by construction and **SHALL NOT** be removable from it
through any UI. Making them merely "unchecked by default" would leave the guarantee one
mis-click from gone.

#### REQ-5 — consumers pin a version (ubiquitous)

**THE enforcement side SHALL** resolve policy against the **currently active** version, not a
per-tenant pin. Tenants do not choose a platform version — that would leave a tenant sitting
on a superseded default indefinitely, which is the opposite of why the floor exists.

Versioning here buys **auditability and rollback**, not per-tenant divergence: publishing a
new version moves everyone, and reverting is repointing the active version rather than
replaying edits.

**THE resolved version id SHALL** appear in the Phase 2 telemetry event alongside the entity
counts, so a support question about a specific conversation can be answered with the policy
that was actually in force at the time.

### Phase 3 — new entity types

#### REQ-6 — `NL_CITY` is a deny-list, and the homonym rate is the gate (ubiquitous)

**THE recogniser SHALL** be a `PatternRecognizer` with a `deny_list` of Dutch place names —
closed vocabulary, no model, no external call — and **SHALL NOT** attempt street-level
detection (D5).

**IT SHALL** be **case-sensitive**. `Best` is a municipality in Noord-Brabant; `best` is an
ordinary adverb, and the registry's default `IGNORECASE` would collapse the two. This is the
same defect already fixed once in `NL_POSTCODE`, where `IGNORECASE` turned `[A-Z]{2}` into
"any two letters" and made `2026 en` a postcode.

**THE list SHALL** ship with a curated exclusion set for place names that are common Dutch
words even when capitalised at sentence start — `Best`, `Ede`, `Nes`, `Hem`, `Ee`, `Oss`,
`Bunde` are the known cases and the list is not exhaustive.

**THE entity SHALL NOT** be enabled by default until a **homonym false-positive measurement**
exists: the deny-list run over a representative sample of real Klai transcript and
knowledge-base text, reporting how often a match is not a place. No product publishes this for
Dutch, so it has to be ours, and it is cheap — the corpus already exists.

**IF** the measured rate is unacceptable, **THEN** the fallback is proximity: only treat a
place name as `NL_CITY` when a postcode appears within a small window, which trades recall for
precision and keeps the combination that actually identifies someone.

#### REQ-7 — default-on is staged, not flipped (event-driven)

**WHEN** the six default-on entity types are activated, **THE rollout SHALL** proceed
tenant-by-tenant using the existing `KLAI_PII_ENFORCE_ORG_IDS` allowlist, **SHALL** report
per-entity detection counts per tenant before widening, and **SHALL NOT** be applied to all
tenants in one change.

The observer has been counting since Phase 2 of the pipeline SPEC. That data is the input.

#### REQ-8 — `PERSON` is gated on evidence that does not exist yet (state-driven)

**`PERSON` SHALL NOT** appear as a selectable entity in any UI, **SHALL NOT** be accepted by
any write endpoint, and **SHALL** remain in `PII_FORBIDDEN_ENTITIES` **UNTIL** all of:

1. A detector is deployed. Candidate: `gliner_multi_pii-v1` or GLiNER2-PII, both Apache-2.0.
   **`Piiranha` is excluded**: CC-BY-NC-ND-4.0, non-commercial *and* no-derivatives. A widely
   cited 2024 article calls it MIT — that is wrong, and the model card is authoritative.
2. The analyzer's memory ceiling is raised deliberately. It loads no model today and is capped
   at 512 MB; a Dutch NER model is 500–750 MB on top.
3. A Dutch PERSON precision/recall measurement exists **on Klai's own transcripts**, because
   none exists in public for Dutch in any domain.
4. An answer-quality measurement exists for over-masking — the catch in the Motivation. A/B a
   set of real questions with and without `PERSON` masking and compare answers.

Tussenvoegsels (`van`, `de`, `van der`, `'t`, `ten`) are a closed set and should be a grammar
rule extending a name span, not something a model must learn. Common-noun surnames (`Bakker`,
`Visser`, `Dijk`, `Kok`) are expected to **under**-fire rather than over-fire, because training
corpora use them overwhelmingly as common nouns.

**A per-country export of the most common names is available** and changes what condition 1
can look like. The research recommends a name gazetteer as a **secondary signal combined with
model confidence**, not as a hard allow-list — a bare list re-creates the homonym problem in
reverse, masking every `Bakker` and `Roos` regardless of context. So the list raises
confidence on a candidate the detector already surfaced; it does not become the detector.

That also opens a cheaper path worth evaluating before committing to a NER model: gazetteer
plus title/tussenvoegsel context (`dhr.`, `mevr.`, `van der`, an adjacent first name) may
carry enough of the value at a fraction of the 500-750 MB memory cost. **THE choice between
the two SHALL** be made on the condition-3 measurement, not by assuming the model is better.

### Phase 4 — custom entities and preview

#### REQ-9 — user-supplied patterns run in a safety envelope (ubiquitous)

Tenant-defined regex is the most dangerous surface in this SPEC. **IT SHALL**:

- run on a **linear-time engine** (RE2 or equivalent), not the backtracking engine used for
  Klai's own patterns. This is the only structural defence against ReDoS;
- carry a timeout in the **low hundreds of milliseconds**. Presidio's own default is
  `REGEX_TIMEOUT_SECONDS = 60` — copy the existence of a timeout, not that value;
- run against a **length-capped** input, since backtracking blowups need long inputs;
- be **rate-limited at the preview endpoint specifically**, which is the one deliberately
  exposed to less-trusted admins.

**A custom entity SHALL NOT** be addable to the never-restore set. A tenant defining a pattern
whose matches are destroyed unrecoverably is a support incident, not a feature.

**THE same envelope SHALL** cover allow-list entries with `match: regex` (D1). An exclusion
pattern is user-supplied regex reaching the analyzer exactly like a detection pattern, and is
if anything more dangerous: a catastrophic pattern there fails *open*, silently letting real
PII through rather than merely erroring.

#### REQ-10 — preview runs the real pipeline (ubiquitous)

**THE preview SHALL** call the same analyzer and the same masking code the request path uses,
and **SHALL** show what would actually be forwarded to the provider.

A preview that approximates is worse than none: it teaches an admin to trust a policy that
behaves differently in production. AWS Bedrock's console does this correctly — it invokes the
real guardrail.

**THE preview SHALL NOT** persist submitted sample text, and **SHALL** be subject to REQ-9's
rate limit.

### Phase 5 — the admin surfaces

#### REQ-11 — tenant UI extends the existing privacy tab (ubiquitous)

**THE tenant surface SHALL** be a new section on the existing `privacy` tab of
`/admin/settings`, beside the telemetry control, following
`-ExtensionsSettingsSection.tsx`'s bounded-checkbox pattern and the house conventions in
`ui-standards.md`: `components/ui/` primitives, Paraglide strings, `sonner` toasts, semantic
tokens, hand-rolled form state (this frontend has **no** form library and one **SHALL NOT** be
introduced here).

#### REQ-13 — the settings page is accurate about what it does and does not catch (ubiquitous)

**Framing first, because the wrong framing is worse than no page.** Klai's GDPR position does
not rest on this feature. EU-only processing, the DPA, the telemetry modes and the retention
limits already carry it. This is **voluntary data minimisation** — sending a provider less
than we are entitled to send, because it is the decent thing to do, not because something was
missing.

**THE copy SHALL NOT** be written defensively. Wording that reads as a disclaimer implies the
platform needed this to be compliant, which is untrue and sells the existing position short.
The register is "here is an extra layer and here is exactly what it catches", not "here is why
this may not be enough".

**AND it SHALL be accurate**, because an admin deciding what to switch on needs to know where
the detection ends:

1. **Person names are not detected** (until REQ-8 lands). If a name appears in a message, the
   provider sees it. State it, rather than letting an unchecked box imply names are handled.
2. **Addresses are detected by postcode and city, not by street.** An address written without
   a postcode is not detected.
3. **Detection covers structured identifiers** — numbers, codes, formats. It is not a general
   understanding of what is sensitive.
4. **The context around a masked value stays.** Masking a BSN in *"the BSN of the customer
   with payment arrears is 111222333"* removes the number and leaves the fact. Identification
   by combination — a rare job title, an exact date, a small town — is not what this catches.
5. **False positives happen.** For restored entity types the user sees their own text back
   unchanged, but the model answered on the masked version, so an answer can be slightly worse
   with no visible sign.
6. **This governs what is sent to the AI provider.** It does not change what Klai stores:
   knowledge bases, meeting transcripts and chat history are unaffected.
7. **Two categories cannot be switched off**, with the reason: credentials, because forwarding
   one is an incident regardless of preference; the BSN, because a private company may only
   process one where a statute allows it.

**THE word "anonymous" (and "geanonimiseerd") SHALL NOT** be used about the result. That one is
not a matter of tone: pseudonymised data is still personal data under GDPR Art. 4(5), and
calling it anonymous is factually wrong in any framing.

**THE page SHALL NOT** claim that enabling any of this makes a tenant compliant with anything.
Not because compliance is in doubt, but because a per-entity toggle is not what compliance
rests on, in either direction.

**THE page SHALL** state the same things to a tenant admin and to Klai staff.

#### REQ-12 —#### REQ-12 — platform UI extends the existing console (ubiquitous)

**THE platform surface SHALL** live under `/admin/platform`, gated by
`require_platform_admin()`, as a route rather than a drawer — `ui-standards.md` forbids sheets
for admin entity detail.

**IT SHALL NOT** be a second admin console. `SPEC-PLATFORM-ADMIN-001` already owns this
surface.

## Non-Functional Requirements

- **Latency.** Policy resolution is cached per org in-process (the existing client already
  does this, 30 s TTL). Adding versioning **SHALL NOT** add a per-request round trip.
- **Failure mode.** Policy resolution already fails closed to the empty policy. That stays:
  under D4, closed means the floor still applies, so a portal-api outage degrades to
  "credentials and BSN masked, nothing else" rather than to "nothing masked".
- **Tenant isolation.** The platform endpoints use `cross_org_session()`; the tenant endpoints
  must not. `/klai:tenant-review` applies to every PR in this SPEC.
- **No new admin console, no new form library, no new NER model** in scope.

## Acceptance Criteria

| AC | Test | Expected |
|---|---|---|
| AC-1 | Tenant admin PATCHes a valid entity set | Persisted; audit row written; `validate_entity_selection()` was called |
| AC-2 | Tenant admin attempts to disable a floor entity | 4xx with the reason; value unchanged |
| AC-3 | Tenant admin submits `PERSON` | Rejected by the endpoint AND by the DB CHECK |
| AC-4 | Non-admin tenant user PATCHes | 403, no DB write |
| AC-5 | Tenant user opens the privacy tab | Floor entities render locked with a reason; others editable |
| AC-6 | Platform admin publishes a version | New immutable version; prior version still resolvable |
| AC-7 | Policy resolved for an org | Version id present in the Phase 2 telemetry event |
| AC-8 | Portal-api unreachable | Enforcement falls back to floor-only, not to nothing |
| AC-9 | Custom regex with a catastrophic-backtracking shape | Rejected or times out under 500 ms; no worker blocked |
| AC-10 | Preview with a known BSN and a known phone number | Output matches byte-for-byte what the request path would forward |
| AC-11 | Preview submitted twice above the rate limit | Second call throttled |
| AC-12 | `NL_CITY` homonym measurement over real corpus | Non-place hit rate reported; `Best`/`Ede`/`Nes` exclusions verified; entity stays off until reviewed |
| AC-12b | `NL_CITY` on `dat is de best mogelijke oplossing` | No match (case-sensitive + exclusion list) |
| AC-13 | Two orgs with different policies, concurrent requests | Neither sees the other's policy |
| AC-14 | Settings page copy review | All seven REQ-13 points present; "anonymous"/"geanonimiseerd" absent; no claim that enabling this makes a tenant compliant; tone is additive, not defensive |
| AC-15 | Settings page with `PERSON` unavailable | Copy states names are not detected — not merely an absent or disabled checkbox |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Default-on degrades answer quality through over-masking | medium | high | Documented in two 2026 studies. REQ-7 stages the rollout per tenant with detection counts before widening; every default-on entity is restored, so the user's own text is never lost |
| `NL_CITY` deny-list masks ordinary Dutch words | medium | medium | Case sensitivity plus a curated exclusion list; REQ-6 makes the homonym measurement a gate. Fallback is postcode-proximity, which keeps the identifying combination and drops the lone-city case |
| A tenant disables something they needed masked | medium | medium | The floor exists precisely for the categories where that is unacceptable; everything above the floor is the controller's call, which is the correct allocation |
| Tenant regex takes down the shared analyzer | low | high | REQ-9's four-part envelope. This is the only surface in the SPEC where untrusted input becomes executable |
| Versioning is built and never used | medium | low | AC-7 puts the version id in telemetry, so it is load-bearing from day one rather than decorative |
| `PERSON` gets enabled on multilingual-average numbers | medium | high | REQ-8's four conditions, of which two are measurements that do not exist yet. Named explicitly so "GLiNER says F1 0.47" cannot be read as sufficient |
| Someone re-proposes Piiranha because it supports Dutch | medium | low | Rejected in REQ-8 with the licence and the incorrect secondary source both named |

## Implementation handoff

| PR | Phase | Scope | Gate |
|----|-------|-------|------|
| 1 | 1 | Tenant write endpoint + service + CHECK widening | AC-1 through AC-4 |
| 2 | 5a | Tenant UI section on the privacy tab, including REQ-13's limitations copy | AC-5, AC-14, AC-15 + Playwright click-through |
| 3 | 2 | Platform defaults table, floor, versioning, resolution order | AC-6, AC-7, AC-8 |
| 4 | 5b | Platform UI under `/admin/platform` | `/klai:tenant-review` |
| 5 | 3 | `NL_CITY` deny-list recogniser, shipped off, plus the homonym measurement | AC-12, AC-12b |
| 6 | 4 | Custom entities + preview | AC-9, AC-10, AC-11 |
| 7 | 3 | Default-on rollout, staged per tenant | AC-13 + per-tenant counts |

Rules for the implementer:

- Do not introduce a form library, a second admin console, or a NER model. All three are
  explicitly out of scope and all three are tempting.
- `validate_entity_selection()` is the single validation path. Its docstring says so.
- Every PR that touches policy resolution needs `/klai:tenant-review`.
- Load the module through LiteLLM's loader in tests if you touch `deploy/litellm/**` —
  `tests/test_callback_module_loading.py` exists because a `@dataclass` crashlooped production
  on 2026-08-21 while 837 unit tests passed.

## Sources

Source references:

- `klai-portal/backend/app/core/permissions.py:213,364-394,430-456` — platform-admin check, role gates
- `klai-portal/backend/app/core/profiles.py:23-67` — role ladder and capabilities
- `klai-portal/backend/app/api/admin/platform_unlocks.py:89-115` — the write-endpoint template
- `klai-portal/backend/app/services/pii_entity_policy.py:42-90` — unwired validation
- `klai-portal/backend/app/models/portal.py:125-166` — `platform_unlocked_features`, `pii_masked_entities`
- `klai-portal/backend/app/services/telemetry_level.py:42-127` — the chain to copy
- `klai-portal/frontend/src/routes/admin/_components/-ExtensionsSettingsSection.tsx:46,64-92` — two-level UI precedent
- `klai-portal/frontend/docs/ui-standards.md` — layout, primitives, forbidden patterns
- `deploy/presidio/analyzer/klai_pii_recognizers.py:56-419` — the nine recognisers
- `deploy/litellm/klai_pii_entities.py:33-68` — never-restore vs return set

External research (2026-08-21):

- [AWS Bedrock Guardrails — versions](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-versions-create-manage.html) and [test](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-test.html) — draft/version/pin model, preview against the real engine
- [Tonic Textual — generator_config](https://tonic-textual-sdk.readthedocs-hosted.com/en/latest/redact/redact_config.html) — per-entity state with a global fallback
- [Google Cloud DLP — infoType categories](https://docs.cloud.google.com/python/docs/reference/dlp/latest/google.cloud.dlp_v2.types.InfoTypeCategory) and [reference](https://docs.cloud.google.com/sensitive-data-protection/docs/infotypes-reference) — location/industry/type axes; `STREET_ADDRESS` latency warning; country detectors less reliable than generic
- [Azure AI Language — entity categories](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/concepts/entity-categories-list) — global vs country-prefixed split
- [Nightfall — confidence levels](https://help.nightfall.ai/detection_platform/faq/confidence_levels) — vendor recommending customers raise thresholds
- [presidio#752](https://github.com/microsoft/presidio/issues/752) — no rule-based address recogniser, and why
- [PDOK Locatieserver](https://www.pdok.nl/pdok-locatieserver) · [BAG licence: publiek domein](https://data.overheid.nl/dataset/basisregistratie-adressen-en-gebouwen--bag-) — free validation, no SLA
- [iiiorg/piiranha-v1](https://huggingface.co/iiiorg/piiranha-v1-detect-personal-information) — CC-BY-NC-ND-4.0, contradicting the widely cited MIT claim
- [urchade/gliner_multi_pii-v1](https://huggingface.co/urchade/gliner_multi_pii-v1) · [GLiNER2-PII](https://fastino.ai/blog/gliner2-pii-open-source-privacy-filtering-with-pii-detection) — Apache-2.0 candidates
- [spacy/nl_core_news_lg](https://huggingface.co/spacy/nl_core_news_lg) — P 0.785 / R 0.750 aggregate, silver-standard, out-of-domain caveat
- [arXiv 2508.05545](https://arxiv.org/pdf/2508.05545) · [arXiv 2509.14464](https://arxiv.org/pdf/2509.14464) — measured over-redaction
- [arXiv 2604.11430](https://arxiv.org/pdf/2604.11430) — the precision/recall asymmetry argument, and its limits
