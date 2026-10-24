# Runbook: apply a changed KB setting to existing chunks (`kb_patch`)

**Service:** `klai-knowledge-ingest`
**Script:** `knowledge_ingest/scripts/kb_patch.py`
**Model calls:** none

## When

A knowledge base's `aliases` or `product_rules` changed (portal, `PATCH
/api/app/knowledge-bases/{slug}`). New and re-ingested documents pick the
change up by themselves; existing chunks keep the old "Also known as" line,
`product` label and embedding until this patch runs. It recomputes only the
cheap layer (`cheap_layer.py`), re-embeds only the chunks whose embedded text
changed (TEI and the sparse sidecar on our own GPU, batches of 200) and never
touches the summary, context sentence or HyPE questions. Never use
`rebuild_kb` for a settings change.

## Order (AGENTS.md: production knowledge data is rewritten only with approval per run)

Run inside the knowledge-ingest container (see [rebuild-kb.md](rebuild-kb.md)
for how to reach it), at least a minute after the setting changed: ingest
caches the settings for 60 s, so a chunk written in that window carries the old
settings and the old version and is picked up by the next run. The steps are
fixed and none is skipped:

1. **Unit tests** on the branch that is deployed:
   `cd klai-knowledge-ingest && uv run --frozen --extra dev pytest -q tests/test_cheap_layer.py`.
2. **Dry run** on production. It only reads, and prints per knowledge base how
   many points it checked, how many would change per field, and the first
   ten changes (point id, field, old -> new):
   ```bash
   python -m knowledge_ingest.scripts.kb_patch --org <zitadel org id> [--kb <slug>] [--only aliases|products]
   ```
   It stops before anything else when the settings cannot be read from Postgres.
3. **The user's yes** for that one run, in his own words, after he has seen the
   dry-run output. A yes for an earlier run does not carry over.
4. **One knowledge base as a trial**, with `INGEST_OK=1`. `--execute` refuses
   to run (exit 2) without it. The local prefix satisfies the operator
   machine's PreToolUse guard, and `-e` carries it into the container:
   ```bash
   INGEST_OK=1 ssh core-01 'docker exec -e INGEST_OK=1 klai-core-knowledge-ingest-1 python -m knowledge_ingest.scripts.kb_patch --org <zitadel org id> --kb <slug> --execute'
   ```
   Then measure: run the dry run again (it must report 0 points out of date
   for that KB), and run the retrieval measurement that motivated the setting
   (`meetlat retrieval`) before and after.
5. **The rest**, the same command without `--kb` or per KB.

## Behaviour

- Scope: every Qdrant scroll is filtered on `org_id` and `kb_slug`; the
  settings are read from `portal_knowledge_bases` for that org's zitadel id,
  read-only. An unknown `--kb` raises.
- Idempotent: a patched point carries `cheap_layer_version`, a hash of the KB's
  alias groups and product rules. With unchanged settings a second run selects
  nothing and writes nothing. Removing an alias group or a rule removes its
  line or label on the next run.
- `--only aliases` or `--only products` checks every point in scope and does
  not write `cheap_layer_version`, because the other part was not updated; a
  later full run picks those points up again.
- A batch whose sparse vector the sidecar does not return raises before it is
  written. The run is safe to repeat after the sidecar is back.
- A batch is written vectors first, then the text fields, then the version.
  When a point disappears mid-batch (a concurrent re-ingest) the call fails,
  and no surviving point carries new text or the new version next to its old
  vectors; rerun the patch to finish the batch.
