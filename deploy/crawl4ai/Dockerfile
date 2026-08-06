# Klai build of crawl4ai with an env-tunable body-visibility wait.
#
# Stock crawl4ai spends up to 30s waiting for document.body to become
# visible and then ignores the answer (ignore_body_visibility defaults to
# True). On ng-cloak/v-cloak pages whose app never bootstraps, that is a
# fixed 30s tax per page. See apply_body_visibility_patch.py and
# https://github.com/unclecode/crawl4ai/issues/2129 for the upstream report.
#
# Build (on core-01, where the compose stack runs):
#   docker build -t ghcr.io/getklai/crawl4ai:0.8.9-local-<YYMMDD-HHMM> deploy/crawl4ai/
#
# The tag follows the locally-built convention accepted by
# deploy/check-image-pullable.sh (`<semver>-local-YYMMDD-HHMM`) — this image
# is never pushed to a registry.
#
# Tune at runtime via docker-compose.yml:
#   CRAWL4AI_BODY_VISIBILITY_TIMEOUT: "2000"   # ms; unset = stock 30000
FROM unclecode/crawl4ai:0.8.9

# site-packages is root-owned; the base image runs as appuser.
USER root
COPY apply_body_visibility_patch.py /tmp/apply_body_visibility_patch.py
RUN python /tmp/apply_body_visibility_patch.py && rm /tmp/apply_body_visibility_patch.py
USER appuser
