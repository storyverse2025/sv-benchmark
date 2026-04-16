"""
Metrics Analyzer — Stage 4 of the sv-benchmark pipeline
========================================================

Dual-source strategy:
  PRIMARY  — compiled_testcases.json → read active_dimensions (which tags
             the compiler actually preserved in the final prompt)
  FALLBACK — compiler_payloads_v4.json → all non-"none" tags are treated
             as active (used when compiled testcases are unavailable or
             lack the active_dimensions field)

Tag values always come from the compiler payload so the checklist can
show the concrete value assigned to each dimension.

Output (written to analyzer/ folder):
  1. metrics_checklists.json        — per-testcase active metrics + tag values
  2. tag_distribution_by_level.json — per-difficulty cumulative distribution
"""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ============================================================
# 1. Metric definitions  (27 dimensions)
# ============================================================

FIELD_TO_METRIC = OrderedDict([
    # ── v3 original 18 ──────────────────────────────────────
    ("style",              "画风"),
    ("scenes",             "场景"),
    ("subjects",           "主体"),
    ("physical_state",     "物理属性.状态"),
    ("physical_rule",      "物理属性.规则"),
    ("texture",            "物理属性.纹理"),
    ("opacity",            "物理属性.透光度"),
    ("spatial_layout",     "空间布局"),
    ("action",             "动作"),
    ("emotion",            "表情"),
    ("effect",             "特效"),
    ("lighting_tone",      "灯光.色调"),
    ("lighting_direction", "灯光.方向"),
    ("camera_angle",       "相机.角度"),
    ("camera_movement",    "相机.运镜"),
    ("composition",        "相机.构图"),
    ("time_mode",          "相机.时间"),
    ("shot_size",          "相机.景别"),
    # ── v4 new 9 ────────────────────────────────────────────
    ("scale",              "物理属性.尺度"),
    ("lighting_intensity", "灯光.强度"),
    ("color_saturation",   "色彩.饱和度"),
    ("color_palette",      "色彩.色板"),
    ("depth_of_field",     "相机.景深"),
    ("focal_length",       "相机.焦距"),
    ("time_of_day",        "环境.时间"),
    ("weather",            "环境.天气"),
    ("transition",         "转场"),
])

N_METRICS = len(FIELD_TO_METRIC)          # 27

DIFFICULTY_LABELS = ["S1", "S2", "S3", "S4", "S5"]


# ============================================================
# 2. Helpers — tag value extraction
# ============================================================

def _to_values(value: Any) -> List[str]:
    """Normalize a tag field value to a flat list of display strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip().lower() != "none" and value.strip():
        return [value]
    return []


def _payload_active_fields(payload: Dict[str, Any]) -> Set[str]:
    """Determine which dimensions are active from a raw payload (fallback)."""
    active: Set[str] = set()
    for en_field in FIELD_TO_METRIC:
        raw = payload.get(en_field)
        is_active = (
            (isinstance(raw, list) and len(raw) > 0)
            or (isinstance(raw, str) and raw.strip().lower() != "none" and raw.strip() != "")
        )
        if is_active:
            active.add(en_field)
    return active


# ============================================================
# 3. Build per-testcase metrics checklist
# ============================================================

def _detect_metrics(
    payload: Dict[str, Any],
    active_dims: Optional[Set[str]],
) -> List[Dict[str, Any]]:
    """
    For each of the 27 dimensions, decide active/inactive and read values.

    active_dims (from compiled testcase) is the primary source.
    Falls back to payload-level detection when active_dims is None.
    """
    fallback = active_dims is None
    if fallback:
        active_dims = _payload_active_fields(payload)

    results: List[Dict[str, Any]] = []
    for en_field in FIELD_TO_METRIC:
        active = en_field in active_dims
        values = _to_values(payload.get(en_field)) if active else []
        results.append({
            "metric": FIELD_TO_METRIC[en_field],
            "en_field": en_field,
            "detected_values": values,
            "active": active,
            "source": "payload_fallback" if fallback else "compiled",
        })
    return results


def _make_testcase_id(payload: Dict[str, Any]) -> str:
    diff = payload.get("difficulty", "??")
    idx = payload.get("id", 0)
    return f"{diff}-{idx:03d}"


def build_metrics_checklists(
    payloads: List[Dict[str, Any]],
    compiled: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build a per-testcase checklist of which metrics are active.

    If compiled testcases are provided AND they contain active_dimensions,
    those are used as the ground truth.  Otherwise, falls back to
    payload-level tag presence.
    """
    compiled_by_idx: Dict[int, Dict[str, Any]] = {}
    if compiled:
        for i, tc in enumerate(compiled):
            compiled_by_idx[i] = tc

    checklists: List[Dict[str, Any]] = []
    for i, pl in enumerate(payloads):
        tc = compiled_by_idx.get(i)
        active_dims: Optional[Set[str]] = None
        if tc and "active_dimensions" in tc:
            active_dims = set(tc["active_dimensions"])

        tid_compiled = tc["testcase_id"] if tc and "testcase_id" in tc else None
        tid = tid_compiled or _make_testcase_id(pl)

        detected = _detect_metrics(pl, active_dims)
        active_count = sum(1 for m in detected if m["active"])

        checklists.append({
            "testcase_id": tid,
            "difficulty": pl.get("difficulty", "unknown"),
            "num_metrics": active_count,
            "source": "compiled" if active_dims is not None else "payload_fallback",
            "metrics": detected,
        })
    return checklists


# ============================================================
# 4. Cumulative distribution (array format)
# ============================================================

def build_distribution(
    checklists: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for cl in checklists:
        groups.setdefault(cl["difficulty"], []).append(cl)

    result: List[Dict[str, Any]] = []
    for diff in DIFFICULTY_LABELS:
        if diff not in groups:
            continue
        group = groups[diff]
        n_samples = len(group)
        total_possible = N_METRICS * n_samples

        counts: Dict[str, int] = dict.fromkeys(FIELD_TO_METRIC, 0)
        for cl in group:
            for m in cl["metrics"]:
                if m["active"]:
                    counts[m["en_field"]] += 1

        total_active = sum(counts.values())

        metrics_arr: List[Dict[str, Any]] = []
        for en_field, cn_metric in FIELD_TO_METRIC.items():
            c = counts[en_field]
            metrics_arr.append({
                "metric": cn_metric,
                "en_field": en_field,
                "active_count": c,
                "ratio": round(c / total_possible, 4) if total_possible > 0 else 0,
            })

        result.append({
            "difficulty": diff,
            "n_samples": n_samples,
            "total_possible_metric_slots": total_possible,
            "total_active_metric_slots": total_active,
            "metrics": metrics_arr,
        })

    return result


# ============================================================
# 5. Pretty-print
# ============================================================

def print_checklists(checklists: List[Dict[str, Any]]) -> None:
    for cl in checklists:
        src = cl["source"]
        tag = f"{cl['num_metrics']} active / {N_METRICS} total  [{src}]"
        print(f"\n{'='*85}")
        print(f"  Testcase : {cl['testcase_id']}")
        print(f"  Difficulty: {cl['difficulty']}  |  Metrics: {tag}")
        print(f"{'='*85}")
        print(f"  {'Active':<8s} {'Metric':<16s} {'EN Field':<22s} {'Values'}")
        print(f"  {'-'*8} {'-'*16} {'-'*22} {'-'*30}")
        for m in cl["metrics"]:
            flag = "  ✓" if m["active"] else "  ✗"
            vals = ", ".join(m["detected_values"]) if m["detected_values"] else "—"
            print(f"  {flag:<8s} {m['metric']:<16s} {m['en_field']:<22s} {vals}")


def print_distribution(dist: List[Dict[str, Any]]) -> None:
    for level in dist:
        diff = level["difficulty"]
        n = level["n_samples"]
        tp = level["total_possible_metric_slots"]
        ta = level["total_active_metric_slots"]
        print(f"\n{'#'*65}")
        print(f"  {diff}  (n_samples={n}, active={ta}/{tp})")
        print(f"{'#'*65}")
        print(f"  {'Metric':<16s} {'EN Field':<22s} {'Active':>6s}  {'Ratio':>8s}  Bar")
        print(f"  {'-'*16} {'-'*22} {'-'*6}  {'-'*8}  {'-'*20}")
        for m in level["metrics"]:
            bar_len = int(m["ratio"] * 20 * N_METRICS)
            bar = "█" * min(bar_len, 20) + "░" * max(20 - bar_len, 0)
            active_str = f"{m['active_count']}/{n}"
            print(f"  {m['metric']:<16s} {m['en_field']:<22s} {active_str:>6s}  "
                  f"{m['ratio']:>7.2%}  {bar}")


# ============================================================
# 6. Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Metrics Analyzer: derive active metrics from compiled testcases + payloads",
    )
    parser.add_argument(
        "--payloads", type=str,
        default="../outputs/compiler_payloads_v4.json",
        help="Path to compiler payloads JSON (structured tag source)",
    )
    parser.add_argument(
        "--compiled", type=str, default=None,
        help="Path to compiled testcases JSON (active_dimensions source). "
             "If omitted, falls back to payload-only detection.",
    )
    parser.add_argument(
        "--out_dir", type=str, default=".",
        help="Output directory (default: analyzer/ folder itself)",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent

    pl_path = (base / args.payloads).resolve()
    payloads = json.loads(pl_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(payloads)} payloads from {pl_path.name}")

    compiled = None
    if args.compiled:
        tc_path = (base / args.compiled).resolve()
        compiled = json.loads(tc_path.read_text(encoding="utf-8"))
        has_ad = sum(1 for tc in compiled if "active_dimensions" in tc)
        print(f"Loaded {len(compiled)} compiled testcases from {tc_path.name} "
              f"({has_ad}/{len(compiled)} have active_dimensions)")
    else:
        print("No --compiled provided → using payload-only fallback")

    out_dir = (base / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    checklists = build_metrics_checklists(payloads, compiled)
    print_checklists(checklists)

    p1 = out_dir / "metrics_checklists.json"
    p1.write_text(json.dumps(checklists, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Saved: {p1}")

    dist = build_distribution(checklists)
    print_distribution(dist)

    p2 = out_dir / "tag_distribution_by_level.json"
    p2.write_text(json.dumps(dist, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ Saved: {p2}")


if __name__ == "__main__":
    main()
