"""
Across-metrics overall accuracy / precision / recall for VLM top-k predictions.

Given:
    GT  (from metrics_ground_truth.json) — set of allowed labels per metric
    PRED                                  — top-k ranked candidates per metric:
                                            [{value, confidence}, ...] (v4)
                                            or [str, ...] (legacy v3, auto-
                                            wrapped at confidence=1.0).

Per metric, on the top_k ranked candidates that pass confidence threshold τ:

    top_set   = {c.value for c in selected, value != "unpredictable"}
    hit       = 1 if top_set ∩ GT else 0          # "any top-k matches GT"
    tp_m      = |top_set ∩ GT|
    fp_m      = |top_set − GT|
    fn_m      = |GT − top_set|

UNWEIGHTED aggregate (default):
    accuracy  = Σ hit / #metrics
    precision = Σ tp_m / (Σ tp_m + Σ fp_m)         # micro, label-level
    recall    = Σ tp_m / (Σ tp_m + Σ fn_m)         # micro, label-level

WEIGHTED aggregate (--weighted): confidence as weight.
    hit_w_m       = max(conf_i for cand_i in selected if value_i ∈ GT, 0)
    tp_w_m        = Σ conf_i for cand_i in selected, value_i ∈ GT
    fp_w_m        = Σ conf_i for cand_i in selected, value_i ∉ GT and ≠ unpred
    rec_mass_m    = Σ_{g ∈ GT}  max(conf for c in selected if c.value == g, 0)

    accuracy  = Σ hit_w_m / #metrics
    precision = Σ tp_w_m / (Σ tp_w_m + Σ fp_w_m)
    recall    = Σ rec_mass_m / Σ |GT|

When all confidences = 1.0 (legacy v3 data), weighted == unweighted exactly.

Metrics with QC failure (scorable=False) are excluded entirely.
Metrics absent from the prediction contribute hit=0, fn=|GT|.

CoT compatibility:
    Predictions emitted by --enable-cot prompts (build_vlm_prompts.py) carry
    a top-level "reasoning" object alongside the metric keys. load_predictions
    silently drops that key — its 5 stage paragraphs are advisory and not
    metric values. This works whether the file is a bare metric dict, a
    keyed dict-of-testcases, or a list of records.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


UNPREDICTABLE = "unpredictable"
DEFAULT_CONFIDENCE_THRESHOLD = 0.1
DEFAULT_TOP_K = 3

Candidate = Dict[str, Any]  # {"value": str, "confidence": float}


# ============================================================
# Candidate normalization (handles legacy List[str])
# ============================================================

def _normalize(raw: Any) -> Optional[List[Candidate]]:
    """Coerce a metric's predicted value into ranked List[Candidate].

    None / unrecognized → None (caller treats as missing).
    Legacy List[str] → wrapped at confidence=1.0.
    Output is sorted by confidence descending.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return [{"value": raw, "confidence": 1.0}]
    if not isinstance(raw, list):
        return None

    out: List[Candidate] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"value": item, "confidence": 1.0})
        elif isinstance(item, dict) and "value" in item:
            try:
                conf = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                conf = 0.0
            conf = max(0.0, min(1.0, conf))
            out.append({"value": str(item["value"]), "confidence": conf})
    out.sort(key=lambda c: c["confidence"], reverse=True)
    return out


# ============================================================
# Per-metric scoring — returns enough fields to aggregate either way
# ============================================================

def score_metric(
    gt_values: List[str],
    pred_raw: Any,
    *,
    threshold: float,
    top_k: int,
) -> Dict[str, Any]:
    """Compute hit / tp / fp / fn for one metric, both unweighted and weighted.

    Selection: take the top_k ranked candidates, drop conf < τ.
    Both modes are computed; the caller picks one at aggregation time.
    """
    gt_set = set(gt_values)
    cands = _normalize(pred_raw)

    if cands is None:
        return {
            "gt": sorted(gt_set),
            "selected": [],
            "missing": True,
            # Unweighted
            "hit": 0,
            "tp": 0,
            "fp": 0,
            "fn": len(gt_set),
            # Weighted
            "hit_w": 0.0,
            "tp_w": 0.0,
            "fp_w": 0.0,
            "rec_mass": 0.0,
            "gt_size": len(gt_set),
        }

    selected = [c for c in cands[:top_k] if c["confidence"] >= threshold]
    top_values = [c for c in selected if c["value"] != UNPREDICTABLE]
    top_set = {c["value"] for c in top_values}

    # Unweighted
    tp_set = top_set & gt_set
    tp = len(tp_set)
    fp = len(top_set - gt_set)
    fn = len(gt_set - top_set)
    hit = 1 if tp > 0 else 0

    # Weighted
    tp_w = sum(c["confidence"] for c in top_values if c["value"] in gt_set)
    fp_w = sum(c["confidence"] for c in top_values if c["value"] not in gt_set)
    hit_w = max(
        (c["confidence"] for c in top_values if c["value"] in gt_set),
        default=0.0,
    )
    rec_mass = sum(
        max(
            (c["confidence"] for c in top_values if c["value"] == g),
            default=0.0,
        )
        for g in gt_set
    )

    return {
        "gt": sorted(gt_set),
        "selected": selected,
        "missing": False,
        "hit": hit,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "hit_w": hit_w,
        "tp_w": tp_w,
        "fp_w": fp_w,
        "rec_mass": rec_mass,
        "gt_size": len(gt_set),
    }


# ============================================================
# Per-testcase aggregation
# ============================================================

def score_testcase(
    gt_record: Dict[str, Any],
    pred: Dict[str, Any],
    *,
    threshold: float,
    top_k: int,
    weighted: bool,
) -> Dict[str, Any]:
    """Aggregate accuracy / precision / recall across all scorable metrics."""
    rows: List[Dict[str, Any]] = []

    sum_hit = 0
    sum_tp = 0
    sum_fp = 0
    sum_fn = 0

    sum_hit_w = 0.0
    sum_tp_w = 0.0
    sum_fp_w = 0.0
    sum_rec_mass = 0.0
    sum_gt_size = 0

    n_scorable = 0
    n_missing = 0

    for m in gt_record.get("metrics", []):
        en = m["en_field"]
        if not m.get("scorable", False):
            continue

        r = score_metric(
            m.get("gt_values", []),
            pred.get(en),
            threshold=threshold,
            top_k=top_k,
        )
        r["en_field"] = en
        rows.append(r)
        n_scorable += 1
        if r["missing"]:
            n_missing += 1

        sum_hit += r["hit"]
        sum_tp += r["tp"]
        sum_fp += r["fp"]
        sum_fn += r["fn"]

        sum_hit_w += r["hit_w"]
        sum_tp_w += r["tp_w"]
        sum_fp_w += r["fp_w"]
        sum_rec_mass += r["rec_mass"]
        sum_gt_size += r["gt_size"]

    if weighted:
        accuracy = sum_hit_w / n_scorable if n_scorable else 0.0
        precision = (
            sum_tp_w / (sum_tp_w + sum_fp_w) if (sum_tp_w + sum_fp_w) else 0.0
        )
        recall = sum_rec_mass / sum_gt_size if sum_gt_size else 0.0
    else:
        accuracy = sum_hit / n_scorable if n_scorable else 0.0
        precision = sum_tp / (sum_tp + sum_fp) if (sum_tp + sum_fp) else 0.0
        recall = sum_tp / (sum_tp + sum_fn) if (sum_tp + sum_fn) else 0.0

    return {
        "testcase_id": gt_record.get("testcase_id"),
        "difficulty": gt_record.get("difficulty"),
        "duration_seconds": gt_record.get("duration_seconds"),
        "threshold": threshold,
        "top_k": top_k,
        "weighted": weighted,
        "n_scorable": n_scorable,
        "n_missing": n_missing,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "metrics": rows,
    }


# ============================================================
# Pretty printing
# ============================================================

GT_W = 30
PRED_W = 50


def _fmt_list(values: List[str], width: int) -> str:
    s = ", ".join(values) if values else "∅"
    return (s[: width - 1] + "…").ljust(width) if len(s) > width else s.ljust(width)


def _fmt_cands(cands: List[Candidate], width: int) -> str:
    if not cands:
        s = "∅"
    else:
        s = " ".join(
            f"{c['value']}(.{int(round(c['confidence']*100)):02d})"
            for c in cands
        )
    return (s[: width - 1] + "…").ljust(width) if len(s) > width else s.ljust(width)


def print_report(result: Dict[str, Any]) -> None:
    tid = result["testcase_id"]
    diff = result["difficulty"]
    dur = result["duration_seconds"]
    tau = result["threshold"]
    k = result["top_k"]
    weighted = result["weighted"]
    sep = "─" * 110

    mode = "weighted" if weighted else "unweighted"
    print(sep)
    print(f"Testcase: {tid}  ({diff}, ~{dur}s)   τ={tau}  top_k={k}  mode={mode}")
    print(sep)
    print(
        f"{'#':>2}  {'Metric':<20}  {'GT':<{GT_W}}  "
        f"{'Selected (top-k, conf%)':<{PRED_W}}  hit"
    )
    print(sep)

    for i, r in enumerate(result["metrics"], start=1):
        en = r["en_field"]
        gt = _fmt_list(r["gt"], GT_W)
        if r["missing"]:
            cands = "—".ljust(PRED_W)
            hit_str = "  (missing)"
        else:
            cands = _fmt_cands(r["selected"], PRED_W)
            if weighted:
                hit_str = f"{r['hit_w']:.2f}"
            else:
                hit_str = " ✓ " if r["hit"] else " ✗ "
        print(f"{i:>2}.  {en:<20}  {gt}  {cands}  {hit_str}")

    print(sep)
    if result["n_missing"]:
        print(f"  scorable: {result['n_scorable']}   "
              f"(missing in pred: {result['n_missing']})")
    else:
        print(f"  scorable: {result['n_scorable']}")
    print(f"  accuracy:  {result['accuracy']:.3f}")
    print(f"  precision: {result['precision']:.3f}")
    print(f"  recall:    {result['recall']:.3f}")
    print(sep)


def print_overall(reports: List[Dict[str, Any]]) -> None:
    if len(reports) <= 1:
        return
    sep = "═" * 110
    n = len(reports)
    print(sep)
    print(f"  Overall across {n} testcase(s)")
    print(f"  mean accuracy:  {sum(r['accuracy'] for r in reports) / n:.3f}")
    print(f"  mean precision: {sum(r['precision'] for r in reports) / n:.3f}")
    print(f"  mean recall:    {sum(r['recall'] for r in reports) / n:.3f}")
    print(sep)


# ============================================================
# Input loading
# ============================================================

def _strip_reasoning(d: Any) -> Any:
    """Drop a top-level CoT ``reasoning`` object from a prediction dict.

    Predictions emitted by --enable-cot system prompts include a leading
    ``"reasoning"`` key whose value is a dict of stage→paragraph. That key
    is NOT a metric prediction, so it must be removed before downstream
    shape detection (else it produces "Mixed top-level shapes" errors when
    every other top-level value is a list of candidates).

    No-op for non-dicts and for dicts whose ``reasoning`` field is not a
    dict (defensive — callers may shove arbitrary junk under that key).
    Mutates ``d`` in place and returns it.
    """
    if isinstance(d, dict) and isinstance(d.get("reasoning"), dict):
        d.pop("reasoning", None)
    return d


def load_predictions(path: Path) -> Dict[str, Dict[str, Any]]:
    """{testcase_id → {metric → raw_value}}.

    Bare metric→list dict is returned under sentinel "__anonymous__".
    Top-level CoT ``reasoning`` objects are silently stripped at every
    nesting level so v4-cot files load identically to v4 files.
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        out: Dict[str, Dict[str, Any]] = {}
        for rec in data:
            tid = rec.get("testcase_id")
            if tid is None:
                raise ValueError(f"List entry missing 'testcase_id': {rec!r}")
            preds = rec.get("predictions") or rec.get("prediction") or {}
            out[tid] = _strip_reasoning(preds)
        return out

    if isinstance(data, dict):
        # Bare-dict CoT form: reasoning is sibling to metric keys at top level.
        _strip_reasoning(data)
        values = list(data.values())
        if not values:
            raise ValueError(f"Empty prediction file: {path}")
        if all(isinstance(v, list) for v in values):
            return {"__anonymous__": data}
        if all(isinstance(v, dict) for v in values):
            # Keyed-dict CoT form: reasoning lives one level deeper, inside
            # each per-testcase prediction dict.
            for v in data.values():
                _strip_reasoning(v)
            return data
        raise ValueError(f"Mixed top-level shapes in {path}")

    raise ValueError(f"Unsupported prediction JSON shape in {path}")


# ============================================================
# Main
# ============================================================

def _resolve(s: str) -> Path:
    """CWD-first, fall back to script dir."""
    p = Path(s)
    if p.is_absolute():
        return p
    cwd_p = (Path.cwd() / s).resolve()
    if cwd_p.exists():
        return cwd_p
    script_p = (Path(__file__).resolve().parent / s).resolve()
    return script_p if script_p.exists() else cwd_p


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compute across-metrics overall accuracy / precision / recall for "
            "VLM top-k predictions vs metrics_ground_truth.json. Default mode "
            "treats all candidates equally (any top-k match counts as a hit); "
            "pass --weighted to use confidence as weight."
        ),
    )
    parser.add_argument("--pred", required=True, help="Prediction JSON.")
    parser.add_argument("--gt", default="metrics_ground_truth.json",
                        help="Ground-truth JSON (default: metrics_ground_truth.json).")
    parser.add_argument("--testcase-id", default=None,
                        help="Required for bare dict; filter for keyed dict.")
    parser.add_argument("--confidence-threshold", "-t", type=float,
                        default=DEFAULT_CONFIDENCE_THRESHOLD,
                        help=f"Drop candidates with conf < τ "
                             f"(default: {DEFAULT_CONFIDENCE_THRESHOLD}).")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                        help=f"Use only the top K ranked candidates "
                             f"(default: {DEFAULT_TOP_K}).")
    parser.add_argument("--weighted", action="store_true",
                        help="Weight every candidate by its confidence "
                             "(default: off — all candidates count equally).")
    parser.add_argument("--out", default=None,
                        help="Optional JSON path to dump structured report.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress console output (still writes --out).")
    args = parser.parse_args()

    if not (0.0 <= args.confidence_threshold <= 1.0):
        parser.error(
            f"--confidence-threshold must be in [0.0, 1.0], "
            f"got {args.confidence_threshold}"
        )
    if args.top_k < 1:
        parser.error(f"--top-k must be >= 1, got {args.top_k}")

    pred_path = _resolve(args.pred)
    gt_path = _resolve(args.gt)
    out_path = _resolve(args.out) if args.out else None

    if not pred_path.exists():
        parser.error(f"--pred file not found: {pred_path}")
    if not gt_path.exists():
        parser.error(f"--gt file not found: {gt_path}")

    gt_records = json.loads(gt_path.read_text(encoding="utf-8"))
    gt_by_id = {r["testcase_id"]: r for r in gt_records}

    preds = load_predictions(pred_path)

    if "__anonymous__" in preds:
        if not args.testcase_id:
            print("ERROR: --pred is a bare dict; pass --testcase-id <id>.\n",
                  file=sys.stderr)
            print("Available ids in GT:", file=sys.stderr)
            for tid in gt_by_id:
                print(f"  - {tid}", file=sys.stderr)
            sys.exit(2)
        preds = {args.testcase_id: preds["__anonymous__"]}
    elif args.testcase_id:
        if args.testcase_id not in preds:
            print(f"ERROR: --testcase-id '{args.testcase_id}' not in --pred.\n",
                  file=sys.stderr)
            print("Available ids in --pred:", file=sys.stderr)
            for tid in preds:
                print(f"  - {tid}", file=sys.stderr)
            sys.exit(2)
        preds = {args.testcase_id: preds[args.testcase_id]}

    reports: List[Dict[str, Any]] = []
    for tid, pred in preds.items():
        if tid not in gt_by_id:
            print(f"⚠ Skipping '{tid}': not found in GT.", file=sys.stderr)
            continue
        r = score_testcase(
            gt_by_id[tid], pred,
            threshold=args.confidence_threshold,
            top_k=args.top_k,
            weighted=args.weighted,
        )
        reports.append(r)
        if not args.quiet:
            print_report(r)

    if not args.quiet:
        print_overall(reports)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "n_testcases": len(reports),
                    "confidence_threshold": args.confidence_threshold,
                    "top_k": args.top_k,
                    "weighted": args.weighted,
                    "reports": reports,
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"✓ Saved report: {out_path}")


if __name__ == "__main__":
    main()
