#!/usr/bin/env bash
# Builds a static, read-only website of the brain and publishes it atomically.
#
# Meant to be run by brain-mcp after every commit (BRAIN_MCP_POST_COMMIT_CMD),
# and safe to run by hand. It builds from `git archive` of HEAD rather than the
# working tree, so a half-written or uncommitted file never reaches the site,
# and it swaps a symlink at the end, so the web server never sees a partial
# build. Nothing in the brain is changed or needs to change for this to work.
#
#   BRAIN_ROOT      the brain repo (required)
#   BRAIN_SITE_DIR  where to publish; the web server serves $BRAIN_SITE_DIR/current
set -euo pipefail
umask 022

: "${BRAIN_ROOT:?BRAIN_ROOT is not set}"
site_dir="${BRAIN_SITE_DIR:-/var/www/brain-site}"
repo="$(cd "$(dirname "$0")/.." && pwd)"

sha="$(git -C "$BRAIN_ROOT" rev-parse HEAD)"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
release="releases/$(date -u +%Y%m%dT%H%M%SZ)-${sha:0:12}-$$"

mkdir -p "$site_dir/releases"
work="$(mktemp -d "$site_dir/work.XXXXXX")"
trap 'rm -rf "$work"' EXIT

mkdir "$work/docs"
git -C "$BRAIN_ROOT" archive "$sha" | tar -x -C "$work/docs"
cp "$repo/site/mkdocs.yml" "$work/mkdocs.yml"
cp "$repo/site/nav.yml" "$work/docs/.nav.yml"
cp "$repo/site/extra.css" "$work/docs/extra.css"

# A landing page, and a page for each folder: titles rather than paths, the
# notes that changed most recently, and the projects active this week. A page
# the brain already has is kept.
uv run --quiet --project "$repo" --frozen python "$repo/scripts/site_pages.py" "$work/docs" "$BRAIN_ROOT" "$sha" "$now"

# Material prints a banner about MkDocs 2.0 on every build; this is pinned to 1.x.
NO_MKDOCS_2_WARNING=1 uvx --from mkdocs==1.6.1 --with mkdocs-material==9.7.7 --with mkdocs-awesome-nav==3.3.0 mkdocs build --quiet -f "$work/mkdocs.yml" -d "$site_dir/$release"

# rename(2) over the old symlink is atomic; `ln -sfn` alone is not.
ln -s "$release" "$work/current"
python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$work/current" "$site_dir/current"

# Keep the three newest releases. The one just published is the newest.
ls -1dt "$site_dir"/releases/*/ | tail -n +4 | while read -r old; do rm -rf "$old"; done

echo "published $sha to $site_dir/$release"
