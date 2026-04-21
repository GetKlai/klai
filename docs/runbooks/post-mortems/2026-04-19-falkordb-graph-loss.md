# Post-mortem: FalkorDB graph data loss on image pinning

| Field | Value |
|---|---|
| Date | 2026-04-19 |
| Severity | Major — total loss of Graphiti knowledge graph across all orgs |
| Duration of data loss | Permanent for pre-incident ingests (no backup existed) |
| Detection | User noticed "Relations (org-wide): 0 entities, 0 links" in Klai portal |
| Author | Mark Vletter + Claude (post-mortem drafted 2026-04-19 23:00) |
| Status | Mitigated (mount fixed in 3c5673ea). Corrective actions open. |

---

## 1. Impact

- The entire Graphiti knowledge graph in FalkorDB was wiped for **all orgs**.
- Qdrant chunks (1124 fragments) and Postgres `knowledge.artifacts` (508 rows for the getklai org; 511 total) were **not** affected.
- User-visible: knowledge-base overview shows `0 entities, 0 links` under Relations.
- Downstream: any feature relying on Graphiti graph traversal (entity relations, graph-augmented retrieval) degrades to pure vector search until the graph is rebuilt.
- No data breach, no availability impact on login or ingest pipelines.

## 2. Timeline (UTC+2 unless noted)

| When | Event |
|---|---|
| 2026-03-26 15:57 | Commit `fe9a4239` introduces the FalkorDB service with bind mount `/opt/klai/falkordb-data:/data`. **Latent persistence bug born.** The image's real data path is `/var/lib/falkordb/data`, so the host directory stays empty from this moment on — but FalkorDB itself runs fine, writing its RDB snapshots into the container's writable layer. |
| 2026-03-26 → 2026-04-19 | FalkorDB container runs continuously. Graphiti episodes are ingested normally; queries work; entity/edge counts grow as expected. Data lives in RAM + ephemeral RDB snapshots inside the container's writable layer. The host bind mount stays empty. No symptom visible from inside or outside the service. |
| 2026-04-19 09:57 | Commit `5d12587e` ("pin the last 5 services I missed") changes `falkordb:latest` → `v4.18.1` as part of a broad image-pinning policy rollout. |
| 2026-04-19 ~11:00 EEST (08:00 UTC) | `docker compose up -d` executed on core-01 as part of the deploy. Container is recreated. Ephemeral in-container data dir is lost with the old container. **Graph data is gone at this moment.** |
| 2026-04-19 11:01 EEST | New `klai-core-falkordb-1` starts. Logs show no "Loading RDB" line — started from empty state. |
| 2026-04-19 19:23 EEST | FalkorDB first BGSAVE ("1 changes in 3600 seconds") writes 909 bytes — just the skeleton of newly-requested graphs, no real data. |
| 2026-04-19 22:00 EEST | User opens Klai portal, sees `0 entities, 0 links`, reports the incident. |
| 2026-04-19 23:58 EEST | Mount corrected via commit `3c5673ea`. Container recreated with correct mount. Canary write + SAVE confirms `dump.rdb` now appears on host volume (909 bytes). |

## 3. Root cause — 5 whys

1. **Why did the graph disappear?**
   Because the FalkorDB container was recreated at 11:00 EEST. Graph data lived in RAM and in an RDB snapshot *inside the container's writable layer*. `docker compose up -d` with a changed image tag discards both.

2. **Why wasn't the RDB snapshot on the persistent host volume?**
   Because the bind mount in `docker-compose.yml` targeted `/data` inside the container, but FalkorDB writes its RDB/AOF to `/var/lib/falkordb/data` (as configured in the image's `FALKORDB_DATA_PATH` env var). FalkorDB saved correctly — just to the wrong path. For 24 days those saves landed in the container's writable layer (which looks identical to a "real" filesystem from inside the container, so nothing flagged it).

3. **Why did the mount target the wrong path?**
   Because when the service was added on 2026-03-26 (commit `fe9a4239`, SPEC-KB-010), `/data` was chosen based on the Redis-family convention (Redis and RedisStack both use `/data`). The author did not verify the path against the FalkorDB image's actual config. The image happens to bundle a `run.sh` entrypoint that sets a non-Redis data path.

4. **Why wasn't this caught by any check?**
   - No smoke test post-deploy validates that a database's declared volume actually receives writes.
   - The image did not fail to start — FalkorDB happily wrote to the ephemeral container layer.
   - The bug was silent for 24 days until a trigger (container recreate) exposed it.
   - No monitoring on graph size: there's no alert for "FalkorDB entity count dropped to 0 across all orgs".

5. **Why did the image pinning trigger a recreate?**
   `docker compose up -d` with a changed image tag always recreates the container. This is correct docker behaviour. The problem isn't the recreate — it's that the compose file promised persistence that never existed. The pinning commit was the first deliberate container restart since the service was introduced, which is why the latent bug surfaced precisely now.

## 4. Contributing factors

1. **`:latest` enables "deploy-without-restart"**: with `:latest` and no pull, a long-running container keeps going indefinitely. That masked the persistence bug for 24 days. An explicit pin from day one would have failed faster, when less data was at stake.
2. **No volume backup policy for FalkorDB**: the version-management playbook (§3.4) mandates `docker run --rm -v <volume>:/data ... tar czf` backups before major upgrades. This was treated as a minor pin, not a major change, so no backup was taken. Even if it had been taken, the backup would have archived an empty `/opt/klai/falkordb-data/` — because the mount was fake.
3. **Playbook silently assumes the mount works**: §3.3 ("Docker image minor/patch upgrade") tells you to `docker compose pull && up -d` and verify container health. It does not say "verify the declared volume mount points to where the image actually writes".
4. **Pitfall §7.7 ("`:latest` on server"):** the playbook correctly flags that `:latest` makes reproducibility impossible, but does not flag that the first deliberate pin of a long-lived `:latest` is itself a high-risk event for any stateful service. It should.
5. **Single-person review**: commit `5d12587e` bypassed the PR review gate (see `remote: Bypassed rule violations` during push). A reviewer might have spotted the latent mount bug, especially for a stateful service. The branch protection "Required status check quality" was expected but skipped.

## 5. What the playbook said, and what actually happened

| Playbook step (§3.3) | What it says | What happened |
|---|---|---|
| 1. Research | "Data migration on startup? Config file syntax change?" | Not done. The commit was batched with four other image pins; the release-notes-reading step was skipped per-image. |
| 2. Repo | "Edit compose with new explicit version tag" | Done correctly. |
| 3. VERSIONS.md | "Update the row with the new version" | Done correctly. |
| 4. Commit + push | "auto-syncs compose to /opt/klai" | Done, via `deploy-compose.yml` workflow. |
| 5. Pull + restart | "docker compose pull && up -d" | Done. This is the step where data died. |
| 6. Verify | "docker ps ... container must be healthy, logs must not show errors" | Container was Up + healthy. Logs showed successful startup (of an empty DB). **No check flagged that the pre-existing graph was gone.** |

The playbook's verify step is health-only. For stateful services, "container is healthy" is necessary but not sufficient — you also need "the data I had before is still there".

## 6. Blast radius check

I audited every other host bind mount in `deploy/docker-compose.yml` to verify this isn't a wider pattern:

| Mount | Host | Container path | Status |
|---|---|---|---|
| `/opt/klai/portal-dist:/srv/portal:ro` | populated | correct for Caddy | OK (read-only, rebuilt by CI) |
| `/opt/klai/caddy-logs:/var/log/caddy` | populated | matches Caddy default | OK |
| `/opt/klai/ollama/models:/root/.ollama` | populated | matches Ollama default | OK |
| `/opt/klai/research-uploads:/opt/klai/research-uploads` | populated | inside-out path, explicit | OK |
| `/opt/klai/falkordb-data:/data` | **empty** | **image writes to `/var/lib/falkordb/data`** | **BUG — now fixed** |
| `/opt/klai/garage-meta:/var/lib/garage/meta` | populated | matches Garage default | OK |
| `/opt/klai/garage-data:/var/lib/garage/data` | populated | matches Garage default | OK |
| `/opt/klai/garage/garage.toml:/etc/garage.toml:ro` | populated | correct | OK |

Named Docker volumes (`postgres-data`, `mongodb-data`, `redis-data`, etc.) are managed by Docker itself and were spot-checked. None exhibit the same class of bug: their mount targets (`/var/lib/postgresql`, `/data/db`, `/data`) match their images' data paths.

**Only FalkorDB was affected. No other service silently discards writes.**

## 7. Lessons

### 7.1 A bind mount is a claim, not a fact

Declaring `/opt/klai/X:/some/path` in compose tells docker where to mount, not where the image writes. If the image's configured data path differs, the bind mount is decorative — writes still "succeed" (into the container's writable layer) and vanish on recreate. The service appears healthy; persistence is a lie. Always verify with:

```bash
docker inspect <image> --format '{{range .Config.Env}}{{println .}}{{end}}'
docker inspect <image> --format '{{.Config.WorkingDir}} {{.Config.Cmd}} {{.Config.Entrypoint}}'
```

Grep the output for any env var ending in `_DATA_PATH`, `_HOME`, or `_DIR`. Compare to the mount target in compose.

### 7.2 First explicit pin of a long-running `:latest` stateful service is a major event

Going from `:latest` (never restarted) to `:v4.18.1` (triggers recreate) is technically a one-line change but operationally a restart of a long-running stateful container. All the major-upgrade hygiene applies: backup, verify data survives, verify mount path.

### 7.3 "Healthy" is not "data intact"

Post-deploy verification must include a data-intact check for stateful services, not just container health. For a graph DB that's "count of nodes across graphs should be non-zero and monotonically ≥ previous". For Postgres it's "row count on a known pilot table should be ≥ previous".

### 7.4 Silent misconfig survives until a trigger

The mount bug existed for 24 days without symptoms. Any test that had forced a container recreate during that window (rolling restart drill, patch deploy, OOM kill) would have exposed it much earlier — probably with less data at stake. Rare container restarts are not a sign of stability; they are a sign of missing drills.

## 8. Corrective actions

### Immediate (done during incident)

- [x] Fix compose mount: `/opt/klai/falkordb-data:/var/lib/falkordb/data` (commit `3c5673ea`).
- [x] Deploy fix to core-01; verify canary write + SAVE lands on host dump.rdb.
- [x] Write this post-mortem.

### Short-term (this week)

- [ ] Decide on graph rebuild strategy. Two options:
      (a) Run `python -m knowledge_ingest.backfill` after clearing stale `graphiti_episode_id` markers. ~508 artifacts × LLM extraction = hours + credits.
      (b) Accept the loss, let the graph repopulate organically from new ingests.
      **Owner**: Mark. **Decision required before next steps.**
- [ ] Add a FalkorDB volume backup to the weekly maintenance window:
      ```bash
      ssh core-01 "docker run --rm -v /opt/klai/falkordb-data:/data -v /opt/klai/backups:/backup alpine tar czf /backup/falkordb-$(date +%F).tar.gz /data"
      ```
      Cron or systemd timer on core-01.
- [ ] Add a cheap post-deploy persistence smoke test to `deploy/scripts/push-health.sh` or equivalent. For each stateful service: write a canary key, force a SAVE / flush, then verify the backing file on disk changed its mtime in the last 60 seconds.

### Medium-term (next sprint)

- [ ] Write a `deploy/scripts/audit-volumes.sh` that, for every bind mount in compose, diffs the mount target against the image's declared data path (extracted from image env vars). Fail CI on mismatch.
- [ ] Add a Grafana/VictoriaMetrics panel + alert: "FalkorDB total node count across all orgs". Alert if it drops > 50% week-over-week.
- [ ] Update `docs/runbooks/version-management.md`:
      - §3.3 verify step: add "for stateful services, also verify a pre-existing known record still exists post-restart."
      - §3.4 pre-step: add "verify the declared volume mount path matches the image's actual data path via `docker inspect`."
      - §7: add a new pitfall "`7.10 — Bind mount path must match the image's data path` — summary of this incident."
- [ ] Re-enable branch protection on `main` for commits to `deploy/docker-compose.yml`. Prevent future bypass.

### Structural

- [ ] Introduce a "stateful service change" label on PRs. Any PR modifying a service with a bind mount gets a required checklist item: "mount path verified via docker inspect? Volume backed up?"
- [ ] All new stateful-service compose entries require an accompanying entry in `docs/runbooks/volume-mounts.md` (to be created) documenting: image, data path, backup method, restore procedure. No volume backup plan = PR blocked.

## 9. Non-goals

This post-mortem does not address:

- Whether Graphiti/FalkorDB is the right choice for the knowledge graph layer. That's a separate architectural discussion.
- Whether we should run FalkorDB in HA/replicated mode. Premature for current scale.
- General image pinning strategy — that's covered in `docs/runbooks/version-management.md` §1.1 and is unchanged.

## 10. What must not happen again

A stateful service bind mount that has never been validated. Every mount is either proven (by evidence of writes landing on the host) or it is a bug waiting to trigger. We had one. The audit in §6 says we have no others. The corrective actions in §8 are designed so that we never have a third category — "mounts whose validation status is unknown."

---

*Post-mortem classification: structural bug with ~24-day latent window, triggered by routine hygiene commit. Not a people-failure; a process gap.*
