# Running a meetlat round

The chat quality measuring stick (`docs/architecture/chat-quality-history-and-plan.md`, sections 6 and 8) is one CLI,
`klai-portal/backend/scripts/meetlat.py`. It holds no case data: the case file, the rows it produces and the overlay
live outside the repository, in the output directory of a throwaway portal-api clone.

- `run` replays the user turns named in a case file through the one chat pipeline and writes one row per turn.
- `compare` classifies the rows of one or more rounds against the case file and prints the verdicts and a reading section.
- `retrieval` replays the same turns but only their retrieval, and says per stage whether the article a case expects was
  there ([Measure the retrieval](#measure-the-retrieval)).

A case is one JSON object per line: `cid` (first 8 characters of a conversation id), `turn` (index of the user turn),
`class` (`weigering`, `antwoord_kb`, `dekkend_antwoord`, `antwoord_algemeen_gelabeld` or `weigering_feiten`) and,
for `dekkend_antwoord`, optionally `variants` (regexes that must all appear in the answer, default `android` and
`iphone|ios`). `run` reads only `cid` and `turn`.

## Why the banner exists

In October 2026 three rounds ran the code of `main` instead of the candidate: a script put its own backend root at the
front of `sys.path` when it was imported, which shadowed the overlay given through `PYTHONPATH`, and nothing printed
which code ran. The scripts no longer touch `sys.path` on import, and `run` prints where the pipeline's modules were
loaded from before it replays anything.

## Rounds run on the Vibe allowance

A round never spends the keys real visitors use. On 23 October 2026 several rounds ran in parallel on `klai-primary`,
`klai-medium` and `klai-fast`, spent the Mistral workspaces' monthly limit, and both keys answered 402 from about 10:00
UTC: the gateway refused every model and live chat was down for every tenant. The rule of the chat plan (section 6 of
`docs/architecture/chat-quality-history-and-plan.md`) is enforced by the CLI, so no flag or habit has to remember it:
`run` and `retrieval` set the request model and every model the chat path reads from the settings to `klai-ingest` (the
answer, the case and the judges that default to the small model) or `klai-judge` (the selection step, the grounding
check, the paraphrases), and the route accepts `klai-ingest` for the round (`_apply_measurement_patches` in
`meetlat.py`). The calls retrieval-api makes itself cannot be patched from the portal, so
`deploy/scripts/retrieval-api-oneoff.sh` fixes its model variables to `klai-ingest` (see
[Measure the retrieval](#measure-the-retrieval)). A round that points at the live retrieval-api (the default
`KNOWLEDGE_RETRIEVE_URL`) still lets that service rewrite follow-up questions on `klai-fast`, so a round with follow-up
turns runs against such a one-off. A model call added to the chat path later has to be routed there too;
the test `test_a_replayed_turn_calls_no_model_outside_the_vibe_allowance` fails on any turn that names another model.

## Run a round

1. Build the overlay in the output directory of the one-off (here `/opt/klai/oneoff/meetlat`, mounted as `/out`). It
   needs complete `app/` and `klai_chat_prompts/` packages of the candidate, plus the scripts, so the tool matches the
   code it measures:

   ```bash
   stage=$(mktemp -d)
   git -C <checkout> archive <candidate-ref> klai-portal/backend/app klai-portal/backend/scripts \
     klai-libs/chat-prompts/klai_chat_prompts | tar -x -C "$stage"
   mkdir -p /opt/klai/oneoff/meetlat/ov-candidate
   mv "$stage"/klai-portal/backend/app "$stage"/klai-portal/backend/scripts \
     "$stage"/klai-libs/chat-prompts/klai_chat_prompts /opt/klai/oneoff/meetlat/ov-candidate/
   ```

   Put the case file next to it as `/opt/klai/oneoff/meetlat/cases.jsonl`. The container user must be able to read both.

2. Run it through the one-off, with the overlay first on `PYTHONPATH` and the backend root after it:

   ```bash
   deploy/scripts/portal-api-oneoff.sh --out /opt/klai/oneoff/meetlat -- \
     env PYTHONPATH=/out/ov-candidate:/repo/klai-portal/backend \
     python /out/ov-candidate/scripts/meetlat.py run --surface internal --org <org-slug> \
       --cases /out/cases.jsonl --out /out/round-candidate-1.jsonl --overlay /out/ov-candidate
   ```

   `--days` (default 365) bounds how far back the conversations are looked up.
   Leave out `--overlay` to measure the image as it is; the banner then says that nothing is checked.

   A widget round replays as one widget, through the route itself (a preview session token, the answer held), so the
   widget's own settings and articles apply and nothing is recorded. Its cases carry their own conversation: `id`,
   `messages` (the visitor's turns and the earlier replies, ending in the turn to replay) and `expect` (`antwoord`,
   `vraag`, `niet_gevonden` or `doorverwijzing`). `--widget` names a JSON file with `wgt_id`, `org_id`, `kb_ids`,
   `slug` and `origin`, kept next to the cases, outside the repository:

   ```bash
   deploy/scripts/portal-api-oneoff.sh --out /opt/klai/oneoff/meetlat -- \
     env PYTHONPATH=/out/ov-candidate:/repo/klai-portal/backend \
     python /out/ov-candidate/scripts/meetlat.py run --surface widget --org <org-slug> --widget /out/widget.json \
       --cases /out/widget-cases.jsonl --out /out/widget-candidate-1.jsonl --overlay /out/ov-candidate
   ```

   A widget row has `text`, `sources`, `appointment` (the button was shown), `signals` and `total_ms`; `compare` reads
   the same file for both surfaces. The page context the browser sends when the widget has it enabled is not in the
   cases, so a widget round does not reproduce it.

   Both surfaces replay the case memory a follow-up reads in production. Per conversation, `run` replays every user
   turn up to the highest named one, in order, and writes rows for the named turns only; the earlier turns are
   replayed for the case and passages they store. An internal turn carries LibreChat's conversation id in the
   `X-Klai-Conversation-Id` header, a widget case gets one preview session for all its turns, and the store and the
   read go to a dictionary in the process, keyed by that conversation, never to the database. The history stays the
   conversation's own (LibreChat's messages, the case's `messages`), so the stored widget turn stands for the reply in
   that history, which is what the route checks before it reads a case. A round therefore replays more turns than it
   writes rows: for each conversation, its highest named turn plus one. A row written after an earlier turn of its
   conversation failed carries `prefix_errors` (that turn's `u` and error), because it was measured without that
   turn's memory, and `compare` counts such rows as "rows with prefix errors". A retried turn starts from the memory
   the turn found, never from what its own failed attempt stored.

   A deploy that lands during a round restarts the gateway or portal-api under it; the round is not protected
   against that, a turn that meets a restart is replayed: a connection error or a timeout before the stream starts, a stream
   that ends in an error frame (the gateway went away, also after part of the text), an empty answer, a selection step
   that got no verdict, or a retrieval 502/503/504, connection error or timeout behind the internal chat's refusal is
   tried again after 10, 30 and 60 seconds, each attempt logged with its number, and only then written as an error
   row. Any other failure is written as an error row at once.

3. Read the banner. It is the first output and the first key of `round-candidate-1.jsonl.meta.json`:

   ```
   meetlat: code under test
     app.services.internal_turn: /out/ov-candidate/app/services/internal_turn.py
     app.services.widget_turn: ...
     app.services.retrieval_context: ...
     app.services.passage_selection: ...
     klai_chat_prompts: /out/ov-candidate/klai_chat_prompts/__init__.py
   meetlat: overlay /out/ov-candidate (every module above must be under it)
   ```

   Every path must start with the overlay directory. With `--overlay`, `run` exits with status 2 and replays nothing if
   one does not. The meta file also holds the module paths, `git describe` of the overlay when it is a checkout, the
   start time, the org, the surface and the number of cases.

4. A run can be interrupted: start the same command again and it replays only the cases that have no row yet (rows are
   keyed by `cid` and `u`); the earlier turns of a conversation are replayed again for their memory. It refuses to resume rows that were produced with other code, so a new candidate gets a new
   `--out`. A row with an `error` counts as done; delete it to replay that case.

The replay is read-only: the database connections open read-only, the retrieval log, the gap event and the internal turn
record are skipped, the case memory stays in the process, and the grounding check gets a 30 s budget so a slow host does not decide the measurement.

## Compare rounds

```bash
deploy/scripts/portal-api-oneoff.sh --out /opt/klai/oneoff/meetlat -- \
  env PYTHONPATH=/out/ov-candidate:/repo/klai-portal/backend \
  python /out/ov-candidate/scripts/meetlat.py compare --cases /out/cases.jsonl \
    /out/round-main-1.jsonl /out/round-candidate-1.jsonl
```

Per round, one line with the automatic verdicts per class and in total, then the decisions, `unsupported`, hard facts
outside the knowledge base, median `total_ms`, error or empty rows and the selection verdicts. `weigering_feiten` is
never judged automatically: it is counted as "te lezen". The reading section lists, for every `weigering_feiten` and
`dekkend_antwoord` case, each round's decision, sources, unsupported count, hard facts and the first 260 characters of
the text, plus any e-mail addresses, amounts and URLs in it. `--json` prints the same as one JSON object.

Keep the output in the output directory: the rows hold real answers.

## Measure the retrieval

`retrieval` takes the options of `run` and replays every turn only up to retrieval-api's answer. It sends the request the
chat sends (`retrieval_context._retrieve_body`) with `debug_stages: true`, keeps what comes back, and stops the turn
there: the selection step, the writer and the grounding check never run, so a round takes minutes and spends none of the
`klai-medium` budget that live answers share. The earlier turns of a conversation are cut short the same way, because
the case they store for a follow-up does not depend on the reply.

A case is measured when it names the article its answer should come from and its class is `antwoord`, `antwoord_kb` or
`dekkend_antwoord`; every other case gets a `skipped` row. The expected article is the case's `articles` (a list of URLs,
as the widget cases carry them; an empty list means none); an internal case without the field takes the links in the
reply recorded in the LibreChat conversation for that turn, so check that those replies cite the article the owner
expects. Of those links only the ones on a host that retrieval returned for that turn count, so a web search citation or a
product URL does not become an expected article; a case left with none is `skipped`. A turn whose retrieval failed, and a
turn that searches nothing (a thank-you, a question about Klai itself) although its case expects an article, are misses at
every stage (`failed`, `not_searched` in the row); only a retrieval that could not be had at all (a restart that outlasted
the retries, an older retrieval-api) is an `error` row, which you delete to replay that case. URLs are compared as the
evidence pack keys a source (`klai_citations.source_url_key`: host without `www.`, path without the trailing slash).

A row says, for the expected articles together (any one counts):

- `search`: among the candidates after search, before the reranker (`retrieval_candidates`, plus Graphiti facts when that
  leg is on, and the chunks added by link expansion);
- `rerank`: among what the reranker returned (the best `3 x top_k` of the `reranker_candidates` it scored);
- `served`: the rank of the best of them among the distinct sources of the evidence pack the chat would receive, or
  `null`. That is the pack before the selection step, which then picks the passages the writer reads.

The first two stages include the query variants (the widget's two paraphrases, a follow-up's case queries), because their
candidates reach the served list too. retrieval-api answers `debug_stages` only when the request sets it, with source keys
and no text; an older retrieval-api answers without it and the row becomes an `error`, not a miss.

Next to the rows the round writes `<out>.summary.json` and prints it: `recall_search`, `recall_rerank`, `recall_served`,
`mrr_served` (mean of `1/rank` in the served list, 0 where absent), the counts `cases` (the rows that expect an article,
`failed` and `not_searched` included), `skipped` (cases without an expected article) and `errors`, and p50 and p95 of the time of `/retrieve` as the
portal saw it (`retrieve_ms`) and as retrieval-api reports it (`api_ms`). The summary is the comparison: run the same
cases against two settings and put the summaries side by side. It covers all rows in the file, so a resumed round
summarises both runs.

To measure a retrieval-api change before it merges, start the checkout's retrieval-api beside the live one and point the
portal one-off at it with `KNOWLEDGE_RETRIEVE_URL`. `deploy/scripts/retrieval-api-oneoff.sh` clones the live service's
image, environment and networks, mounts the checkout's `retrieval_api` package over the image's, opens its Postgres pool
read-only, skips the product event and the shadow write, and publishes no port. It also sets `COREFERENCE_MODEL`,
`GRAPHITI_LLM_MODEL` and `SYNTHESIS_MODEL` to `klai-ingest` (the Vibe allowance, see above) and refuses an `--env` for
them:

```bash
git -C <checkout> archive <candidate-ref> klai-retrieval-api/retrieval_api | tar -x -C "$stage"
deploy/scripts/retrieval-api-oneoff.sh --name ra-depth60 --code "$stage"/klai-retrieval-api \
  --env RERANKER_CANDIDATES=60 --env GRAPHITI_ENABLED=false
deploy/scripts/portal-api-oneoff.sh --out /opt/klai/oneoff/meetlat -- \
  env PYTHONPATH=/out/ov-candidate:/repo/klai-portal/backend KNOWLEDGE_RETRIEVE_URL=http://ra-depth60:8040 \
  python /out/ov-candidate/scripts/meetlat.py retrieval --surface widget --org <org-slug> --widget /out/widget.json \
    --cases /out/cases.jsonl --out /out/retrieval-depth60.jsonl --overlay /out/ov-candidate
docker stop ra-depth60  # the one-off runs with --rm; it refuses the live service's names
```

Both one-offs share the live Qdrant, Redis, the reranker GPU and the LiteLLM gateway, so run rounds one after the other
when the timing matters. Container names carry a timestamp to the second: start two portal one-offs a few seconds apart.
FalkorDB crashed every few minutes on 23 October 2026 (exit code 139), so with the graph leg on a round measures a
search that sometimes lacks its graph facts and waits for the 5 s graph timeout. Set `GRAPHITI_ENABLED=false` on the
one-off when the comparison is about something else, and measure the leg itself separately (plan step 8).
