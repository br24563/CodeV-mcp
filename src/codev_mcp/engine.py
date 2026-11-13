"""Thin, thread-safe wrapper around the CODE V COM object.

All COM calls run on one dedicated worker thread, because the MCP server
dispatches tool calls from a thread pool and a COM proxy should stay on the
thread that created it.

Every public method returns a plain dict "envelope":

    {"ok": bool, ..., "errors": [...], "warnings": [...], "plots": [...]}

``errors`` and ``warnings`` are the messages CODE V raised while the call ran
(collected from the COM events), so a caller can tell a command that ran from
a command that ran cleanly. Nothing here raises for an engine-side failure.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

DEFAULT_PROGID = "CodeV.Application"
MAX_OUTPUT_CHARS = 20_000


class _EventLog:
    """Collects messages delivered through the ICVApplicationEvents interface."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.plots: list[str] = []

    def add(self, kind: str, text: str) -> None:
        with self._lock:
            getattr(self, kind).append(str(text).strip())

    def drain(self) -> dict[str, list[str]]:
        with self._lock:
            out = {
                "errors": self.errors,
                "warnings": self.warnings,
                "plots": self.plots,
            }
            self.errors, self.warnings, self.plots = [], [], []
        return out


def _make_handler(log: _EventLog) -> type:
    class Handler:
        def OnLicenseError(self, error):  # noqa: N802 (COM event name)
            log.add("errors", f"License error: {error}")

        def OnCodeVError(self, error):  # noqa: N802
            log.add("errors", error)

        def OnCodeVWarning(self, warning):  # noqa: N802
            log.add("warnings", warning)

        def OnPlotReady(self, filename, plotwindow):  # noqa: N802
            log.add("plots", filename)

    return Handler


def _clip(text: Any) -> tuple[str, bool]:
    text = "" if text is None else str(text)
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS], True


class CodeVEngine:
    def __init__(self, progid: str | None = None, start_dir: str | None = None) -> None:
        self.progid = progid or os.environ.get("CODEV_PROGID") or DEFAULT_PROGID
        self.start_dir = (
            start_dir
            or os.environ.get("CODEV_START_DIR")
            or os.path.join(tempfile.gettempdir(), "codev-mcp")
        )
        self._cv: Any = None
        self._events_attached = False
        self._log = _EventLog()
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="codev-com", initializer=self._init_thread
        )

    # -- worker-thread plumbing -------------------------------------------

    @staticmethod
    def _init_thread() -> None:
        import pythoncom

        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)

    def _run(self, fn: Callable[[], dict]) -> dict:
        """Run ``fn`` on the COM thread and attach the events it produced."""

        def call() -> dict:
            self._log.drain()
            try:
                result = fn()
            except Exception as exc:  # com_error or anything else
                result = {"ok": False, "exception": _describe(exc)}
            events = self._log.drain()
            if events["errors"] or "exception" in result:
                result["ok"] = False
            result.setdefault("ok", True)
            result.update(events)
            return result

        return self._pool.submit(call).result()

    def _ensure_started(self) -> None:
        """Start CODE V if needed. Call only from the COM thread."""
        if self._cv is not None:
            return
        import win32com.client as wc

        os.makedirs(self.start_dir, exist_ok=True)
        try:
            cv = wc.DispatchWithEvents(self.progid, _make_handler(self._log))
            self._events_attached = True
        except Exception:
            # Events need a generated type-library wrapper; fall back without them.
            cv = wc.Dispatch(self.progid)
            self._events_attached = False
        cv.StartingDirectory = self.start_dir
        cv.StartCodeV()
        # StartCodeV returns even when the licence checkout failed, leaving a
        # session that silently ignores commands. Probe it before trusting it.
        try:
            cv.EvaluateExpression("(CVV)")
            healthy = not self._log.errors
        except Exception:
            healthy = False
        if not healthy:
            try:
                cv.StopCodeV()
            except Exception:
                pass
            raise RuntimeError(
                "CODE V started but is not usable (see errors; usually a licence problem)."
            )
        self._cv = cv

    # -- session -----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._cv is not None

    def start(self) -> dict:
        def fn() -> dict:
            self._ensure_started()
            return {
                "version": str(self._cv.CodeVVersion),
                "progid": self.progid,
                "starting_directory": self.start_dir,
                "events_attached": self._events_attached,
            }

        return self._run(fn)

    def stop(self) -> dict:
        def fn() -> dict:
            if self._cv is None:
                return {"stopped": False, "note": "No session was running."}
            try:
                self._cv.StopCodeV()
            finally:
                self._cv = None
            return {"stopped": True}

        return self._run(fn)

    # -- commands and expressions -------------------------------------------

    def command(self, command: str, timeout_s: float | None = None) -> dict:
        def fn() -> dict:
            self._ensure_started()
            if timeout_s:
                self._cv.CommandTimeout = int(timeout_s * 1000)
            output, clipped = _clip(self._cv.Command(command))
            return {"command": command, "output": output, "output_truncated": clipped}

        return self._run(fn)

    def evaluate(self, expression: str) -> dict:
        def fn() -> dict:
            self._ensure_started()
            value = _coerce(self._cv.EvaluateExpression(expression))
            if self._log.errors:  # stale value from a failed evaluation
                return {"expression": expression}
            return {"expression": expression, "value": value}

        return self._run(fn)

    def evaluate_many(self, expressions: dict[str, str]) -> dict:
        """Evaluate several named expressions in one round trip to the COM thread."""

        def fn() -> dict:
            self._ensure_started()
            values: dict[str, Any] = {}
            failed: list[str] = []
            for name, expr in expressions.items():
                seen = len(self._log.errors)
                try:
                    value = _coerce(self._cv.EvaluateExpression(expr))
                except Exception as exc:
                    values[name] = {"error": _describe(exc)}
                    failed.append(name)
                    continue
                # A bad database item does not raise: CODE V reports an error
                # event and hands back a stale value. Never pass that value on.
                new_errors = self._log.errors[seen:]
                if new_errors:
                    values[name] = {"error": "; ".join(new_errors), "expression": expr}
                    failed.append(name)
                else:
                    values[name] = value
            result: dict[str, Any] = {"values": values}
            if failed:
                result["ok"] = False
                result["failed"] = failed
            return result

        return self._run(fn)

    def counts(self) -> dict:
        def fn() -> dict:
            self._ensure_started()
            cv = self._cv
            return {
                "surfaces": int(cv.SurfaceCount),
                "fields": int(cv.FieldCount),
                "wavelengths": int(cv.WavelengthCount),
                "zooms": int(cv.ZoomCount),
                "stop_surface": int(cv.StopSurface),
            }

        return self._run(fn)

    # -- long-running commands ---------------------------------------------

    def async_start(self, command: str) -> dict:
        def fn() -> dict:
            self._ensure_started()
            if self._cv.IsExecutingCommand:
                return {"ok": False, "note": "A command is already executing."}
            self._cv.AsyncCommand(command)
            return {"command": command, "started": True}

        return self._run(fn)

    def async_poll(self, wait_ms: int = 1000) -> dict:
        def fn() -> dict:
            self._ensure_started()
            if self._cv.IsExecutingCommand:
                self._cv.Wait(int(wait_ms))
            if self._cv.IsExecutingCommand:
                return {"executing": True}
            output, clipped = _clip(self._cv.CommandOutput)
            return {"executing": False, "output": output, "output_truncated": clipped}

        return self._run(fn)

    def async_stop(self) -> dict:
        def fn() -> dict:
            self._ensure_started()
            self._cv.StopCommand()
            return {"stop_requested": True}

        return self._run(fn)


def _coerce(value: Any) -> Any:
    """EvaluateExpression returns text; hand back a number when it is one."""
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        return text


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


if sys.platform != "win32":  # pragma: no cover
    raise ImportError("codev_mcp.engine needs Windows: CODE V's API is COM-only.")
