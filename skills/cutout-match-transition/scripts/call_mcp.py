"""Call the final-cut-pro MCP server's tools over stdio when it is not loaded in the session.

    call_mcp.py <fcp-mcp folder> <tool> '<json args>' [<tool> '<json args>' ...]
    call_mcp.py <fcp-mcp folder> fcpxml_build_timeline @spec.json      # @file reads args from a file

Prints each result block in full. Run with the server's own environment, kept outside the skill folder:
    UV_PROJECT_ENVIRONMENT=~/.cache/cutout-skill-fcp-mcp-venv \
        uv run --directory <fcp-mcp folder> python <this file> <fcp-mcp folder> ...
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main(server_dir: str, pairs: list[str]) -> int:
    # The MCP client passes only a minimal environment to the server; hand over the venv location so
    # uv doesn't create a .venv inside the (skill-bundled) server folder.
    venv = os.environ.get("UV_PROJECT_ENVIRONMENT", os.path.expanduser("~/.cache/cutout-skill-fcp-mcp-venv"))
    params = StdioServerParameters(command="uv", args=["run", "-q", "--directory", server_dir, "fcp-mcp"],
                                   env={**os.environ, "UV_PROJECT_ENVIRONMENT": venv})
    failed = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for name, raw in zip(pairs[::2], pairs[1::2], strict=True):
                args = json.load(open(raw[1:])) if raw.startswith("@") else json.loads(raw)
                result = await session.call_tool(name, args)
                failed += bool(result.is_error)
                for block in result.content:
                    print(f"== {name} error={result.is_error}")
                    print(getattr(block, "text", f"<{block.type}>"))
    return failed


if __name__ == "__main__":
    if len(sys.argv) < 4 or len(sys.argv) % 2:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2:])))
