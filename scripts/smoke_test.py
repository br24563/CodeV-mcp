"""Start CODE V over COM, load a sample lens, read a few values, shut down.

Run with the environment's interpreter:

    .venv\\Scripts\\python.exe scripts\\smoke_test.py

It takes a CODE V licence seat for a few seconds.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codev_mcp.engine import CodeVEngine  # noqa: E402


def show(label: str, result: dict) -> None:
    print(f"--- {label}")
    print(json.dumps(result, indent=2, default=str))


def main() -> int:
    eng = CodeVEngine()
    failed = False
    try:
        r = eng.start()
        show("start", r)
        if not r["ok"]:
            return 1

        r = eng.command("res cv_lens:cooke1")
        show("restore cooke1", r)
        failed |= not r["ok"]

        show("counts", eng.counts())
        r = eng.evaluate_many(
            {
                "title": "(TIT)",
                "efl": "(EFL)",
                "fno": "(FNO)",
                "num_surfaces": "(NUM S)",
                "rdy_s1": "(RDY S1)",
                "thi_s1": "(THI S1)",
                "gla_s1": "(GLA S1)",
                "sd_s1": "(SD S1)",
                "wl_w1": "(WL W1)",
                "yan_f2": "(YAN F2)",
            }
        )
        show("evaluate", r)
        failed |= not r["ok"]

        # A deliberately bad command: the envelope should come back ok=False.
        r = eng.command("thisisnotacommand")
        show("bad command (expect ok=false)", r)
        failed |= r["ok"]
    finally:
        show("stop", eng.stop())
    print("SMOKE TEST", "FAILED" if failed else "PASSED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
