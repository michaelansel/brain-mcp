#!/usr/bin/env bash
# Renders nginx.conf with the deployment's identities filled in, on stdout.
#
#   BRAIN_OWNER_EMAIL=you@example.com \
#   BRAIN_SITE_READER_EMAILS=reader@example.com,other@example.org \
#     gateway/deploy/render-nginx.sh > oauth-gateway.conf
#
# BRAIN_OWNER_EMAIL is required: it is the one identity that reaches /mcp.
# BRAIN_SITE_READER_EMAILS is optional; each address on it may read /site/
# and nothing else. Neither has a default, and the repo never holds a real one.
#
# Every address is checked against a plain email shape before it is written,
# so a stray quote, brace or semicolon cannot change the config's meaning.
set -euo pipefail
cd "$(dirname "$0")"

shape='^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'

check() {
    if ! [[ "$1" =~ $shape ]]; then
        echo "render-nginx: not an email address: '$1'" >&2
        exit 1
    fi
}

owner="${BRAIN_OWNER_EMAIL:-}"
[ -n "$owner" ] || { echo "render-nginx: BRAIN_OWNER_EMAIL is not set" >&2; exit 1; }
check "$owner"

readers=""
IFS=',' read -ra list <<<"${BRAIN_SITE_READER_EMAILS:-}"
for r in "${list[@]}"; do
    r="${r//[[:space:]]/}"
    [ -n "$r" ] || continue
    check "$r"
    if [ "$r" = "$owner" ]; then
        echo "render-nginx: $r is the owner; leave it off BRAIN_SITE_READER_EMAILS" >&2
        exit 1
    fi
    readers+="    \"$r\" 1;"$'\n'
done

placeholder='"allowed-email@example.com"'
grep -qF "$placeholder" nginx.conf || { echo "render-nginx: owner placeholder missing from nginx.conf" >&2; exit 1; }
grep -qF '# @SITE_READERS@' nginx.conf || { echo "render-nginx: reader marker missing from nginx.conf" >&2; exit 1; }

while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        *'# @SITE_READERS@'*) printf '%s' "$readers" ;;
        *"$placeholder"*) printf '%s\n' "${line//$placeholder/\"$owner\"}" ;;
        *) printf '%s\n' "$line" ;;
    esac
done <nginx.conf
