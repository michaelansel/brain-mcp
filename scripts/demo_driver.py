"""Drives a brain-mcp stdio server through the main flow.

Run by scripts/demo.sh with BRAIN_ROOT pointing at a scratch brain.
"""

import asyncio
import json
import os
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


def show(step: str, payload: object) -> None:
    print(f"\n=== {step} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def unwrap(result):
    sc = result.structured_content
    if sc is not None and set(sc) == {"result"}:
        return sc["result"]
    return sc if sc is not None else result.data


async def main() -> None:
    transport = StdioTransport(sys.executable, ["-m", "brain_mcp.cli"], env=dict(os.environ))
    async with Client(transport) as client:
        show("list: brain root", unwrap(await client.call_tool("list", {})))

        show(
            "read: inbox/rocket-idea.md",
            unwrap(await client.call_tool("read", {"path": "inbox/rocket-idea.md"})),
        )

        show(
            "project_create: fork 'rocket' from the inbox note (one commit)",
            unwrap(
                await client.call_tool(
                    "project_create",
                    {
                        "name": "rocket",
                        "summary": "Build a small sounding rocket.",
                        "from_path": "inbox/rocket-idea.md",
                    },
                )
            ),
        )

        show(
            "write: projects/rocket/notes.md",
            unwrap(
                await client.call_tool(
                    "write",
                    {
                        "path": "projects/rocket/notes.md",
                        "content": (
                            "# Notes\n\n## Context\n\nStarting from the inbox idea.\n\n"
                            "## Next steps\n\n- sketch airframe\n"
                        ),
                    },
                )
            ),
        )

        show(
            "edit: extend the step list",
            unwrap(
                await client.call_tool(
                    "edit",
                    {
                        "path": "projects/rocket/notes.md",
                        "old_string": "- sketch airframe\n",
                        "new_string": "- sketch airframe\n- order motor\n- fuel budget\n",
                    },
                )
            ),
        )

        show("search: 'fuel'", unwrap(await client.call_tool("search", {"query": "fuel"})))
        show("projects", unwrap(await client.call_tool("projects", {})))
        rocket = unwrap(await client.call_tool("project", {"name": "rocket"}))
        show("project: rocket (no summary yet — and the sha to claim)", rocket)

        show(
            "write: projects/rocket/summary.md",
            unwrap(
                await client.call_tool(
                    "write",
                    {
                        "path": "projects/rocket/summary.md",
                        "content": (
                            "---\ntitle: rocket\nsurveyed: 2026-08-25\n"
                            "source: projects/rocket\n"
                            # What this summary accounts for. Handed over by
                            # the refresh request; here, the head of the brain
                            # as the summary was written.
                            f"covers: {rocket['summary']['covers']}\n---\n\n"
                            "Sounding rocket. Airframe sketched, motor not ordered.\n"
                        ),
                    },
                )
            ),
        )
        show(
            "read: summary.md while current — nothing to report",
            unwrap(await client.call_tool("read", {"path": "projects/rocket/summary.md"})),
        )

        await client.call_tool(
            "write",
            {"path": "projects/rocket/motor.md", "content": "# Motor\n\nOrdered.\n"},
        )
        show(
            "read: summary.md after a note arrives — the delta, and the ask",
            unwrap(await client.call_tool("read", {"path": "projects/rocket/summary.md"})),
        )

        show(
            "projects(summaries_too=True): what is worth refreshing, and how "
            "much attention each has had",
            unwrap(await client.call_tool("projects", {"summaries_too": True})),
        )


if __name__ == "__main__":
    asyncio.run(main())
