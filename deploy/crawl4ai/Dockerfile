# Klai build of crawl4ai: one patch on top of upstream.
#
# Build (on core-01, where the compose stack runs):
#   docker build -t ghcr.io/getklai/crawl4ai:0.9.5-local-<YYMMDD-HHMM> deploy/crawl4ai/
#
# The tag follows the locally-built convention accepted by
# deploy/check-image-pullable.sh (`<semver>-local-YYMMDD-HHMM`) — this image
# is never pushed to a registry. Keep it in lockstep with the `crawl4ai` image
# pin in deploy/docker-compose.yml. They drifted between 2026-08-13 and
# 2026-08-14: compose was flipped to 0.9.2 (for CVE-2026-57571/57572/57573)
# while this line stayed on 0.8.9, so any rebuild would have silently shipped
# the old version back — reintroducing those CVEs AND the 0.9.x
# incompatibility that 400'd every tenant crawl for ~20 minutes on 2026-08-13.
#
# 0.9.x forbids `js_code` / `js_code_before_wait` on untrusted configs, and
# 0.9.5 (GHSA-qj46-wp7m-rgj8) also refuses a JavaScript `wait_for` with HTTP
# 400, which ended every nightly crawl with 0 pages from 2026-10-23. The client
# in knowledge_ingest/crawl4ai_config.py therefore sends CSS only. A probe of
# 20 stored pages without the old JS prep fetched 20/20 with identical headings,
# +1% to +8% words and markdown links 139 -> 363. Do not add page JS back.
#
# The body-visibility patch that used to live here is GONE, and that is the
# good outcome: 0.9.3 ships `CrawlerRunConfig.body_visibility_timeout`, which
# is the config field we proposed in
# https://github.com/unclecode/crawl4ai/issues/2129. It is on the
# untrusted-allowed list and server-clamped, so we set it per request from
# knowledge_ingest/crawl4ai_config.py instead of patching a hardcoded 30000
# and steering it with an env var. A supported field beats a patched constant.
FROM unclecode/crawl4ai:0.9.5

# site-packages is root-owned; the base image runs as appuser.
USER root
COPY apply_hook_isolation_patch.py /tmp/apply_hook_isolation_patch.py
RUN python /tmp/apply_hook_isolation_patch.py && rm /tmp/apply_hook_isolation_patch.py
USER appuser
