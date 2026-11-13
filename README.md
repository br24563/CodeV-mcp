# codev-mcp

Experimental MCP server that lets a coding agent drive CODE V through its COM
API. Code only: it uses your own licensed CODE V install at runtime and ships
no vendor data.

## Requirements

- Windows with CODE V installed and its COM server registered (the installer does this)
- Python 3.11+

## Install

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

## Check the connection

```powershell
.venv\Scripts\python.exe scripts\smoke_test.py
.venv\Scripts\python.exe scripts\mcp_client_test.py
```

The first drives CODE V directly; the second launches the server over stdio
and calls its tools the way an agent would. Both take a licence seat for a few
seconds.

## Register with Claude Code

```powershell
claude mcp add codev -- C:\full\path\to\.venv\Scripts\python.exe -m codev_mcp
```

## Tools

| Tool | Purpose |
|---|---|
| `session_status` | Is a session running (never starts one) |
| `start_session` / `stop_session` | Take and release the licence |
| `run_command` | Run command-line text, return the listing |
| `evaluate` | Evaluate a database item or expression, e.g. `(EFL)` |
| `open_lens` / `save_lens` | Restore or save a lens |
| `get_system_summary` | Title, EFL, f-number, wavelengths, fields |
| `get_surface_table` | Radius, thickness, glass, semi-diameter per surface |
| `get_rms_spot` | RMS spot diameter per field |
| `get_wavefront_error` | RMS wavefront error and Strehl per field, nominal or best focus |
| `get_mtf` | Modulation and diffraction limit per field and frequency, azimuths 0 and 90 |
| `start_long_command` / `poll_long_command` / `stop_long_command` | Optimization and other long runs |

Every tool returns an envelope with `ok`, `errors`, `warnings` and `plots`,
filled from CODE V's COM events, so the agent can tell "ran" from "ran cleanly".

CODE V is started on the first tool call that needs it, not when the server starts.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CODEV_PROGID` | `CodeV.Application` | COM ProgID; set to pick a specific install |
| `CODEV_START_DIR` | `%TEMP%\codev-mcp` | CODE V starting directory (recovery and listing files go here) |

## Licensing note

CODE V finds its licence server through the environment. An MCP client that
launches the server with a trimmed environment (the Python SDK's
`stdio_client` does by default) produces "Cannot connect to license server";
pass the full environment through.

Which install runs is decided by the COM registration of the ProgID, and a
per-user registration (HKCU) overrides the machine-wide one. `start_session`
reports the version that actually answered.

## Layout

- `src/codev_mcp/engine.py` — COM wrapper; one worker thread owns the COM object
- `src/codev_mcp/analyses.py` — typed analyses (RMSSPOT, RMS_1FLD, MTF_1FLD)
- `src/codev_mcp/server.py` — MCP tool definitions
- `scripts/` — connection tests
