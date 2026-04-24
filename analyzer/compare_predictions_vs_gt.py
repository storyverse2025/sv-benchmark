"""
Compare VLM predictions against metrics_ground_truth.json.

Input contract (matches vlm_system_prompt.md output contract):
    Every prediction is a JSON object where each metric maps to List[str],
    mirroring the GT's `gt_values: List[str]` shape, so we can compute
    differences by pure set operations.

Accepted prediction file shapes:
    1.  Bare dict (single testcase, no testcase_id embedded):
            { "style": ["surrealist"], "scenes": ["theater stage"], ... }
        → requires --testcase-id on the CLI.

    2.  Dict keyed by testcase_id:
            { "S1-1-...": { "style": [...], ... },
              "S2-41-...": { "style": [...], ... } }

    3.  List of records:
            [ { "testcase_id": "S1-1-...", "predictions": { ... } }, ... ]

Per-metric status (using set semantics on gt_values vs pred_values):
    ✓ exact        — pred == gt
    ~ partial      — non-empty overlap, but not equal
    ✗ miss         — zero overlap
    ? unpredictable — pred == ["unpredictable"] (VLM abstained)
    · missing      — metric was scorable but not in prediction
    – skipped      — GT marked non-scorable (QC failed); excluded from metrics

Aggregate:
    exact_match_rate       = |exact| / |scorable|
    macro_f1               = mean(F1) over metrics where VLM produced an answer
    micro_precision/recall/f1  = pooled over all (metric, label) pairs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# Status symbols and constants
# ============================================================

STATUS_EXACT = "exact"
STATUS_PARTIAL = "partial"
STATUS_MISS = "miss"
STATUS_UNPREDICTABLE = "unpredictable"
STATUS_MISSING = "missing"
STATUS_SKIPPED = "skipped"

SYMBOL = {
    STATUS_EXACT: "✓",
    STATUS_PARTIAL: "~",
    STATUS_MISS: "✗",
    STATUS_UNPREDICTABLE: "?",
    STATUS_MISSING: "·",
    STATUS_SKIPPED: "–",
}


# ============================================================
# Core comparison
# ============================================================

def compare_metric(
    gt_values: List[str],
    pred_values: Optional[List[str]],
) -> Dict[str, Any]:
    """Compare one metric's gt vs prediction.

    Returns a dict with status, gt (sorted), pred (sorted or None),
    and precision/recall/f1 computed on the cleaned (sans "unpredictable")
    prediction set.
    """
    gt_set = set(gt_values)

    if pred_values is None:
        return {
            "status": STATUS_MISSING,
            "gt": sorted(gt_set),
            "pred": None,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
        }

    pred_set = set(pred_values)

    if pred_set == {"unpredictable"}:
        return {
            "status": STATUS_UNPREDICTABLE,
            "gt": sorted(gt_set),
            "pred": ["unpredictable"],
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
        }

    # Strip a stray "unpredictable" mixed with real labels (violates contract,
    # but don't let it wreck precision).
    clean = pred_set - {"unpredictable"}
    tp = len(gt_set & clean)
    precision = tp / len(clean) if clean else 0.0
    recall = tp / len(gt_set) if gt_set else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) else 0.0
    )

    if clean == gt_set:
        status = STATUS_EXACT
    elif tp > 0:
        status = STATUS_PARTIAL
    else:
        status = STATUS_MISS

    return {
        "status": status,
        "gt": sorted(gt_set),
        "pred": sorted(pred_set),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def compare_testcase(
    gt_record: Dict[str, Any],
    pred: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Build a full comparison report for one testcase."""
    metric_results: List[Dict[str, Any]] = []
    total_tp = 0
    total_fp = 0
    total_fn = 0
    counts = dict.fromkeys(
        [STATUS_EXACT, STATUS_PARTIAL, STATUS_MISS,
         STATUS_UNPREDICTABLE, STATUS_MISSING, STATUS_SKIPPED],
        0,
    )

    for m in gt_record.get("metrics", []):
        en = m["en_field"]

        if not m.get("scorable", False):
            metric_results.append({
                "en_field": en,
                "status": STATUS_SKIPPED,
                "gt": m.get("gt_values", []),
                "pred": pred.get(en),
                "qc_note": m.get("qc_note", ""),
                "precision": None,
                "recall": None,
                "f1": None,
            })
            counts[STATUS_SKIPPED] += 1
            continue

        r = compare_metric(m.get("gt_values", []), pred.get(en))
        r["en_field"] = en
        metric_results.append(r)
        counts[r["status"]] += 1

        gt_set = set(m.get("gt_values", []))
        pred_set = set(pred.get(en, [])) - {"unpredictable"}

        if r["status"] == STATUS_EXACT:
            total_tp += len(gt_set)
        elif r["status"] == STATUS_PARTIAL:
            total_tp += len(gt_set & pred_set)
            total_fp += len(pred_set - gt_set)
            total_fn += len(gt_set - pred_set)
        elif r["status"] == STATUS_MISS:
            total_fp += len(pred_set)
            total_fn += len(gt_set)
        elif r["status"] == STATUS_MISSING:
            total_fn += len(gt_set)
        # UNPREDICTABLE → excluded from micro tallies

    n_scorable = (
        counts[STATUS_EXACT] + counts[STATUS_PARTIAL] + counts[STATUS_MISS]
        + counts[STATUS_UNPREDICTABLE] + counts[STATUS_MISSING]
    )

    exact_match_rate = (
        counts[STATUS_EXACT] / n_scorable if n_scorable else 0.0
    )

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
    micro_f1 = (
        2 * micro_p * micro_r / (micro_p + micro_r)
        if (micro_p + micro_r) else 0.0
    )

    answered_f1s = [
        r["f1"] for r in metric_results
        if r["status"] in (
            STATUS_EXACT, STATUS_PARTIAL, STATUS_MISS, STATUS_MISSING,
        )
    ]
    macro_f1 = sum(answered_f1s) / len(answered_f1s) if answered_f1s else 0.0

    # Extra predictions that weren't asked for (VLM predicted a QC-skipped metric
    # — doesn't hurt scoring, but flag so the user notices).
    asked = {m["en_field"] for m in gt_record.get("metrics", []) if m.get("scorable")}
    extraneous = sorted(set(pred.keys()) - asked)

    return {
        "testcase_id": gt_record.get("testcase_id"),
        "difficulty": gt_record.get("difficulty"),
        "duration_seconds": gt_record.get("duration_seconds"),
        "counts": {
            "scorable": n_scorable,
            **counts,
        },
        "aggregate": {
            "exact_match_rate": exact_match_rate,
            "macro_f1": macro_f1,
            "micro_precision": micro_p,
            "micro_recall": micro_r,
            "micro_f1": micro_f1,
        },
        "extraneous_predictions": extraneous,
        "metrics": metric_results,
    }


# ============================================================
# Pretty printing
# ============================================================

MAX_LIST_WIDTH = 34


def _fmt_list(values: Optional[List[str]], width: int = MAX_LIST_WIDTH) -> str:
    if values is None:
        s = "—"
    elif not values:
        s = "∅"
    else:
        s = ", ".join(values)
    if len(s) > width:
        s = s[: width - 1] + "…"
    return s.ljust(width)


def print_report(result: Dict[str, Any]) -> None:
    tid = result["testcase_id"]
    diff = result["difficulty"]
    dur = result["duration_seconds"]
    sep = "─" * 112

    print(sep)
    print(f"Testcase: {tid}  ({diff}, ~{dur}s)")
    print(sep)
    print(
        f"{'#':>2}  {'Metric':<20}  {'St':<2}  "
        f"{'GT':<{MAX_LIST_WIDTH}}  {'Predicted':<{MAX_LIST_WIDTH}}  P/R/F1"
    )
    print(sep)

    for i, r in enumerate(result["metrics"], start=1):
        en = r["en_field"]
        status = r["status"]
        sym = SYMBOL[status]
        gt = _fmt_list(r.get("gt"))
        pred = _fmt_list(r.get("pred"))
        if status in (STATUS_EXACT, STATUS_PARTIAL, STATUS_MISS, STATUS_MISSING):
            prf = f"{r['precision']:.2f}/{r['recall']:.2f}/{r['f1']:.2f}"
        elif status == STATUS_UNPREDICTABLE:
            prf = "  (abstained)"
        else:
            prf = "  (skipped)"
        print(f"{i:>2}.  {en:<20}  {sym}   {gt}  {pred}  {prf}")

    c = result["counts"]
    a = result["aggregate"]
    n = c["scorable"]
    print(sep)

    def pct(x: int) -> str:
        return f"{x}/{n}  ({100*x/n:.1f}%)" if n else f"{x}/0"

    print(f"  Exact:         {pct(c[STATUS_EXACT])}")
    print(f"  Partial:       {pct(c[STATUS_PARTIAL])}")
    print(f"  Miss:          {pct(c[STATUS_MISS])}")
    print(f"  Unpredictable: {pct(c[STATUS_UNPREDICTABLE])}")
    print(f"  Missing:       {pct(c[STATUS_MISSING])}")
    if c[STATUS_SKIPPED]:
        print(f"  Skipped (QC):  {c[STATUS_SKIPPED]}  (not counted in scorable)")
    if result["extraneous_predictions"]:
        print(
            f"  ⚠ Extra keys in prediction (not asked for): "
            f"{', '.join(result['extraneous_predictions'])}"
        )
    print(sep)
    print(f"  Exact-match rate:  {a['exact_match_rate']*100:.1f}%")
    print(
        f"  Micro P / R / F1:  "
        f"{a['micro_precision']:.3f} / {a['micro_recall']:.3f} / {a['micro_f1']:.3f}"
    )
    print(f"  Macro F1:          {a['macro_f1']:.3f}")
    print(sep)


def print_overall(reports: List[Dict[str, Any]]) -> None:
    """Aggregate numbers across all testcases (useful in batch mode)."""
    if len(reports) <= 1:
        return

    sep = "═" * 112
    print(sep)
    print(f"  Overall across {len(reports)} testcase(s)")
    print(sep)

    tot_scorable = sum(r["counts"]["scorable"] for r in reports)
    tot_exact = sum(r["counts"][STATUS_EXACT] for r in reports)
    if tot_scorable:
        print(
            f"  Global exact-match rate: "
            f"{tot_exact}/{tot_scorable}  ({100*tot_exact/tot_scorable:.1f}%)"
        )

    macro_f1 = sum(r["aggregate"]["macro_f1"] for r in reports) / len(reports)
    micro_f1 = sum(r["aggregate"]["micro_f1"] for r in reports) / len(reports)
    print(f"  Mean macro F1:           {macro_f1:.3f}")
    print(f"  Mean micro F1:           {micro_f1:.3f}")
    print(sep)


# ============================================================
# Input loading
# ============================================================

def load_predictions(path: Path) -> Dict[str, Dict[str, List[str]]]:
    """Load predictions into a {testcase_id → {en_field → [labels]}} dict.

    Anonymous single-testcase input is returned under the sentinel key
    ``"__anonymous__"``; the caller must then resolve a real testcase_id.
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        out: Dict[str, Dict[str, List[str]]] = {}
        for rec in data:
            tid = rec.get("testcase_id")
            if tid is None:
                raise ValueError(
                    f"List entry missing 'testcase_id' in {path}: {rec!r}"
                )
            out[tid] = rec.get("predictions") or rec.get("prediction") or {}
        return out

    if isinstance(data, dict):
        values = list(data.values())
        if not values:
            raise ValueError(f"Empty prediction file: {path}")
        if all(isinstance(v, list) for v in values):
            return {"__anonymous__": data}
        if all(isinstance(v, dict) for v in values):
            return data
        raise ValueError(
            f"Mixed top-level shapes in {path} — expected either a flat "
            f"metric dict (values are lists) or a testcase_id-keyed dict "
            f"(values are dicts)."
        )

    raise ValueError(f"Unsupported prediction JSON shape in {path}")


# ============================================================
# Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare VLM predictions (List[str] per metric) against "
            "metrics_ground_truth.json and print per-metric + aggregate stats."
        ),
    )
    parser.add_argument(
        "--pred", required=True,
        help="Prediction JSON (bare dict, id-keyed dict, or list of records).",
    )
    parser.add_argument(
        "--gt", default="metrics_ground_truth.json",
        help="Ground-truth JSON from metrics_analyzer.py "
             "(default: analyzer/metrics_ground_truth.json).",
    )
    parser.add_argument(
        "--testcase-id", default=None,
        help="Required if --pred is a bare (anonymous) single-testcase dict.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Optional: write the structured comparison report to this JSON path.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the console table (still writes --out if given).",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent

    def resolve(s: str) -> Path:
        p = Path(s)
        return p if p.is_absolute() else (base / s).resolve()

    pred_path = resolve(args.pred)
    gt_path = resolve(args.gt)
    out_path = resolve(args.out) if args.out else None

    gt_records = json.loads(gt_path.read_text(encoding="utf-8"))
    gt_by_id = {r["testcase_id"]: r for r in gt_records}

    preds = load_predictions(pred_path)

    if "__anonymous__" in preds:
        if not args.testcase_id:
            print(
                "ERROR: --pred is a bare dict without an embedded testcase_id. "
                "Pass --testcase-id <id>.\n\n"
                "Available ids in GT:",
                file=sys.stderr,
            )
            for tid in gt_by_id:
                print(f"  - {tid}", file=sys.stderr)
            sys.exit(2)
        preds = {args.testcase_id: preds["__anonymous__"]}

    reports: List[Dict[str, Any]] = []
    for tid, pred in preds.items():
        if tid not in gt_by_id:
            print(f"⚠ Skipping '{tid}': not found in GT.", file=sys.stderr)
            continue
        r = compare_testcase(gt_by_id[tid], pred)
        reports.append(r)
        if not args.quiet:
            print_report(r)

    if not args.quiet:
        print_overall(reports)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {"n_testcases": len(reports), "reports": reports},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"✓ Saved comparison report: {out_path}")


if __name__ == "__main__":
    main()
