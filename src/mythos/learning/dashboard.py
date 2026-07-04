"""Render a lightweight dataset operations dashboard."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def render_dashboard(report: dict[str, Any]) -> str:
    crawl = report.get("crawl", {})
    source = report.get("source_registry", {})
    contamination = report.get("contamination_report") or {}
    tasks = report.get("task_report") or {}
    uploaded = report.get("uploaded_objects") or []
    rows = [
        ("Status", report.get("status", "unknown")),
        ("Dataset", report.get("dataset_id", "")),
        ("Version", report.get("version_id", "")),
        ("Sources", source.get("source_count", 0)),
        ("Seed URLs", source.get("url_count", 0)),
        ("Fetched", crawl.get("fetched", 0)),
        ("Accepted", crawl.get("accepted", 0)),
        ("Rejected", crawl.get("rejected", 0)),
        ("Duplicates", crawl.get("duplicate", 0)),
        ("Contamination Hits", len(contamination.get("hits", []))),
        ("Extracted Tasks", tasks.get("extracted", 0)),
        ("Uploaded Objects", len(uploaded)),
    ]
    table = "\n".join(f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    warnings = source.get("warnings", [])
    warning_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in warnings) or "<li>None</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mythos Data Platform Dashboard</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; color: #17202a; }}
    table {{ border-collapse: collapse; width: min(900px, 100%); }}
    th, td {{ border: 1px solid #d6dbdf; padding: 10px 12px; text-align: left; }}
    th {{ width: 240px; background: #f4f6f7; }}
    code {{ background: #f4f6f7; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>Mythos Data Platform Dashboard</h1>
  <table>{table}</table>
  <h2>Registry Warnings</h2>
  <ul>{warning_html}</ul>
</body>
</html>
"""


def write_dashboard(report: dict[str, Any], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dashboard(report), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Mythos data pipeline dashboard HTML.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    print(write_dashboard(report, args.output))


if __name__ == "__main__":
    main()
