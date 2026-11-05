"""Запрет публикации при HIGH/Critical или неполном отчёте Bandit."""

import json
import sys
from pathlib import Path


def main():
    report = json.loads(Path(sys.argv[1]).read_text())
    if report["errors"] or report["metrics"]["_totals"]["loc"] <= 0:
        raise SystemExit("SAST failed: incomplete scan")
    blocking = [finding for finding in report["results"]
                if finding["issue_severity"].upper() in {"HIGH", "CRITICAL"}]
    print(f"SAST: {len(report['results'])} findings; {len(blocking)} blocking")
    raise SystemExit(1 if blocking else 0)


if __name__ == "__main__":
    main()
