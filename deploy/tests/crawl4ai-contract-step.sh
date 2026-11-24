#!/bin/sh
# Run a crawl4ai contract script as a CI step and decide what its exit means.
#
# Usage: crawl4ai-contract-step.sh <script> [args...]
#
# 0  the contracts hold: green, summary says OK.
# 3  the script's own SKIPPED (it already proved httpbin.org unreachable from
#    this host): green with a warning and a job-summary note, because an outage
#    elsewhere is not a broken contract.
# anything else, including 2: red. A crawl4ai that answers wrongly is not an
# echo-service outage, and a stray exit code must never end green.
set -u

code=0
"$@" || code=$?
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

case "$code" in
0)
    echo "### crawl4ai cookie contract: OK" >> "$summary"
    ;;
3)
    echo "::warning::crawl4ai cookie contract SKIPPED: httpbin.org is unreachable, so cookie delivery and isolation were not checked"
    {
        echo "### crawl4ai cookie contract: SKIPPED"
        echo ""
        echo "httpbin.org (the cookie echo service) did not answer a direct request from the runner. The version, body-visibility and user-agent contracts passed; cookie delivery and isolation were **not** checked in this run. This is not a contract break."
    } >> "$summary"
    code=0
    ;;
*)
    echo "### crawl4ai cookie contract: BROKEN (exit $code)" >> "$summary"
    ;;
esac
exit "$code"
