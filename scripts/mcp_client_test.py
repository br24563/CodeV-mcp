"""Launch the server over stdio as an MCP client would, and call its tools.

    .venv\\Scripts\\python.exe scripts\\mcp_client_test.py
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result) -> dict:
    if getattr(result, "structured_content", None):
        return result.structured_content
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    return json.loads(result.content[0].text)


async def main() -> int:
    # Pass the full environment: the SDK's default is a trimmed one, which drops
    # the licence-server variable CODE V needs.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "codev_mcp"], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", ", ".join(t.name for t in tools.tools))

            async def call(name: str, **args):
                out = payload(await session.call_tool(name, args))
                print(f"--- {name} {args or ''}")
                print(json.dumps(out, indent=1, default=str)[:2500])
                return out

            ok = True
            st = await call("session_status")
            ok &= st["running"] is False  # engine must not start with the server
            ok &= (await call("open_lens", path="cv_lens:cooke1"))["ok"]
            ok &= (await call("get_system_summary"))["ok"]
            table = await call("get_surface_table")
            ok &= table["ok"]
            ok &= (await call("run_command", command="thi s2 5.5"))["ok"]
            ok &= (await call("evaluate", expression="(THI S2)")).get("value") == 5.5
            # A bad database item must come back as a failure, not a stale value.
            bad = await call("evaluate", expression="(NOTANITEM)")
            ok &= bad["ok"] is False and "value" not in bad
            ok &= (await call("start_long_command", command="aut; mxc 5; go"))["ok"]
            for _ in range(30):
                poll = await call("poll_long_command", wait_s=2)
                if not poll.get("executing"):
                    break
            ok &= poll["ok"] and not poll.get("executing")
            ok &= (await call("stop_session"))["ok"]
            print("MCP CLIENT TEST", "PASSED" if ok else "FAILED")
            return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
