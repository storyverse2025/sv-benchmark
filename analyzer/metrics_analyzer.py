"""
Metrics Analyzer — Stage 4 of the sv-benchmark pipeline
========================================================
Input:  compiled_testcases.json ONLY (Stage 2 output)

Determines which of the 18 TAG_SCHEMA metrics each testcase activates
by scanning the testcase's text fields (core_intent, story_logic,
shot_plan, final_video_prompt, coverage_notes.must_show) with keyword
detection rules.  negative_prompt is excluded to avoid false positives.

Output (written to analyzer/ folder):
  1. metrics_checklists.json   — per-testcase active metrics + detected values
  2. tag_distribution_by_level.json — per-difficulty cumulative distribution
"""

from __future__ import annotations

import argparse
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List


# ============================================================
# 1. Metric definitions
# ============================================================

FIELD_TO_METRIC = OrderedDict([
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
])

DIFFICULTY_LABELS = ["S1", "S2", "S3", "S4", "S5"]


# ============================================================
# 2. Keyword detection rules
# ============================================================
# For each metric, map value_name → list of regex patterns.
# A metric is active if ANY value's pattern matches the scan text.

DETECTION_RULES: Dict[str, Dict[str, List[str]]] = OrderedDict([
    ("style", {
        "realistic":  [r"\brealistic\b", r"\brealism\b"],
        "gothic":     [r"\bgothic\b"],
        "cartoon":    [r"\bcartoon\b", r"\bcartoonish\b"],
    }),
    ("scenes", {
        "indoor":  [r"\bindoors?\b", r"\binterior\b"],
        "outdoor": [r"\boutdoors?\b", r"\bexterior\b", r"\bpark\b"],
    }),
    ("subjects", {
        "human":  [r"\bhuman\b", r"\bperson\b", r"\bwoman\b", r"\bman\b", r"\bpeople\b"],
        "animal": [r"\banimal\b", r"\bdog\b", r"\bcat\b", r"\bcreature\b", r"\bwolf\b"],
        "object": [r"\bobjects?\b"],
    }),
    ("physical_state", {
        "solid":     [r"\bsolid\b"],
        "liquid":    [r"\bliquid\b"],
        "gas":       [r"\bgas(?:eous)?\b"],
        "rigid":     [r"\brigid\b"],
        "non-rigid": [r"\bnon-rigid\b"],
    }),
    ("physical_rule", {
        "real-world": [r"\breal[- ]world\b"],
        "sci-fi":     [r"\bsci-fi\b", r"\bscience[- ]fiction\b", r"\bfuturistic\b"],
    }),
    ("texture", {
        "smooth":   [r"\bsmooth\b"],
        "hair/fur": [r"\bhair\b", r"\bfur\b", r"\bfurry\b"],
    }),
    ("opacity", {
        "transparent":      [r"\btransparent\b"],
        "semi-transparent": [r"\bsemi-transparent\b", r"\btranslucent\b"],
        "opaque":           [r"\bopaque\b"],
    }),
    ("spatial_layout", {
        "vertical relation":              [r"\bvertical\b"],
        "left-right relation":            [r"\bleft[- ]right\b", r"\bside by side\b"],
        "foreground-background relation": [r"\bforeground\b"],
        "inside-outside relation":        [r"\binside[- ]outside\b",
                                           r"\bthreshold\b",
                                           r"\boutdoors?.{0,20}indoors?\b",
                                           r"\boutside.{0,20}inside\b"],
    }),
    ("action", {
        "walking":      [r"\bwalk(?:s|ing|ed)?\b", r"\btrots?\b", r"\btrotting\b"],
        "running":      [r"\brun(?:s|ning)?\b", r"\bsprinting\b"],
        "jumping":      [r"\bjump(?:s|ing|ed)?\b", r"\bleap(?:s|ing)?\b"],
        "fighting":     [r"\bfight(?:s|ing)?\b", r"\bcombat\b"],
        "backflip":     [r"\bback[- ]?flip\b"],
        "martial arts": [r"\bmartial\s+arts?\b"],
        "dialogue":     [r"\bdialogue\b", r"\btalking\b", r"\bconversation\b"],
        "singing":      [r"\bsing(?:s|ing)?\b"],
    }),
    ("emotion", {
        "joy":     [r"\bjoy(?:ful|fully)?\b", r"\bhappy\b", r"\bhappiness\b",
                    r"\bsmil(?:e[sd]?|ing)\b", r"\blaugh(?:s|ing|ter)?\b"],
        "anger":   [r"\banger\b", r"\bangry\b", r"\bfurious\b"],
        "sadness": [r"\bsad(?:ness)?\b", r"\bsorrow\b", r"\bcrying\b"],
        "delight": [r"\bdelight(?:ed)?\b", r"\bcheerful\b", r"\bexcite(?:d|ment)\b"],
    }),
    ("effect", {
        "explosion":    [r"\bexplosion\b", r"\bexplod(?:e[sd]?|ing)\b",
                         r"\bburst\b", r"\bconfetti\b", r"\bdebris\b"],
        "light effect": [r"\blight\s+effect\b", r"\bflash\b"],
    }),
    ("lighting_tone", {
        "warm":    [r"\bwarm\b", r"\bwarmly\b"],
        "cool":    [r"\bcool\b"],
        "neutral": [r"\bneutral\b"],
    }),
    ("lighting_direction", {
        "front light": [r"\bfrontal\b", r"\bfront[- ]?light\b",
                        r"\bfront[- ]lit\b", r"\bfront[- ]facing\b"],
        "side light":  [r"\bside[- ]?light\b"],
        "backlight":   [r"\bbacklight\b", r"\bback[- ]?lit\b"],
        "top light":   [r"\btop[- ]?light\b", r"\boverhead\s+light\b"],
    }),
    ("camera_angle", {
        "high angle": [r"\bhigh[- ]?angle\b"],
        "low angle":  [r"\blow[- ]?angle\b"],
        "eye level":  [r"\beye[- ]?level\b"],
    }),
    ("camera_movement", {
        "push in":       [r"\bpush(?:es|ing)?\s+in\b"],
        "pull out":      [r"\bpull(?:s|ing)?\s+out\b"],
        "pan":           [r"\bpan(?:s|ning)?\b"],
        "truck":         [r"\btruck(?:s|ing)?\b"],
        "tracking shot": [r"\btracking\b", r"\btracks?\s+(?:the|their)\b"],
        "crane":         [r"\bcrane\b"],
        "static":        [r"\bstatic\b", r"\bstationary\b"],
    }),
    ("composition", {
        "rule of thirds": [r"\brule\s+of\s+thirds?\b"],
        "symmetrical":    [r"\bsymmetri(?:cal|y|c)\b"],
        "leading lines":  [r"\bleading\s+lines?\b"],
    }),
    ("time_mode", {
        "real-time":      [r"\breal[- ]?time\b"],
        "slow motion":    [r"\bslow\s+motion\b"],
        "timelapse":      [r"\btime[- ]?lapse\b"],
        "reverse motion": [r"\breverse\b"],
    }),
    ("shot_size", {
        "long shot":   [r"\blong\s+shot\b"],
        "full shot":   [r"\bfull\s+shot\b"],
        "medium shot": [r"\bmedium\s+shot\b"],
        "close shot":  [r"\bclose\s+shot\b", r"\bclose[- ]up\b"],
    }),
])


# ============================================================
# 3. Text extraction & detection
# ============================================================

def _extract_scan_text(tc: Dict[str, Any]) -> str:
    """
    Concatenate all testcase text fields relevant to metric detection.
    Excludes negative_prompt to avoid false positives from negations.
    """
    parts: List[str] = []
    for key in ("core_intent", "story_logic", "final_video_prompt"):
        if key in tc and isinstance(tc[key], str):
            parts.append(tc[key])

    for shot in tc.get("shot_plan", []):
        for key in ("visual_goal", "what_happens", "camera_and_framing", "lighting_and_mood"):
            if key in shot and isinstance(shot[key], str):
                parts.append(shot[key])

    cn = tc.get("coverage_notes", {})
    for key in ("must_show", "soft_interpretations", "tradeoffs"):
        vals = cn.get(key, [])
        if isinstance(vals, list):
            parts.extend(str(v) for v in vals)

    return " ".join(parts)


def _detect_metrics(tc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Run keyword detection against scan text for all 18 metrics.
    Returns list of metric results with detected values and active flag.
    """
    text = _extract_scan_text(tc)

    results: List[Dict[str, Any]] = []
    for en_field, value_patterns in DETECTION_RULES.items():
        detected: List[str] = []
        for value_name, patterns in value_patterns.items():
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    detected.append(value_name)
                    break

        results.append({
            "metric": FIELD_TO_METRIC[en_field],
            "en_field": en_field,
            "detected_values": detected,
            "active": len(detected) > 0,
        })
    return results


def _extract_difficulty(testcase_id: str) -> str:
    for label in DIFFICULTY_LABELS:
        if testcase_id.upper().startswith(label):
            return label
    return "unknown"


# ============================================================
# 4. Output 1 — Per-testcase metrics checklist
# ============================================================

def build_metrics_checklists(
    testcases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    checklists: List[Dict[str, Any]] = []
    for tc in testcases:
        tid = tc["testcase_id"]
        detected = _detect_metrics(tc)
        active_count = sum(1 for m in detected if m["active"])
        checklists.append({
            "testcase_id": tid,
            "difficulty": _extract_difficulty(tid),
            "num_metrics": active_count,
            "metrics": detected,
        })
    return checklists


# ============================================================
# 5. Output 2 — Cumulative distribution (array format)
# ============================================================

def build_distribution(
    checklists: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Per difficulty level, count how many testcases activate each metric.
    ratio = active_count / (18 * n_samples).
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for cl in checklists:
        groups.setdefault(cl["difficulty"], []).append(cl)

    result: List[Dict[str, Any]] = []
    for diff in DIFFICULTY_LABELS:
        if diff not in groups:
            continue
        group = groups[diff]
        n_samples = len(group)
        total_possible = len(FIELD_TO_METRIC) * n_samples

        counts: Dict[str, int] = {en: 0 for en in FIELD_TO_METRIC}
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
# 6. Pretty-print
# ============================================================

def print_checklists(checklists: List[Dict[str, Any]]) -> None:
    for cl in checklists:
        tag = f"{cl['num_metrics']} active / 18 total"
        print(f"\n{'='*80}")
        print(f"  Testcase : {cl['testcase_id']}")
        print(f"  Difficulty: {cl['difficulty']}  |  Metrics: {tag}")
        print(f"{'='*80}")
        print(f"  {'Active':<8s} {'Metric':<14s} {'EN Field':<20s} {'Detected Values'}")
        print(f"  {'-'*8} {'-'*14} {'-'*20} {'-'*35}")
        for m in cl["metrics"]:
            flag = "  ✓" if m["active"] else "  ✗"
            vals = ", ".join(m["detected_values"]) if m["detected_values"] else "—"
            print(f"  {flag:<8s} {m['metric']:<14s} {m['en_field']:<20s} {vals}")


def print_distribution(dist: List[Dict[str, Any]]) -> None:
    for level in dist:
        diff = level["difficulty"]
        n = level["n_samples"]
        tp = level["total_possible_metric_slots"]
        ta = level["total_active_metric_slots"]
        print(f"\n{'#'*60}")
        print(f"  {diff}  (n_samples={n}, active={ta}/{tp})")
        print(f"{'#'*60}")
        print(f"  {'Metric':<14s} {'EN Field':<20s} {'Active':>6s}  {'Ratio':>8s}  Bar")
        print(f"  {'-'*14} {'-'*20} {'-'*6}  {'-'*8}  {'-'*25}")
        for m in level["metrics"]:
            bar_len = int(m["ratio"] * 25 * 18)
            bar = "█" * min(bar_len, 25) + "░" * max(25 - bar_len, 0)
            active_str = f"{m['active_count']}/{n}"
            print(f"  {m['metric']:<14s} {m['en_field']:<20s} {active_str:>6s}  "
                  f"{m['ratio']:>7.2%}  {bar}")


# ============================================================
# 7. Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Metrics Analyzer: detect active metrics from compiled testcase text",
    )
    parser.add_argument(
        "--testcases", type=str,
        default="../examples/compiled_testcases.json",
    )
    parser.add_argument(
        "--out_dir", type=str,
        default=".",
        help="Output directory (default: analyzer/ folder itself)",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    tc_path = (base / args.testcases).resolve()
    out_dir = (base / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    testcases = json.loads(tc_path.read_text(encoding="utf-8"))

    checklists = build_metrics_checklists(testcases)
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
