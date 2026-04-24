"""
Metrics Analyzer — extracts scorable metrics and their ground-truth values.
=============================================================================

Inputs:
  1. compiled testcases JSON  — list of testcases with `testcase_id`,
     `active_dimensions`, and rich text fields (core_intent, story_logic,
     shot_plan, final_video_prompt, coverage_notes).
  2. QC JSON                  — list of per-testcase reviews with
     `metric_review.<metric>.pass`. A metric is "scorable" iff pass == true.
  3. Allowed-values TXT       — canonical enum values per metric.  Extracted
     ground-truth values MUST appear in this list.

Output (single file):
  metrics_ground_truth.json   — per-testcase list of 27 metrics, each with
     `scorable`, `qc_note`, `active_in_prompt`, and `gt_values`.

Ground-truth extraction:
  Concatenate all textual fields of the compiled testcase and scan it for
  any allowed value (case-insensitive substring, longest-first so that
  "extreme close-up" wins over "close-up").  Matches that overlap in the
  text are deduplicated by keeping the longer span.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 1. Metric definitions (27 dimensions)
# ============================================================

FIELD_TO_METRIC: "OrderedDict[str, str]" = OrderedDict([
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

N_METRICS = len(FIELD_TO_METRIC)  # 27


# ============================================================
# 2. Parse allowed-values list (metrics_and_gt_values_list.txt)
# ============================================================

# Header line looks like:
#   画风 (style) — 12 值：
#   画风 (style) — 12 值： photorealistic, cinematic, ...
_HEADER_RE = re.compile(
    r".*?\(([a-z_]+)\)\s*[—\-]+\s*\d+\s*值\s*[:：]\s*(.*)$",
)


def parse_allowed_values(txt_path: Path) -> Dict[str, List[str]]:
    """Parse `metrics_and_gt_values_list.txt` into {en_field: [values]}.

    Handles three layouts present in the source file:
      (a) header line only → values live on the following non-empty line
      (b) header line with inline values after the `：`
      (c) emotion: header → human-readable explanation → `即：<values>` line
          (the explanation line is skipped in favour of the canonical `即：` line)
    """

    lines = txt_path.read_text(encoding="utf-8").splitlines()
    allowed: Dict[str, List[str]] = {}

    def split_values(raw: str) -> List[str]:
        return [v.strip() for v in raw.split(",") if v.strip()]

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _HEADER_RE.match(line)
        if not m:
            i += 1
            continue

        en_field = m.group(1).strip()
        inline = m.group(2).strip()
        values: List[str] = []

        if inline:
            values = split_values(inline)

        # Scan the block until the next header for a richer enum line.
        # This lets us override inline values for `emotion` (which uses `即：`).
        j = i + 1
        while j < len(lines):
            cand = lines[j].strip()
            if _HEADER_RE.match(cand):
                break
            if not cand:
                j += 1
                continue
            if cand.startswith("即"):
                body = re.sub(r"^即\s*[:：]\s*", "", cand)
                values = split_values(body)
            elif not values and "," in cand:
                values = split_values(cand)
            j += 1

        if values:
            allowed[en_field] = values
        i = j if j > i else i + 1

    return allowed


# ============================================================
# 3. Synonym / paraphrase table  (canonical_value → list of triggers)
# ============================================================
#
# `SYNONYMS[en_field][canonical]` lists extra text triggers that should
# resolve to the same canonical value.  Hyphen / space / joined forms
# are generated automatically by `_generate_variants`; this table only
# covers cases where the prompt uses a genuinely different word.
#
# Keep entries SPECIFIC — overly generic triggers (e.g. "huge",
# "sideways") introduce false positives across unrelated metrics.

SYNONYMS: Dict[str, Dict[str, List[str]]] = {
    "action": {
        "dialogue": ["talking", "conversation", "speaking", "visual dialogue"],
    },
    "camera_angle": {
        "eye level": ["eye-line", "eye height"],
        "bird's eye": ["top-down view", "overhead view"],
    },
    "camera_movement": {
        "truck": ["trucks", "trucking", "truck move", "truck movement", "lateral truck"],
        "push in": ["pushes in", "pushing in"],
        "pull out": ["pulls out", "pulling out"],
        "pan": ["panning", "pans"],
        "tracking shot": ["tracks", "tracking"],
        "crane": ["cranes", "craning", "crane shot", "crane move"],
        "orbit": ["orbits", "orbiting", "orbiting camera"],
        "handheld": ["hand held"],
        "static": ["locked off", "stationary", "fixed camera"],
    },
    "color_palette": {
        "monochromatic": ["monochrome", "single-color"],
    },
    "composition": {
        "center framing": ["centered", "stays centered", "center-frame", "center frame"],
        "leading lines": ["leading line"],
    },
    "depth_of_field": {
        "shallow DOF": ["shallow depth of field", "shallow focus"],
        "deep DOF": ["deep depth of field", "deep focus"],
        "pan-focus": ["all in focus", "everything in focus"],
    },
    "focal_length": {
        "standard lens": ["normal lens", "50mm"],
    },
    "lighting_direction": {
        "under light": ["underlight", "underlit", "uplight", "underlighting"],
        "front light": ["frontlit", "frontlighting"],
        "side light": ["sidelit", "sidelighting"],
        "backlight": ["backlit", "backlighting"],
        "top light": ["overhead light", "top-down light"],
        "ambient light": ["ambient lighting"],
    },
    "lighting_tone": {
        "multi-color": ["multicolored", "rainbow", "polychrome"],
        "warm": ["warm lighting", "warm tones", "warm highlights", "warm glow"],
        "cool": ["cool lighting", "cool tones"],
        "neutral": ["neutral lighting", "neutral tones"],
    },
    "physical_state": {
        "rigid body": ["rigid-bodied"],
    },
    "physical_rule": {
        "real-world": ["realistic physics", "real world physics"],
    },
    "scale": {
        "normal scale": ["normal-scale", "normal size", "regular size", "regular-size"],
        "giant": ["enormous", "gigantic", "colossal"],
    },
    "shot_size": {
        "extreme close-up": ["extreme closeup"],
        "close-up": ["closeup"],
        "extreme long shot": ["extreme wide shot"],
    },
    "spatial_layout": {
        "layered/stacked": [
            "layered", "stacked", "layered or stacked", "stacked layered",
            "stacked risers",
        ],
        "foreground-background": ["foreground and background"],
        "left-right": ["left to right"],
        "inside-outside": ["inside to outside", "inside and outside"],
    },
    "subjects": {
        "fictional creature": ["fantasy creature", "mythical creature"],
        "aquatic animal": ["aquatic creature", "sea creature"],
    },
    "texture": {
        "hair/fur": ["hair", "fur", "furry", "fuzzy hair", "fur-like"],
    },
    "time_mode": {
        "real-time": ["in real time"],
    },
    "transition": {
        "none": [
            "no cuts", "single continuous shot", "single-shot",
            "one continuous shot", "one unbroken shot", "single unbroken",
            "no transitions",
        ],
    },
}


# ============================================================
# 4. Ground-truth extraction from testcase text
# ============================================================

_TEXT_FIELDS_TOP = ("core_intent", "story_logic", "final_video_prompt")
_TEXT_FIELDS_SHOT = (
    "visual_goal",
    "what_happens",
    "camera_and_framing",
    "lighting_and_mood",
    "environment_and_color",
)


def build_testcase_text(testcase: Dict[str, Any]) -> str:
    """Concatenate every descriptive text field of a compiled testcase."""
    parts: List[str] = []

    for key in _TEXT_FIELDS_TOP:
        v = testcase.get(key)
        if isinstance(v, str):
            parts.append(v)

    for shot in testcase.get("shot_plan", []) or []:
        if not isinstance(shot, dict):
            continue
        for key in _TEXT_FIELDS_SHOT:
            v = shot.get(key)
            if isinstance(v, str):
                parts.append(v)

    notes = testcase.get("coverage_notes", {})
    if isinstance(notes, dict):
        for key in ("must_show", "soft_interpretations", "tradeoffs"):
            vals = notes.get(key)
            if isinstance(vals, list):
                parts.extend(str(v) for v in vals if isinstance(v, (str, int, float)))

    return "\n".join(parts)


def _generate_variants(value: str) -> List[str]:
    """Return surface variants of a canonical value.

    Covers the orthographic variation found in compiled prompts:
      "real-time"  → {"real-time", "real time", "realtime"}
      "rigid body" → {"rigid body", "rigid-body", "rigidbody"}
      "hair/fur"   → {"hair/fur", "hair or fur", "hair", "fur"}
      "frame within frame" → {..., "frame-within-frame"}
    """
    value = value.strip()
    if not value:
        return []

    variants = {value}

    if "-" in value:
        variants.add(value.replace("-", " "))
    if " " in value:
        variants.add(value.replace(" ", "-"))

    # For "/" (OR-values), add each side as its own trigger.
    if "/" in value:
        for part in value.split("/"):
            part = part.strip()
            if part:
                variants.update(_generate_variants(part))
        variants.add(value.replace("/", " or "))

    # Joined form ("realtime") only for exactly two reasonably long words
    # to avoid collisions like "multicolor" vs "multi color".
    parts = [p for p in re.split(r"[\s\-]+", value.replace("/", " ")) if p]
    if len(parts) == 2 and all(len(p) >= 3 for p in parts):
        variants.add("".join(parts))

    return sorted(variants, key=len, reverse=True)


def _resolve_triggers(en_field: str, allowed: List[str]) -> List[Tuple[str, str]]:
    """Return list of (canonical_value, trigger_text) pairs to scan for."""
    seen: set = set()
    triggers: List[Tuple[str, str]] = []
    syn_for_field = SYNONYMS.get(en_field, {})

    for canon in allowed:
        for variant in _generate_variants(canon):
            key = (canon, variant.lower())
            if key not in seen:
                seen.add(key)
                triggers.append((canon, variant))
        for syn in syn_for_field.get(canon, []):
            key = (canon, syn.lower())
            if key not in seen:
                seen.add(key)
                triggers.append((canon, syn))

    return triggers


def extract_gt_values(en_field: str, text: str, allowed: List[str]) -> List[str]:
    """Return canonical allowed values whose triggers appear in `text`.

    Matching rules:
      * Each canonical value is matched via its own orthographic variants
        plus any handcrafted synonyms from `SYNONYMS[en_field]`.
      * Case-insensitive with word-boundary anchors (`(?<!\\w)` / `(?!\\w)`)
        to avoid substring false positives such as "rain" inside "restrained".
      * Longest-first greedy selection over non-overlapping spans so that
        "extreme close-up" wins over "close-up" when both would match.
      * "none" is dropped when any concrete canonical value is also matched
        for the same metric.
      * Output preserves the order from the allowed list for deterministic
        diffing.
    """
    if not text or not allowed:
        return []

    triggers = _resolve_triggers(en_field, allowed)
    if not triggers:
        return []

    lower_text = text.lower()
    matches: List[Tuple[int, int, str]] = []

    for canon, trigger in sorted(triggers, key=lambda t: len(t[1]), reverse=True):
        t_low = trigger.lower()
        if not t_low:
            continue
        pattern = r"(?<!\w)" + re.escape(t_low) + r"(?!\w)"
        for m in re.finditer(pattern, lower_text):
            matches.append((m.start(), m.end(), canon))

    matches.sort(key=lambda t: (t[1] - t[0]), reverse=True)

    consumed: List[Tuple[int, int]] = []
    kept: List[str] = []
    for start, end, canon in matches:
        if any(not (end <= cs or start >= ce) for cs, ce in consumed):
            continue
        consumed.append((start, end))
        if canon not in kept:
            kept.append(canon)

    if len(kept) > 1 and "none" in kept:
        kept = [v for v in kept if v != "none"]

    order = {v: i for i, v in enumerate(allowed)}
    kept.sort(key=lambda v: order[v])
    return kept


# ============================================================
# 5. Build per-testcase scorable-metrics record
# ============================================================

_DIFFICULTY_RE = re.compile(r"^(S[1-5])\b", re.IGNORECASE)


def _difficulty_of(testcase_id: str) -> str:
    m = _DIFFICULTY_RE.match(testcase_id or "")
    return m.group(1).upper() if m else "??"


def build_records(
    compiled: List[Dict[str, Any]],
    qc_entries: List[Dict[str, Any]],
    allowed: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    """Join compiled + qc by testcase_id and build per-testcase metric records."""
    qc_by_id: Dict[str, Dict[str, Any]] = {
        qc.get("testcase_id", ""): qc for qc in qc_entries if qc.get("testcase_id")
    }

    records: List[Dict[str, Any]] = []
    for tc in compiled:
        tid = tc.get("testcase_id", "")
        qc = qc_by_id.get(tid, {})
        metric_review = qc.get("metric_review", {}) if isinstance(qc, dict) else {}

        active_dims = set(tc.get("active_dimensions", []) or [])
        full_text = build_testcase_text(tc)

        metrics_out: List[Dict[str, Any]] = []
        scorable_count = 0
        for en_field, cn_metric in FIELD_TO_METRIC.items():
            review = metric_review.get(en_field) if isinstance(metric_review, dict) else None
            if isinstance(review, dict):
                scorable = bool(review.get("pass", False))
                qc_note = review.get("note", "") or ""
            else:
                scorable = False
                qc_note = ""

            if scorable:
                scorable_count += 1

            allowed_for_metric = allowed.get(en_field, [])
            gt_values: List[str] = (
                extract_gt_values(en_field, full_text, allowed_for_metric)
                if scorable else []
            )

            metrics_out.append({
                "en_field": en_field,
                "metric": cn_metric,
                "scorable": scorable,
                "qc_note": qc_note,
                "active_in_prompt": en_field in active_dims,
                "gt_values": gt_values,
            })

        records.append({
            "testcase_id": tid,
            "difficulty": _difficulty_of(tid),
            "duration_seconds": tc.get("duration_seconds"),
            "overall_pass": qc.get("overall_pass") if isinstance(qc, dict) else None,
            "num_scorable_metrics": scorable_count,
            "metrics": metrics_out,
        })

    return records


# ============================================================
# 6. Pretty-print summary
# ============================================================

def print_summary(records: List[Dict[str, Any]]) -> None:
    print(f"\n{'=' * 85}")
    print(f"  Summary: {len(records)} testcase(s)")
    print(f"{'=' * 85}")
    for rec in records:
        tid = rec["testcase_id"]
        nsc = rec["num_scorable_metrics"]
        unmatched = [m["en_field"] for m in rec["metrics"] if m["scorable"] and not m["gt_values"]]
        print(f"\n  • {tid}  [scorable: {nsc}/{N_METRICS}]")
        if unmatched:
            print(f"      ⚠ no GT match for: {', '.join(unmatched)}")
        for m in rec["metrics"]:
            if not m["scorable"]:
                continue
            gts = ", ".join(m["gt_values"]) if m["gt_values"] else "—"
            print(f"      {m['en_field']:<22s} → {gts}")


# ============================================================
# 7. Metrics distribution across difficulty levels
# ============================================================

_LEVEL_ORDER = ("S1", "S2", "S3", "S4", "S5")


def build_metrics_distribution(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute scorable-usage rate per metric × difficulty level.

    For each (metric, level) pair:
      scorable = # testcases at that level where metric has pass=true
      total    = # testcases at that level
      pct      = scorable / total * 100

    Numerator = how many times the metric was actually used for evaluation.
    Denominator = total prompt samples at that difficulty level.
    """
    levels_present: List[str] = []
    total_per_level: Dict[str, int] = {}
    scorable_per_level: Dict[str, Dict[str, int]] = {}

    for rec in records:
        level = rec.get("difficulty") or "unknown"
        if level not in total_per_level:
            total_per_level[level] = 0
            scorable_per_level[level] = dict.fromkeys(FIELD_TO_METRIC, 0)
            levels_present.append(level)
        total_per_level[level] += 1
        for m in rec.get("metrics", []):
            if m.get("scorable"):
                en_field = m.get("en_field")
                if en_field in scorable_per_level[level]:
                    scorable_per_level[level][en_field] += 1

    # Keep canonical S1..S5 order for known levels, append any extras alphabetically
    levels = [lv for lv in _LEVEL_ORDER if lv in total_per_level]
    levels += sorted(lv for lv in total_per_level if lv not in _LEVEL_ORDER)

    total_all = sum(total_per_level.values())

    def _pct(num: int, denom: int) -> float:
        return round(num / denom * 100, 2) if denom else 0.0

    metrics_dist: List[Dict[str, Any]] = []
    for en_field, cn_metric in FIELD_TO_METRIC.items():
        per_level: Dict[str, Dict[str, Any]] = {}
        overall_scorable = 0
        for lv in levels:
            sc = scorable_per_level[lv][en_field]
            tot = total_per_level[lv]
            overall_scorable += sc
            per_level[lv] = {
                "scorable": sc,
                "total": tot,
                "pct": _pct(sc, tot),
            }
        metrics_dist.append({
            "en_field": en_field,
            "metric": cn_metric,
            "per_level": per_level,
            "overall": {
                "scorable": overall_scorable,
                "total": total_all,
                "pct": _pct(overall_scorable, total_all),
            },
        })

    return {
        "levels": levels,
        "total_samples_per_level": {lv: total_per_level[lv] for lv in levels},
        "total_samples": total_all,
        "metrics_distribution": metrics_dist,
    }


def print_distribution(dist: Dict[str, Any]) -> None:
    """Render the metrics-distribution matrix as an ASCII table."""
    levels: List[str] = dist["levels"]
    totals: Dict[str, int] = dist["total_samples_per_level"]
    total_all: int = dist["total_samples"]

    print(f"\n{'=' * 85}")
    print("  Metrics distribution (scorable % per difficulty level)")
    print(f"{'=' * 85}")
    header_levels = "  ".join(f"{lv:>8s}" for lv in levels)
    print(f"\n  {'metric':<22s}  {header_levels}  {'overall':>8s}")
    totals_row = "  ".join(f"{'n=' + str(totals[lv]):>8s}" for lv in levels)
    print(f"  {'':<22s}  {totals_row}  {'n=' + str(total_all):>8s}")
    print(f"  {'-' * 22}  {'-' * (len(levels) * 10 - 2)}  {'-' * 8}")
    for m in dist["metrics_distribution"]:
        cells = "  ".join(
            f"{m['per_level'][lv]['pct']:>7.1f}%" for lv in levels
        )
        overall_pct = m["overall"]["pct"]
        print(f"  {m['en_field']:<22s}  {cells}  {overall_pct:>7.1f}%")


# ============================================================
# 8. Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract scorable metrics and ground-truth values for each testcase "
            "from compiled testcases + QC annotations + allowed-values list."
        ),
    )
    parser.add_argument(
        "--compiled", type=str, required=True,
        help="Path to compiled testcases JSON (has active_dimensions + text fields).",
    )
    parser.add_argument(
        "--qc", type=str, required=True,
        help="Path to QC JSON (has metric_review.<metric>.pass).",
    )
    parser.add_argument(
        "--gt-values", type=str, default="metrics_and_gt_values_list.txt",
        help="Path to allowed-values TXT (default: analyzer/metrics_and_gt_values_list.txt).",
    )
    parser.add_argument(
        "--out", type=str, default="metrics_ground_truth.json",
        help="Output JSON file (default: analyzer/metrics_ground_truth.json).",
    )
    parser.add_argument(
        "--dist-out", type=str, default="metrics_distribution.json",
        help=(
            "Per-difficulty metrics usage distribution JSON "
            "(default: analyzer/metrics_distribution.json)."
        ),
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the per-testcase summary print.",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent

    def resolve(p: str) -> Path:
        return Path(p) if Path(p).is_absolute() else (base / p).resolve()

    compiled_path = resolve(args.compiled)
    qc_path = resolve(args.qc)
    gt_path = resolve(args.gt_values)
    out_path = resolve(args.out)
    dist_path = resolve(args.dist_out)

    compiled: List[Dict[str, Any]] = json.loads(compiled_path.read_text(encoding="utf-8"))
    qc_entries: List[Dict[str, Any]] = json.loads(qc_path.read_text(encoding="utf-8"))
    allowed = parse_allowed_values(gt_path)

    missing = [f for f in FIELD_TO_METRIC if f not in allowed]
    if missing:
        print(
            f"⚠ {len(missing)} metric(s) have no allowed values parsed from "
            f"{gt_path.name}: {missing}"
        )

    print(
        f"Loaded {len(compiled)} compiled testcases from {compiled_path.name}\n"
        f"Loaded {len(qc_entries)} QC entries from {qc_path.name}\n"
        f"Loaded allowed values for {len(allowed)} metric(s) from {gt_path.name}"
    )

    records = build_records(compiled, qc_entries, allowed)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✓ Saved: {out_path}")

    distribution = build_metrics_distribution(records)
    dist_path.parent.mkdir(parents=True, exist_ok=True)
    dist_path.write_text(
        json.dumps(distribution, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ Saved: {dist_path}")

    if not args.quiet:
        print_summary(records)
        print_distribution(distribution)


if __name__ == "__main__":
    main()
