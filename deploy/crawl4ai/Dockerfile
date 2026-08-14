# Klai build of crawl4ai with an env-tunable body-visibility wait.
#
# Stock crawl4ai spends up to 30s waiting for document.body to become
# visible and then ignores the answer (ignore_body_visibility defaults to
# True). On ng-cloak/v-cloak pages whose app never bootstraps, that is a
# fixed 30s tax per page. See apply_body_visibility_patch.py and
# https://github.com/unclecode/crawl4ai/issues/2129 for the upstream report.
#
# Build (on core-01, where the compose stack runs):
#   docker build -t ghcr.io/getklai/crawl4ai:0.9.2-local-<YYMMDD-HHMM> deploy/crawl4ai/
#
# The tag follows the locally-built convention accepted by
# deploy/check-image-pullable.sh (`<semver>-local-YYMMDD-HHMM`) — this image
# is never pushed to a registry.
#
# Tune at runtime via docker-compose.yml:
#   CRAWL4AI_BODY_VISIBILITY_TIMEOUT: "2000"   # ms; unset = stock 30000
# Keep this in lockstep with the `crawl4ai` image pin in
# deploy/docker-compose.yml. They drifted between 2026-08-13 and 2026-08-14:
# compose was flipped to 0.9.2 (for CVE-2026-57571/57572/57573) while this
# line stayed on 0.8.9, so any rebuild would have silently shipped the old
# version back — reintroducing those CVEs AND the 0.9.x incompatibility that
# 400'd every tenant crawl for ~20 minutes on 2026-08-13.
#
# 0.9.x forbids `js_code` / `js_code_before_wait` on untrusted configs. Do not
# revert below 0.9.2 without also reverting the wait_for-based client in
# klai-knowledge-ingest/knowledge_ingest/crawl4ai_client.py.
FROM unclecode/crawl4ai:0.9.2

# site-packages is root-owned; the base image runs as appuser.
USER root
COPY apply_body_visibility_patch.py /tmp/apply_body_visibility_patch.py
RUN python /tmp/apply_body_visibility_patch.py && rm /tmp/apply_body_visibility_patch.py
USER appuser
