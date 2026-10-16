"""Specification comparison with explicit provenance and uncertainty.

Rules
-----
* Values are compared only after unit normalisation; incompatible units are
  reported as ``incomparable`` rather than coerced.
* Missing values are shown as missing, never filled in.
* "Dimensional match" is reported only when items share category and standard
  designation and all key boundary dimensions agree. That is NOT an
  interchangeability approval: ratings, materials and tolerances must still be
  verified. Safety-critical items always carry an engineering-review warning.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .seed import EXPECTED_SPECS

LENGTH = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "um": 0.001}
MASS = {"kg": 1.0, "g": 0.001, "lb": 0.45359237}
FORCE = {"N": 1.0, "kN": 1000.0, "lbf": 4.4482216}
SPEED = {"rpm": 1.0, "r/min": 1.0}
CAPACITY = {"Ah": 1.0, "mAh": 0.001}
GROUPS = [LENGTH, MASS, FORCE, SPEED, CAPACITY]
KEY_DIMS = {"bearing": ["bore_d", "outer_d", "width"], "fastener": ["thread", "nominal_length"],
            "nut": ["thread", "width_across_flats", "height"], "washer": ["inner_d", "outer_d", "thickness"],
            "battery_cell": ["diameter", "length", "chemistry", "nominal_capacity", "nominal_voltage"]}


def normalise(value: Any, unit: str | None) -> tuple[Any, str | None, str | None]:
    """Return (value_in_base_unit, base_unit, group_name)."""
    if unit is None or not isinstance(value, (int, float)):
        return value, unit, None
    for g in GROUPS:
        if unit in g:
            base = next(k for k, v in g.items() if v == 1.0)
            return value * g[unit], base, base
    return value, unit, unit


def compare(items: list[dict[str, Any]], rel_tol: float = 1e-6) -> dict[str, Any]:
    cats = sorted({i["category"] for i in items})
    keys: list[str] = []
    for c in cats:
        keys += [k for k in EXPECTED_SPECS.get(c, []) if k not in keys]
    for i in items:
        keys += [k for k in i.get("specs", {}) if k not in keys]
    rows = []
    for k in keys:
        cells, normed = [], []
        for i in items:
            sp = (i.get("specs") or {}).get(k)
            if k == "mass" and (sp is None or sp.get("value") is None) and i.get("mass_kg") is not None:
                sp = {"value": i["mass_kg"], "unit": "kg", "basis": "record", "source": i.get("mass_source"),
                      "verified": i.get("mass_confidence") == "verified"}
            if sp is None or sp.get("value") is None:
                cells.append({"value": None, "status": "missing", "note": (sp or {}).get("note")})
                normed.append(None)
                continue
            v, u, g = normalise(sp["value"], sp.get("unit"))
            cells.append({"value": sp["value"], "unit": sp.get("unit"), "basis": sp.get("basis"), "source": sp.get("source"),
                          "verified": bool(sp.get("verified")), "note": sp.get("note", ""), "normalised": v, "base_unit": u})
            normed.append((v, g))
        present = [n for n in normed if n is not None]
        if not present:
            status = "all_missing"
        else:
            groups = {g for _, g in present}
            if len(groups) > 1:
                status = "incomparable_units"
            else:
                vals = [v for v, _ in present]
                if all(isinstance(v, (int, float)) for v in vals):
                    lo, hi = min(vals), max(vals)
                    same = abs(hi - lo) <= rel_tol * max(abs(hi), abs(lo), 1e-12)
                else:
                    same = len({str(v) for v in vals}) == 1
                status = "equal" if same else "differs"
                if len(present) < len(items):
                    status += "_partial"
        unverified = any(c.get("value") is not None and not c.get("verified") for c in cells)
        row: dict[str, Any] = {"key": k, "status": status, "cells": cells, "has_unverified": unverified}
        nums = [v for v, _ in present if isinstance(v, (int, float))]
        if len(nums) >= 2 and status.startswith("differs"):
            row["spread"] = {"min": min(nums), "max": max(nums),
                             "relative": (max(nums) - min(nums)) / max(abs(max(nums)), 1e-12)}
        rows.append(row)
    assessments = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            assessments.append(assess_pair(items[a], items[b], rows, a, b))
    warnings = []
    if len(cats) > 1:
        warnings.append("Items belong to different categories; the comparison is informational only.")
    if any(i.get("safety_critical") for i in items):
        warnings.append("At least one item is safety-critical. Any substitution requires independent engineering verification "
                        "and, where applicable, homologation.")
    missing_summary = {r["key"]: [items[j]["name"] for j, c in enumerate(r["cells"]) if c.get("value") is None]
                       for r in rows if any(c.get("value") is None for c in r["cells"])}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [{"id": i["id"], "name": i["name"], "category": i["category"], "manufacturer": i.get("manufacturer"),
                   "part_number": i.get("part_number"), "standard": i.get("standard"), "asset_class": i.get("asset_class"),
                   "verification_status": i.get("verification_status"), "source_url": i.get("source_url"),
                   "source_retrieved_at": i.get("source_retrieved_at"), "license": i.get("license"),
                   "completeness": i.get("completeness"), "safety_critical": i.get("safety_critical")} for i in items],
        "rows": rows,
        "assessments": assessments,
        "missing": missing_summary,
        "warnings": warnings,
        "caveats": [
            "Values marked unverified are nominal or derived and must be confirmed against an authoritative source.",
            "Missing values are not estimated. Obtain them from a cited manufacturer datasheet before relying on a comparison.",
            "A dimensional match is not an interchangeability approval.",
        ],
    }


def assess_pair(a: dict[str, Any], b: dict[str, Any], rows: list[dict[str, Any]], ia: int, ib: int) -> dict[str, Any]:
    reasons: list[str] = []
    verdict = "not_established"
    if a["category"] != b["category"]:
        reasons.append("Different categories")
    else:
        keydims = KEY_DIMS.get(a["category"], [])
        byk = {r["key"]: r for r in rows}
        diffs, miss = [], []
        for k in keydims:
            r = byk.get(k)
            if r is None:
                miss.append(k)
                continue
            ca, cb = r["cells"][ia], r["cells"][ib]
            if ca.get("value") is None or cb.get("value") is None:
                miss.append(k)
            elif ca.get("normalised") != cb.get("normalised") and str(ca.get("value")) != str(cb.get("value")):
                if isinstance(ca.get("normalised"), (int, float)) and isinstance(cb.get("normalised"), (int, float)) and \
                        abs(ca["normalised"] - cb["normalised"]) <= 1e-6 * max(abs(ca["normalised"]), 1):
                    continue
                diffs.append(k)
        if diffs:
            reasons.append("Key dimensions differ: " + ", ".join(diffs))
            verdict = "not_interchangeable_dimensionally"
        elif miss:
            reasons.append("Insufficient data for: " + ", ".join(miss))
        else:
            verdict = "dimensional_match"
            reasons.append("Key dimensions agree" + (f" (both per {a['standard']})" if a.get("standard") and
                                                     a.get("standard") == b.get("standard") else ""))
            perf = [r["key"] for r in rows if r["key"] not in keydims and r["status"] in ("all_missing", "differs",
                                                                                          "differs_partial", "equal_partial")]
            if perf:
                reasons.append("Performance/other data missing or different: " + ", ".join(perf))
    if a.get("safety_critical") or b.get("safety_critical"):
        reasons.append("Safety-critical: independent engineering verification required")
    return {"a": a["id"], "b": b["id"], "verdict": verdict, "reasons": reasons,
            "is_interchangeability_approval": False}


def report_markdown(cmp: dict[str, Any]) -> str:
    lines = [f"# Component comparison report", "", f"Generated: {cmp['generated_at']} (UTC)", ""]
    lines += ["## Items", "", "| # | Name | Class | Verification | Source | Retrieved | Licence |", "|---|---|---|---|---|---|---|"]
    for n, i in enumerate(cmp["items"], 1):
        lines.append(f"| {n} | {i['name']} | {i['asset_class']} | {i['verification_status']} | {i['source_url'] or 'n/a'} | "
                     f"{i['source_retrieved_at'] or 'n/a'} | {i.get('license') or 'n/a'} |")
    lines += ["", "## Specifications", "", "| Spec | " + " | ".join(f"#{n}" for n in range(1, len(cmp['items']) + 1)) + " | Status |",
              "|---|" + "---|" * (len(cmp["items"]) + 1)]
    for r in cmp["rows"]:
        cells = []
        for c in r["cells"]:
            if c.get("value") is None:
                cells.append("**missing**")
            else:
                cells.append(f"{c['value']}{' ' + c['unit'] if c.get('unit') else ''}{'' if c.get('verified') else ' (unverified)'}")
        lines.append(f"| {r['key']} | " + " | ".join(cells) + f" | {r['status']} |")
    lines += ["", "## Assessments", ""]
    for a in cmp["assessments"]:
        lines.append(f"- {a['a']} vs {a['b']}: **{a['verdict']}** - " + "; ".join(a["reasons"]))
    lines += ["", "## Warnings and caveats", ""] + [f"- {w}" for w in cmp["warnings"] + cmp["caveats"]]
    lines += ["", "_This report does not certify fitness for purpose. It is not a substitute for engineering review._"]
    return "\n".join(lines) + "\n"
