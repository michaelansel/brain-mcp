#!/usr/bin/env bash
# Renders nginx.conf with the deployment's identities filled in, on stdout.
#
#   BRAIN_OWNER_EMAIL=you@example.com \
#   BRAIN_SITE_READER_EMAILS=reader@example.com,other@example.org \
#     gateway/deploy/render-nginx.sh > oauth-gateway.conf
#
# BRAIN_OWNER_EMAIL is required: it is the one identity that reaches /mcp.
# BRAIN_SITE_READER_EMAILS is optional; each address on it may read /site/
# and nothing else. BRAIN_TOKEN_CLIENT_IPS is optional: comma-separated IPv4
# addresses (or CIDRs) that may reach /token besides Anthropic's range, for a
# confidential client such as Home Assistant. None has a default, and the repo
# never holds a real one.
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
for r in ${list[@]+"${list[@]}"}; do
    r="${r//[[:space:]]/}"
    [ -n "$r" ] || continue
    check "$r"
    if [ "$r" = "$owner" ]; then
        echo "render-nginx: $r is the owner; leave it off BRAIN_SITE_READER_EMAILS" >&2
        exit 1
    fi
    readers+="    \"$r\" 1;"$'\n'
done

ips=""
IFS=',' read -ra iplist <<<"${BRAIN_TOKEN_CLIENT_IPS:-}"
for ip in ${iplist[@]+"${iplist[@]}"}; do
    ip="${ip//[[:space:]]/}"
    [ -n "$ip" ] || continue
    if ! [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})(/([0-9]{1,2}))?$ ]]; then
        echo "render-nginx: not an IPv4 address or CIDR: '$ip'" >&2
        exit 1
    fi
    for octet in "${BASH_REMATCH[@]:1:4}"; do
        [ "$((10#$octet))" -le 255 ] || { echo "render-nginx: not an IPv4 address: '$ip'" >&2; exit 1; }
    done
    # A broad range would quietly reopen /token to most of the internet.
    mask="${BASH_REMATCH[6]:-32}"
    if [ "$((10#$mask))" -lt 16 ] || [ "$((10#$mask))" -gt 32 ]; then
        echo "render-nginx: CIDR must be /16 to /32: '$ip'" >&2
        exit 1
    fi
    ips+="        allow $ip;"$'\n'
done

placeholder='"allowed-email@example.com"'
grep -qF "$placeholder" nginx.conf || { echo "render-nginx: owner placeholder missing from nginx.conf" >&2; exit 1; }
grep -qF '# @SITE_READERS@' nginx.conf || { echo "render-nginx: reader marker missing from nginx.conf" >&2; exit 1; }
grep -qF '# @TOKEN_CLIENT_IPS@' nginx.conf || { echo "render-nginx: token-client marker missing from nginx.conf" >&2; exit 1; }

while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        *'# @SITE_READERS@'*) printf '%s' "$readers" ;;
        *'# @TOKEN_CLIENT_IPS@'*) printf '%s' "$ips" ;;
        *"$placeholder"*) printf '%s\n' "${line//$placeholder/\"$owner\"}" ;;
        *) printf '%s\n' "$line" ;;
    esac
done <nginx.conf
