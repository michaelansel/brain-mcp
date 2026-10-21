#!/usr/bin/env bash
# Proves nginx.conf is default-closed by running it for real, in a container,
# and asking it for every kind of path with and without the allowed identity.
# Run this before deploying any change to nginx.conf.
#
#   CONTAINER=docker gateway/deploy/check-nginx.sh    # default: finch
#
# The requests are made from inside the container, because that is the only
# place they arrive from 127.0.0.1 the way exe.dev's edge does — which is what
# lets X-Forwarded-For stand in for the real client address.
set -euo pipefail
cd "$(dirname "$0")"

runtime="${CONTAINER:-finch}"
name="brain-nginx-check-$$"
tmp="$(mktemp -d)"
trap '"$runtime" rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT

# Stand-ins for the OAuth gateway (:9000) and brain-mcp (:4001).
cat >"$tmp/stubs.conf" <<'EOF'
server { listen 127.0.0.1:9000; location / { return 200 "gateway\n"; } }
server { listen 127.0.0.1:4001; location / { return 200 "mcp\n"; } }
EOF
mkdir -p "$tmp/site/release/areas"
echo "site index" >"$tmp/site/release/index.html"
echo "a note" >"$tmp/site/release/areas/index.html"
ln -s release "$tmp/site/current"

"$runtime" run -d --name "$name" \
    -v "$PWD/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v "$tmp/stubs.conf:/etc/nginx/conf.d/stubs.conf:ro" \
    -v "$tmp/site:/var/www/brain-site:ro" \
    nginx:alpine >/dev/null

for _ in $(seq 30); do
    "$runtime" exec "$name" curl -s -o /dev/null http://127.0.0.1:8000/ && break
    sleep 0.5
done

ME="X-ExeDev-Email: allowed-email@example.com"
OTHER="X-ExeDev-Email: someone-else@example.com"
ANTHROPIC="X-Forwarded-For: 160.79.104.10"
ELSEWHERE="X-Forwarded-For: 203.0.113.9"
fail=0

# check <expected-status> <description> <path> [curl args...]
check() {
    local want="$1" what="$2" path="$3" got
    shift 3
    got="$("$runtime" exec "$name" curl -s -o /dev/null -w '%{http_code}' --path-as-is "$@" "http://127.0.0.1:8000$path")"
    if [ "$got" = "$want" ]; then
        printf 'ok    %s %-52s %s\n' "$got" "$path" "$what"
    else
        printf 'FAIL  %s %-52s %s (wanted %s)\n' "$got" "$path" "$what" "$want"
        fail=1
    fi
}

echo "--- no identity: everything is closed except the OAuth paths"
check 404 "site"                    /site/
check 404 "site page"               /site/areas/
check 404 "site, no slash"          /site
check 404 "root"                    /
check 404 "unknown path"            /anything
check 404 "traversal onto the site" /authorize/../site/
check 404 "encoded traversal"       /authorize/%2e%2e/site/
check 404 "doubled slash"           //site/
check 404 "OAuth path as a prefix"  /authorize/site/
check 404 "case variant of a path"  /Authorize
check 401 "mcp challenges"          /mcp
check 404 "mcp with a suffix"       /mcp/
check 200 "discovery"               /.well-known/oauth-authorization-server
check 200 "discovery"               /.well-known/oauth-protected-resource
check 200 "authorize"               /authorize
check 200 "github callback"         /auth/github/callback
check 200 "register from Anthropic" /register -H "$ANTHROPIC"
check 200 "token from Anthropic"    /token -H "$ANTHROPIC"
check 403 "register from elsewhere" /register -H "$ELSEWHERE"
check 403 "token from elsewhere"    /token -H "$ELSEWHERE"

echo "--- someone else's identity: the same as none"
check 404 "site"          /site/ -H "$OTHER"
check 404 "root"          / -H "$OTHER"
check 401 "mcp"           /mcp -H "$OTHER"
check 404 "two identities" /site/ -H "$OTHER" -H "$ME"
check 404 "two identities, reversed" /site/ -H "$ME" -H "$OTHER"

echo "--- the allowed identity"
check 200 "site"                  /site/ -H "$ME"
check 200 "site page"             /site/areas/ -H "$ME"
check 301 "site page, no slash"   /site/areas -H "$ME"
check 302 "site, no slash"        /site -H "$ME"
check 302 "root goes to the site" / -H "$ME"
check 404 "unknown path"          /anything -H "$ME"
check 400 "traversal out of the site" /site/../../../etc/passwd -H "$ME"
check 200 "mcp"                   /mcp -H "$ME"
check 403 "mcp from a web page"   /mcp -H "$ME" -H "Sec-Fetch-Site: same-origin"
check 403 "mcp from a browser tab" /mcp -H "$ME" -H "Sec-Fetch-Site: none"

challenge="$("$runtime" exec "$name" curl -s -D - -o /dev/null http://127.0.0.1:8000/mcp | tr -d '\r' | grep -i '^www-authenticate:' || true)"
case "$challenge" in
    *'resource_metadata="https://127.0.0.1/.well-known/oauth-protected-resource"'*) echo "ok    401 carries the OAuth challenge" ;;
    *) echo "FAIL  401 has no OAuth challenge: $challenge"; fail=1 ;;
esac

redirect="$("$runtime" exec "$name" curl -s -D - -o /dev/null -H "$ME" http://127.0.0.1:8000/ | tr -d '\r' | grep -i '^location:' || true)"
case "$redirect" in
    "Location: /site/") echo "ok    redirects are relative" ;;
    *) echo "FAIL  redirect is not relative: $redirect"; fail=1 ;;
esac

revalidate="$("$runtime" exec "$name" curl -s -D - -o /dev/null -H "$ME" http://127.0.0.1:8000/site/ | tr -d '\r' | grep -i '^cache-control:' || true)"
case "$revalidate" in
    "Cache-Control: no-cache") echo "ok    site pages are revalidated" ;;
    *) echo "FAIL  site pages may be served stale: $revalidate"; fail=1 ;;
esac

[ "$fail" -eq 0 ] && echo "all checks passed" || { echo "SOME CHECKS FAILED"; exit 1; }
