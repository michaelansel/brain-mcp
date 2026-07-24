#!/usr/bin/env bash
# Creates a scratch brain, starts brain-mcp over stdio, and exercises the
# main flow: fork a project from an inbox note, write, patch, search.
set -euo pipefail
cd "$(dirname "$0")/.."

BRAIN="$(mktemp -d)/brain"
mkdir -p "$BRAIN/inbox"

cat > "$BRAIN/agents.md" <<'EOF'
# Conventions

Notes start in inbox/. Promote them to projects/ when they grow legs.
EOF

cat > "$BRAIN/inbox/rocket-idea.md" <<'EOF'
# Rocket idea

Build a small sounding rocket. Needs an airframe, a motor, and a fuel budget.
EOF

git -C "$BRAIN" init -q
git -C "$BRAIN" add -A
git -C "$BRAIN" -c user.name=demo -c user.email=demo@localhost commit -qm "seed brain"

echo "Scratch brain: $BRAIN"

BRAIN_ROOT="$BRAIN" uv run python scripts/demo_driver.py

echo
echo "=== git log in the brain (one commit per mutating call) ==="
git -C "$BRAIN" log --oneline --stat
