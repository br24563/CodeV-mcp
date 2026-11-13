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

            # Typed analyses on the unmodified Cooke triplet.
            spot = await call("get_rms_spot")
            ok &= spot["ok"] and len(spot["results"]) == 3
            # Manual's worked example: RMSSPOT(1,1,1,.01) on cooke1 = 0.008562
            ok &= abs(spot["results"][0]["rms_spot_diameter"] - 0.00856) < 2e-4
            wfe = await call("get_wavefront_error")
            ok &= wfe["ok"] and all(0 < r["strehl"] <= 1 for r in wfe["results"])
            best = await call("get_wavefront_error", best_focus=True, fields=[1])
            ok &= best["ok"] and best["results"][0]["rms_waves"] <= wfe["results"][0]["rms_waves"]
            m = await call("get_mtf", frequencies=[20, 40], fields=[1, 3])
            ok &= m["ok"] and len(m["results"]) == 4
            ok &= all(
                0 <= r[a]["modulation"] <= r[a]["diffraction_limit"] + 1e-6 <= 1 + 1e-6
                for r in m["results"]
                for a in ("azimuth_0", "azimuth_90")
            )
            # On axis the two azimuths must agree for a rotationally symmetric lens.
            ok &= abs(m["results"][0]["azimuth_0"]["modulation"] - m["results"][0]["azimuth_90"]["modulation"]) < 1e-6
            # Bad inputs must fail, not return numbers.
            ok &= (await call("get_rms_spot", fields=[9]))["ok"] is False
            ok &= (await call("get_mtf", frequencies=[], fields=[1]))["ok"] is False

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
