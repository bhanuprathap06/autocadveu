"""Render docs/feature-status.md from apps/web/src/content/feature-status.json (the single source of truth)."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "apps", "web", "src", "content", "feature-status.json")
OUT = os.path.join(ROOT, "docs", "feature-status.md")
LABEL = {"implemented_and_verified": "Implemented and verified", "implemented_with_limitations": "Implemented with limitations",
         "prototype": "Prototype", "external_integration_required": "External integration required", "planned": "Planned"}


def render() -> str:
    d = json.load(open(SRC))
    lines = ["# Feature status", "", f"_Generated from `apps/web/src/content/feature-status.json` (updated {d['updated']}). "
             "Do not edit by hand: run `python tools/gen_feature_status.py`. The same data drives the in-app /status page._", ""]
    counts: dict[str, int] = {}
    for f in d["features"]:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    lines += ["| Status | Meaning | Count |", "|---|---|---|"]
    lines += [f"| {LABEL[k]} | {v} | {counts.get(k, 0)} |" for k, v in d["statusHelp"].items()]
    area = None
    for f in d["features"]:
        if f["area"] != area:
            area = f["area"]
            lines += ["", f"## {area}", "", "| Capability | Status | Evidence | Limits |", "|---|---|---|---|"]
        cell = lambda s: (s or "").replace("|", "\\|")  # noqa: E731
        lines.append(f"| {cell(f['feature'])} | {LABEL[f['status']]} | {cell(f['evidence'])} | {cell(f.get('limits'))} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        current = open(OUT).read() if os.path.exists(OUT) else ""
        if current != text:
            sys.exit("docs/feature-status.md is out of date: run python tools/gen_feature_status.py")
        print("feature-status.md up to date")
    else:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        open(OUT, "w").write(text)
        print(f"wrote {OUT}")
