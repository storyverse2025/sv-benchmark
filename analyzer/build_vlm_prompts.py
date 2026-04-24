"""
Generate VLM evaluation artifacts from metrics_ground_truth.json.

Artifact layout — no data is duplicated across files.

  metrics_and_gt_values_list.txt   (single source of truth for allowed values)
      │
      ├──► vlm_system_prompt.md        (VLM system message: rules + label bank)
      │
      └──► vlm_evaluation_prompts.json (per-testcase bundles ONLY)
               top-level:
                 * system_prompt_file
                 * version / n_testcases
                 * bundles[]            each with:
                     - testcase_id / difficulty / duration_seconds
                     - num_scorable_metrics
                     - predict_fields[]   — en_field names to predict
                     - skipped_metrics[]  — QC-dropped metrics (en_field + qc_note)
                     - user_prompt        — short text sent alongside the video

Python callers that need a machine-readable "catalog" (cn / kind /
allowed_values per metric) should NOT expect it in the JSON. Instead:

    from metrics_analyzer import parse_allowed_values
    from build_vlm_prompts import build_metric_catalog, build_response_schema

    allowed = parse_allowed_values("metrics_and_gt_values_list.txt")
    catalog = build_metric_catalog(allowed)
    schema  = build_response_schema(bundle["predict_fields"], catalog)

Pipeline:
    compiled testcases + QC + allowed values
        → metrics_analyzer.py    → metrics_ground_truth.json
    metrics_ground_truth.json + allowed values
        → build_vlm_prompts.py   → vlm_system_prompt.md
                                 + vlm_evaluation_prompts.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from metrics_analyzer import FIELD_TO_METRIC, parse_allowed_values


# ============================================================
# Multi-value metrics.
#
# Two groups:
#   (a) List-typed in the sampler taxonomy (sv-benchmark sampling_v4):
#         scenes, subjects, camera_movement, shot_size
#   (b) Taxonomically-entangled single-pick dimensions that actually
#       mix two orthogonal concepts, so a video can legitimately
#       satisfy more than one label at once:
#         color_palette — mixes "hue count" (monochromatic vs.
#           polychromatic) with "temperature / contrast" (warm /
#           cool / complementary). A warm monochromatic scene is
#           both `monochromatic` and `warm palette`, so we let the
#           VLM emit both. GT (extracted from the sampler) stays
#           single-valued, so this turns compound predictions into
#           natural partial matches instead of false misses.
# ============================================================

MULTI_VALUE_METRICS: set = {
    "scenes",
    "subjects",
    "camera_movement",
    "shot_size",
    "color_palette",
}


def _kind_of(en_field: str) -> str:
    return "multi" if en_field in MULTI_VALUE_METRICS else "single"


# ============================================================
# System prompt (shared across all testcases)
# ============================================================

SYSTEM_PROMPT_TEMPLATE = """\
# Role

You are a cinema-grade video analyst. Given a short generated video \
(a few seconds long), classify it along the 27-dimension visual taxonomy \
below and return a strict JSON object.

# Output contract

1. Watch the ENTIRE video before answering.
2. For each metric the user turn asks you to predict, return values ONLY \
from that metric's `allowed` list in the Label Bank below. Do NOT invent, \
paraphrase, or translate labels.
3. **Every value is a JSON array of strings** — this matches the \
ground-truth schema (`gt_values: List[str]`) so predictions can be \
compared 1:1. Do NOT return bare strings.
   - Metrics marked **[single]** must have EXACTLY ONE element, \
e.g. `"style": ["surrealist"]`.
   - Metrics marked **[multi]** must have ONE OR MORE elements \
(use multiple only when the video genuinely exhibits several — \
e.g. two distinct subjects, or a shot that changes framing), \
e.g. `"subjects": ["human", "robot"]`.
4. If no allowed value fits, or the dimension is not observable from the \
video, return `["unpredictable"]`. The literal `"unpredictable"` must be \
the ONLY element in the array when used — never mix it with real labels.
5. Only emit the keys the user turn asks for. Some samples intentionally \
skip metrics whose QC review did not pass — those keys must be omitted \
from your JSON object entirely.
6. Output MUST be exactly ONE JSON object — no prose, no markdown fences, \
no extra text before or after.

# Example shape

```json
{{
  "style": ["cinematic"],
  "scenes": ["city street", "warehouse"],
  "subjects": ["human"],
  "camera_movement": ["tracking shot", "handheld"],
  "depth_of_field": ["unpredictable"]
}}
```

# Label Bank — all 27 metrics

{metrics_block}
"""


def _format_label_bank(allowed: Dict[str, List[str]]) -> str:
    lines: List[str] = []
    for i, (en_field, cn_metric) in enumerate(FIELD_TO_METRIC.items(), start=1):
        kind = _kind_of(en_field)
        values = allowed.get(en_field, [])
        allowed_line = " | ".join(values) if values else "(undefined)"
        lines.append(
            f"{i:>2}. **{en_field}** ({cn_metric}) — [{kind}]\n"
            f"    {allowed_line}"
        )
    return "\n\n".join(lines)


def build_system_prompt(allowed: Dict[str, List[str]]) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        metrics_block=_format_label_bank(allowed),
    )


# ============================================================
# Shared metric catalog
# ============================================================

def build_metric_catalog(allowed: Dict[str, List[str]]) -> Dict[str, Any]:
    """Return a dict keyed by en_field with cn / kind / allowed_values.

    Emitted once at the top level of vlm_evaluation_prompts.json so that
    per-testcase bundles do not have to repeat the 27 × N-values tables.
    """
    catalog: Dict[str, Any] = {}
    for en_field, cn_metric in FIELD_TO_METRIC.items():
        catalog[en_field] = {
            "cn": cn_metric,
            "kind": _kind_of(en_field),
            "allowed_values": list(allowed.get(en_field, [])),
        }
    return catalog


# ============================================================
# Per-testcase user prompt
# ============================================================
#
# Keep the user turn minimal: everything about rules, field names,
# [single]/[multi] typing, and the "unpredictable" fallback is already
# pinned in the system prompt's Label Bank. The user turn only needs
# testcase-specific info — which sample this is, and (if relevant)
# which metrics are intentionally skipped.

USER_PROMPT_ALL_TEMPLATE = """\
Evaluate the attached video for testcase `{testcase_id}` \
(difficulty {difficulty}, duration ~{duration_seconds}s).

Predict all {n_total} metrics from the Label Bank in your system \
instructions. Return a single JSON object with exactly those {n_total} keys.
"""

USER_PROMPT_WITH_SKIP_TEMPLATE = """\
Evaluate the attached video for testcase `{testcase_id}` \
(difficulty {difficulty}, duration ~{duration_seconds}s).

Predict all metrics from the Label Bank in your system instructions \
EXCEPT the following {n_skip}, which are intentionally skipped for this \
sample (QC failed or dropped):

  {skip_list}{qc_notes}

Return a single JSON object with exactly the {n_predict} remaining keys.
"""


def build_vlm_bundle(testcase_record: Dict[str, Any]) -> Dict[str, Any]:
    """Return a lean VLM bundle for one testcase (no redundant catalog data)."""
    order = {f: i for i, f in enumerate(FIELD_TO_METRIC)}

    predict_fields: List[str] = []
    skipped: List[Dict[str, Any]] = []
    for m in testcase_record.get("metrics", []):
        en_field = m["en_field"]
        if m.get("scorable"):
            predict_fields.append(en_field)
        else:
            skipped.append({
                "en_field": en_field,
                "metric": m["metric"],
                "qc_note": m.get("qc_note", ""),
            })

    predict_fields.sort(key=lambda f: order.get(f, 999))
    skipped.sort(key=lambda s: order.get(s["en_field"], 999))

    n_total = len(FIELD_TO_METRIC)

    if skipped:
        skip_fields = [s["en_field"] for s in skipped]
        skip_notes = "; ".join(
            f"{s['en_field']}: {s['qc_note']}"
            for s in skipped if s.get("qc_note")
        )
        qc_notes = f"\n  (QC notes: {skip_notes})" if skip_notes else ""
        user_prompt = USER_PROMPT_WITH_SKIP_TEMPLATE.format(
            testcase_id=testcase_record.get("testcase_id"),
            difficulty=testcase_record.get("difficulty", ""),
            duration_seconds=testcase_record.get("duration_seconds", ""),
            n_skip=len(skip_fields),
            n_predict=len(predict_fields),
            skip_list=", ".join(skip_fields),
            qc_notes=qc_notes,
        )
    else:
        user_prompt = USER_PROMPT_ALL_TEMPLATE.format(
            testcase_id=testcase_record.get("testcase_id"),
            difficulty=testcase_record.get("difficulty", ""),
            duration_seconds=testcase_record.get("duration_seconds", ""),
            n_total=n_total,
        )

    return {
        "testcase_id": testcase_record.get("testcase_id"),
        "difficulty": testcase_record.get("difficulty"),
        "duration_seconds": testcase_record.get("duration_seconds"),
        "num_scorable_metrics": len(predict_fields),
        "predict_fields": predict_fields,
        "skipped_metrics": skipped,
        "user_prompt": user_prompt,
    }


# ============================================================
# Response-schema helper (for callers doing constrained decoding)
# ============================================================

def build_response_schema(
    predict_fields: List[str],
    metric_catalog: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a JSON-Schema for the given subset of metrics.

    Designed for Gemini's ``response_schema`` / OpenAI's ``response_format``.
    Every metric is typed as ``array<string>`` so the predicted payload
    mirrors the GT's ``gt_values: List[str]`` shape 1:1.

    - ``[single]`` metrics are constrained to ``minItems=maxItems=1``.
    - ``[multi]`` metrics are ``minItems=1`` (uncapped).
    - Each item's enum is the catalog's ``allowed_values`` plus the
      fallback literal ``"unpredictable"``.
    """
    properties: Dict[str, Any] = {}
    for f in predict_fields:
        spec = metric_catalog[f]
        enum_vals = list(spec["allowed_values"]) + ["unpredictable"]
        field_schema: Dict[str, Any] = {
            "type": "array",
            "items": {"type": "string", "enum": enum_vals},
            "minItems": 1,
        }
        if spec["kind"] == "single":
            field_schema["maxItems"] = 1
        properties[f] = field_schema

    return {
        "type": "object",
        "properties": properties,
        "required": list(predict_fields),
        "additionalProperties": False,
    }


# ============================================================
# Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build VLM evaluation artifacts from metrics_ground_truth.json: "
            "a shared system prompt (rules + label bank) and a per-testcase "
            "user-prompt JSON that references a shared metric catalog."
        ),
    )
    parser.add_argument(
        "--gt", type=str, default="metrics_ground_truth.json",
        help="Ground-truth JSON from metrics_analyzer.py "
             "(default: analyzer/metrics_ground_truth.json).",
    )
    parser.add_argument(
        "--gt-values", type=str, default="metrics_and_gt_values_list.txt",
        help="Allowed-values TXT (default: analyzer/metrics_and_gt_values_list.txt).",
    )
    parser.add_argument(
        "--out", type=str, default="vlm_evaluation_prompts.json",
        help="Per-testcase user prompts JSON "
             "(default: analyzer/vlm_evaluation_prompts.json).",
    )
    parser.add_argument(
        "--system-out", type=str, default="vlm_system_prompt.md",
        help="Shared system prompt markdown file "
             "(default: analyzer/vlm_system_prompt.md).",
    )
    parser.add_argument(
        "--show-sample", action="store_true",
        help="Print the system prompt and a sample user prompt to stdout.",
    )
    args = parser.parse_args()

    base = Path(__file__).resolve().parent

    def resolve(p: str) -> Path:
        return Path(p) if Path(p).is_absolute() else (base / p).resolve()

    gt_path = resolve(args.gt)
    values_path = resolve(args.gt_values)
    out_path = resolve(args.out)
    system_out_path = resolve(args.system_out)

    gt_records: List[Dict[str, Any]] = json.loads(
        gt_path.read_text(encoding="utf-8")
    )
    allowed = parse_allowed_values(values_path)

    missing = [f for f in FIELD_TO_METRIC if f not in allowed]
    if missing:
        print(
            f"⚠ {len(missing)} metric(s) have no allowed values parsed from "
            f"{values_path.name}: {missing}"
        )

    system_prompt = build_system_prompt(allowed)
    bundles = [build_vlm_bundle(rec) for rec in gt_records]

    system_out_path.parent.mkdir(parents=True, exist_ok=True)
    system_out_path.write_text(system_prompt, encoding="utf-8")
    print(f"✓ Saved system prompt: {system_out_path}")

    output = {
        "system_prompt_file": system_out_path.name,
        "version": "v3",
        "n_testcases": len(bundles),
        "bundles": bundles,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ Saved {len(bundles)} per-testcase bundle(s): {out_path}")

    if args.show_sample:
        print(f"\n{'=' * 85}")
        print(f"  System prompt ({system_out_path.name})")
        print(f"{'=' * 85}\n")
        print(system_prompt)
        if bundles:
            sample_bundle = next(
                (b for b in bundles if b["skipped_metrics"]),
                bundles[0],
            )
            print(f"\n{'=' * 85}")
            print(f"  Sample user prompt: {sample_bundle['testcase_id']}")
            print(f"  (has {len(sample_bundle['skipped_metrics'])} skipped metric(s))")
            print(f"{'=' * 85}\n")
            print(sample_bundle["user_prompt"])


if __name__ == "__main__":
    main()
