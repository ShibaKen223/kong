"""Turn selftest JSON reports into GitHub Actions annotations (readable via the checks API).

Exits 1 when a report is missing or a required check failed, so this step decides the job.

    python tools/ci_annotate.py selftest-source.json selftest-frozen.json
"""

import json
import sys

SOFT = {"clipboard", "foreground", "gpu"}  # depend on the runner, not on Kong


def esc(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


errors, notices, failed = [], [], False
for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError) as e:
        errors.append(f"::error title={path}::could not read report: {esc(str(e))}")
        failed = True
        continue
    for name, c in report.get("checks", {}).items():
        if c.get("ok"):
            notices.append(f"::notice title={path} {name}::{esc(str(c.get('value'))[:300])} ({c.get('s')}s)")
        else:
            detail = c.get("error", "") + "\n" + c.get("trace", "")[-1200:]
            errors.append(f"::error title={path} {name}::{esc(detail)}")
            failed = failed or name not in SOFT
# GitHub keeps only the first 10 annotations of each kind per step: errors first.
print("\n".join(errors + notices))
sys.exit(1 if failed else 0)
