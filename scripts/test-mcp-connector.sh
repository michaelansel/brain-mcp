#!/usr/bin/env bash
# Proves the Anthropic Messages API "MCP connector" can drive the deployed
# brain-mcp server: mints a short-lived exe.dev VM token, then asks Claude
# (via the API) to call the brain's `list` tool through the connector.
#
# Usage: ANTHROPIC_API_KEY=sk-ant-... scripts/test-mcp-connector.sh
set -euo pipefail

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY (from platform.claude.com)}"

echo "Minting 1-day exe.dev VM token (1Password may prompt)..."
TOKEN=$(ssh exe.dev ssh-key generate-api-key --vm=brain-mcp --exp=1d --label=mcp-connector-test \
  | grep -o 'exe1\.[A-Za-z0-9_-]*' | head -1)

echo "Calling Messages API with the MCP connector..."
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: mcp-client-2025-11-20" \
  -H "content-type: application/json" \
  -d @- <<EOF | python3 -m json.tool
{
  "model": "claude-opus-4-8",
  "max_tokens": 1024,
  "mcp_servers": [
    {
      "type": "url",
      "url": "https://brain-mcp.exe.xyz/mcp",
      "name": "brain",
      "authorization_token": "$TOKEN"
    }
  ],
  "tools": [
    { "type": "mcp_toolset", "mcp_server_name": "brain" }
  ],
  "messages": [
    {
      "role": "user",
      "content": "Use the brain's list tool to show what is in the root of the brain, then summarize in one sentence."
    }
  ]
}
EOF

echo "Cleaning up test token..."
ssh exe.dev ssh-key remove mcp-connector-test
echo "Done."
