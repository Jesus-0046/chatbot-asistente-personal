"""Run ruff (JSON output) and print a compact per-rule summary.

Usage: python tools/ruff_summary.py [targets...]
"""

from __future__ import annotations

import json
import subprocess
import sys

PY = r".\.venv\Scripts\python.exe"
RUFF = r".\.venv\Scripts\ruff.exe"

targets = sys.argv[1:] or ["src/asistente/api"]

proc = subprocess.run(
    [PY, "-m", "ruff", "check", "--output-format=json", *targets],
    capture_output=True,
    text=True,
)
data = json.loads(proc.stdout or "[]")
rules = {}
by_code_line = {}
for item in data:
    code = item["code"]
    msg = item["message"]
    rules[code] = rules.get(code, 0) + 1
    if code == "B008":
        cell = "Depends" if "``Depends``" in msg else ("Query" if "``Query``" in msg else "other")
        by_code_line[cell] = by_code_line.get(cell, 0) + 1

print("EXIT:", proc.returncode, "| total:", len(data))
for code, n in sorted(rules.items(), key=lambda kv: (-kv[1], kv[0])):
    print(f"  {code}: {n}")
if by_code_line:
    print("B008 breakdown:", ", ".join(f"{k}={v}" for k, v in sorted(by_code_line.items())))
