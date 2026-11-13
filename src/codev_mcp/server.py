"""MCP server exposing CODE V to a coding agent.

CODE V is not started when the server starts. The first tool call that needs
the engine launches it (and takes a licence seat); ``stop_session`` releases it.
"""

from __future__ import annotations

import os
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from codev_mcp.engine import CodeVEngine

INSTRUCTIONS = """\
Tools for driving a CODE V optical design session.

Every tool returns an envelope with `ok`, `errors` and `warnings`. CODE V
often accepts a command and only prints a warning, so a call that returned is
not proof it did what you meant: check `ok`, read `warnings`, and confirm an
edit by reading the value back with `evaluate` or `get_surface_table`.

Commands are CODE V command-line syntax; separate several with `;`. Values are
read with database items in parentheses, e.g. `(EFL)`, `(THI S2)`, `(GLA S1)`.
Surface 0 is the object and the last surface is the image.

Use `run_command` for anything that finishes in seconds. Use
`start_long_command` then `poll_long_command` for optimization (AUT),
tolerancing (TOR) and other long runs.
"""

mcp = MCPServer("codev", instructions=INSTRUCTIONS)
engine = CodeVEngine()


# -- session --------------------------------------------------------------


@mcp.tool()
def session_status() -> dict[str, Any]:
    """Report whether a CODE V session is running. Never starts one."""
    return {
        "ok": True,
        "running": engine.running,
        "progid": engine.progid,
        "starting_directory": engine.start_dir,
    }


@mcp.tool()
def start_session() -> dict[str, Any]:
    """Start CODE V (takes a licence seat) and report its version.

    Other tools start it on demand, so this is only needed to check the
    connection or licence up front.
    """
    return engine.start()


@mcp.tool()
def stop_session() -> dict[str, Any]:
    """Stop CODE V and release the licence. Unsaved lens changes are lost."""
    return engine.stop()


# -- commands and values ----------------------------------------------------


@mcp.tool()
def run_command(command: str, timeout_s: float = 120.0) -> dict[str, Any]:
    """Run CODE V command-line text and return its listing output.

    Several commands may be joined with `;`. `ok` is false if CODE V raised
    an error while it ran.
    """
    return engine.command(command, timeout_s)


@mcp.tool()
def evaluate(expression: str) -> dict[str, Any]:
    """Evaluate a CODE V expression, e.g. `(EFL)`, `(THI S3)`, `(RDY S1)*2`.

    Returns a number when the result is numeric, otherwise text.
    """
    return engine.evaluate(expression)


# -- lens -----------------------------------------------------------------


@mcp.tool()
def open_lens(path: str) -> dict[str, Any]:
    """Restore a lens file and read back its title and first-order data.

    `path` is a file path or a CODE V library reference such as
    `cv_lens:cooke1`. The current lens is replaced.
    """
    target = path if ":" in path and not os.path.isabs(path) else f'"{path}"'
    result = engine.command(f"res {target}")
    if result["ok"]:
        result["summary"] = _summary_values()
    return result


@mcp.tool()
def save_lens(path: str) -> dict[str, Any]:
    """Save the current lens to `path` (a .len file)."""
    return engine.command(f'sav "{path}"')


@mcp.tool()
def get_system_summary() -> dict[str, Any]:
    """Title, focal length, f-number, counts, wavelengths and field angles."""
    counts = engine.counts()
    if not counts["ok"]:
        return counts
    exprs = {
        "title": "(TIT)",
        "efl": "(EFL)",
        "fno": "(FNO)",
        "epd": "(EPD)",
        "overall_length": "(OAL)",
    }
    for w in range(1, counts["wavelengths"] + 1):
        exprs[f"wavelength_nm_{w}"] = f"(WL W{w})"
    for f in range(1, counts["fields"] + 1):
        exprs[f"field_y_angle_{f}"] = f"(YAN F{f})"
    result = engine.evaluate_many(exprs)
    result["counts"] = {k: counts[k] for k in ("surfaces", "fields", "wavelengths", "zooms", "stop_surface")}
    return result


@mcp.tool()
def get_surface_table() -> dict[str, Any]:
    """Radius, thickness, glass and semi-diameter for every surface.

    Surface 0 is the object and the last row is the image. A radius of 1e18
    means flat, a thickness of 1e10 means infinite, and an empty glass is air.
    """
    counts = engine.counts()
    if not counts["ok"]:
        return counts
    n = counts["surfaces"]
    exprs: dict[str, str] = {}
    for s in range(n):
        name = "SO" if s == 0 else ("SI" if s == n - 1 else f"S{s}")
        exprs[f"{s}.radius"] = f"(RDY {name})"
        exprs[f"{s}.thickness"] = f"(THI {name})"
        exprs[f"{s}.glass"] = f"(GLA {name})"
        exprs[f"{s}.semi_diameter"] = f"(SD {name})"
    result = engine.evaluate_many(exprs)
    values = result.pop("values", {})
    rows = []
    for s in range(n):
        rows.append(
            {
                "surface": s,
                "is_stop": s == counts["stop_surface"],
                "radius": values.get(f"{s}.radius"),
                "thickness": values.get(f"{s}.thickness"),
                "glass": values.get(f"{s}.glass"),
                "semi_diameter": values.get(f"{s}.semi_diameter"),
            }
        )
    result["surfaces"] = rows
    return result


# -- long-running commands ----------------------------------------------------


@mcp.tool()
def start_long_command(command: str) -> dict[str, Any]:
    """Start a long-running command (e.g. `AUT; ...; GO`) without waiting.

    Follow with `poll_long_command` until `executing` is false.
    """
    return engine.async_start(command)


@mcp.tool()
def poll_long_command(wait_s: float = 5.0) -> dict[str, Any]:
    """Wait up to `wait_s` seconds for the long command, then report.

    Returns `executing: true` while it runs, or the listing output when done.
    """
    return engine.async_poll(int(max(0.0, min(wait_s, 30.0)) * 1000))


@mcp.tool()
def stop_long_command() -> dict[str, Any]:
    """Ask CODE V to halt the running long command."""
    return engine.async_stop()


def _summary_values() -> dict[str, Any]:
    return engine.evaluate_many(
        {"title": "(TIT)", "efl": "(EFL)", "fno": "(FNO)", "image_surface": "(NUM S)"}
    ).get("values", {})


def main() -> None:
    try:
        mcp.run()
    finally:
        # Release the licence when the client disconnects. Done here rather
        # than in atexit, which runs after the COM worker thread is gone.
        if engine.running:
            engine.stop()


if __name__ == "__main__":
    main()
