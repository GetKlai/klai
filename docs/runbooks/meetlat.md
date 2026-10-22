# Running a meetlat round

The chat quality measuring stick (`docs/architecture/chat-quality-history-and-plan.md`, sections 6 and 8) is one CLI,
`klai-portal/backend/scripts/meetlat.py`. It holds no case data: the case file, the rows it produces and the overlay
live outside the repository, in the output directory of a throwaway portal-api clone.

- `run` replays the user turns named in a case file through the one chat pipeline and writes one row per turn.
- `compare` classifies the rows of one or more rounds against the case file and prints the verdicts and a reading section.

A case is one JSON object per line: `cid` (first 8 characters of a conversation id), `turn` (index of the user turn),
`class` (`weigering`, `antwoord_kb`, `dekkend_antwoord`, `antwoord_algemeen_gelabeld` or `weigering_feiten`) and,
for `dekkend_antwoord`, optionally `variants` (regexes that must all appear in the answer, default `android` and
`iphone|ios`). `run` reads only `cid` and `turn`.

## Why the banner exists

In October 2026 three rounds ran the code of `main` instead of the candidate: a script put its own backend root at the
front of `sys.path` when it was imported, which shadowed the overlay given through `PYTHONPATH`, and nothing printed
which code ran. The scripts no longer touch `sys.path` on import, and `run` prints where the pipeline's modules were
loaded from before it replays anything.

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
   deploy/scripts/portal-api-oneoff.sh --measurement --out /opt/klai/oneoff/meetlat -- \
     env PYTHONPATH=/out/ov-candidate:/repo/klai-portal/backend \
     python /out/ov-candidate/scripts/meetlat.py run --surface internal --org <org-slug> \
       --cases /out/cases.jsonl --out /out/round-candidate-1.jsonl --overlay /out/ov-candidate
   ```

   `--days` (default 365) bounds how far back the conversations are looked up. Only `--surface internal` is built.
   Leave out `--overlay` to measure the image as it is; the banner then says that nothing is checked.

   `--measurement` tells the host a round is running. The one-off holds a shared lock on `/opt/klai/.measurement.lock`
   for as long as its container runs, so several rounds can run at once. `compose-up.sh` and
   `tenant-config-regenerate.sh` want that file to themselves before they recreate or restart anything: a deploy that
   lands during a round waits for it, printing once a minute that it is waiting, for at most 20 minutes
   (`KLAI_MEASUREMENT_WAIT_SECONDS`). After that it goes ahead with a warning in the workflow log, because a hotfix must
   not sit behind a measurement, and the round meets the restart. A round started while a deploy holds the file waits
   for that deploy, then clones the portal-api it left behind.

   A turn that meets a restart anyway is replayed: a connection error or a timeout before the stream starts, a stream
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
   keyed by `cid` and `u`). It refuses to resume rows that were produced with other code, so a new candidate gets a new
   `--out`. A row with an `error` counts as done; delete it to replay that case.

The replay is read-only: the database connections open read-only, the retrieval log, the gap event and the internal turn
record are skipped, and the grounding check gets a 30 s budget so a slow host does not decide the measurement.

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
