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
from typing import Any, Dict, List, Tuple

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
    "composition",
    "texture",
    "opacity",
    "style",
    "spatial_layout",
    "weather",
    "action",
    "lighting_intensity",
}


def _kind_of(en_field: str) -> str:
    return "multi" if en_field in MULTI_VALUE_METRICS else "single"


# ============================================================
# Chain-of-Thought (CoT) stages
#
# 27 metrics partitioned into 5 ordered analysis stages. When --enable-cot
# is on, the system prompt instructs the VLM to think through these stages
# IN THIS ORDER before emitting predictions, and to write its observations
# under a top-level `reasoning` object whose keys match the stage `key`s
# below.
#
# IMPORTANT: the metric prediction order remains FIELD_TO_METRIC (label-bank
# order), NOT regrouped by stage. CoT only changes the prose written before
# the predictions; the predictions themselves keep the same shape and order
# so downstream tooling (compare_predictions_vs_gt.py, GT extraction, etc.)
# stays untouched.
# ============================================================

#  (key, cn_label, [metric_en_field, ...])
COT_STAGES: List[Tuple[str, str, List[str]]] = [
    (
        "fx_and_style",
        "视觉特效 & 风格",
        ["style", "effect", "transition", "color_saturation", "color_palette"],
    ),
    (
        "environment",
        "环境",
        ["scenes", "time_of_day", "weather", "spatial_layout"],
    ),
    (
        "subjects_and_physics",
        "主体 & 物理属性",
        [
            "subjects", "action", "emotion", "physical_state",
            "physical_rule", "texture", "opacity", "scale",
        ],
    ),
    (
        "camera",
        "运镜",
        [
            "camera_angle", "camera_movement", "composition", "shot_size",
            "depth_of_field", "focal_length", "time_mode",
        ],
    ),
    (
        "lighting",
        "灯光",
        ["lighting_tone", "lighting_direction", "lighting_intensity"],
    ),
]

# Coverage assertion: every metric must appear in exactly one stage.
# Trips at import time if anyone adds/removes/renames a metric without
# updating the stage map above.
_cot_covered: List[str] = [m for _, _, fs in COT_STAGES for m in fs]
assert set(_cot_covered) == set(FIELD_TO_METRIC), (
    "COT_STAGES must cover every metric in FIELD_TO_METRIC exactly once. "
    f"Missing from stages: {sorted(set(FIELD_TO_METRIC) - set(_cot_covered))}; "
    f"unknown metrics in stages: {sorted(set(_cot_covered) - set(FIELD_TO_METRIC))}"
)
assert len(_cot_covered) == len(set(_cot_covered)), (
    "COT_STAGES has duplicate metrics across stages: "
    f"{sorted({m for m in _cot_covered if _cot_covered.count(m) > 1})}"
)
del _cot_covered


def _format_cot_stage_list() -> str:
    """Render COT_STAGES as a numbered Markdown list for the system prompt."""
    lines: List[str] = []
    for i, (key, cn_label, metrics) in enumerate(COT_STAGES, start=1):
        joined = ", ".join(metrics)
        lines.append(
            f"   {i}. `{key}` — {cn_label} (covers: {joined})"
        )
    return "\n".join(lines)


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
3. **Every metric value is a JSON array of EXACTLY 3 ranked candidate \
objects**, each shaped \
`{{"value": "<allowed_label>", "confidence": <0.0..1.0>}}`. \
Items MUST be sorted by `confidence` descending (most likely first). \
Always emit exactly 3 items — pad with low-confidence guesses if only 1–2 \
labels look plausible. Never repeat the same `value` twice within the \
same metric's array.
   - Metrics marked **[single]** are mutually-exclusive: `items[0]` is \
your single best label; `items[1..2]` are your 2nd / 3rd best alternatives.
   - Metrics marked **[multi]** are co-presence metrics: all 3 items are \
labels the video could simultaneously exhibit, ranked by each label's \
own likelihood of being present.
4. **[multi] slates must be internally consistent — no contradicting \
co-presence claims.** Because every candidate in a [multi] slate is an \
affirmative claim that the label is genuinely present in the video, the \
slate MUST NOT mix labels that are semantically mutually exclusive — \
even at low confidence. Once one side of a mutually-exclusive axis is in \
the slate (at any confidence), the other sides of that axis are \
FORBIDDEN in the same slate. Examples:
   - `color_palette`: `warm palette`, `cool palette`, and `complementary` \
all describe colour temperature/contrast and exclude one another; \
`monochromatic` describes hue count instead, so `monochromatic + warm \
palette` is fine, but `warm palette + cool palette` (at any confidence) \
is NOT — predicting `cool palette` at conf 0.85 means `warm palette` \
must not appear in the same slate even at conf 0.05.
   - `weather`: `clear`, `overcast`, `rainy`, `foggy`, `snowy` are \
largely mutually exclusive — pick at most one.
   - `lighting_intensity`: `high-key` and `low-key` are stylistic \
opposites — never both in one slate.
   When fewer than 3 compatible labels are plausible, pad the remaining \
slot(s) with `unpredictable` rather than with a contradicting label. \
([single] metrics are exempt from this rule — `items[1..2]` are \
explicitly *competing alternatives* to `items[0]` and may legitimately \
contradict it.)
5. **Metric focus discipline — each metric describes ONE focal target.** \
Do NOT borrow a label from one metric to pad another. The most common \
failure mode is conflating *environmental / atmospheric / effect* \
dynamism with the *subject's* own state — e.g., predicting \
`action: falling` for a still subject just because snow is falling \
around them. Falling snow goes under `effect`; the still subject's \
`action` is `none`. The five metric families and their focal targets:
   - **Subject family** (`subjects`, `action`, `emotion`, \
`physical_state`, `physical_rule`, `texture`, `opacity`, `scale`) \
describe the FOCAL subject(s) themselves — NOT the environment, weather, \
VFX particles, or camera artefacts. If the subject is motionless, \
`action` = `none` regardless of how dynamic the scene around them is. \
The texture / opacity / scale of background elements does NOT enter the \
subject family unless those elements are themselves listed under \
`subjects`.
   - **Environment family** (`scenes`, `time_of_day`, `weather`, \
`spatial_layout`) describes the SETTING only. Real atmospheric \
conditions go in `weather`; particles or stylised effects added on top \
go in `effect`.
   - **Camera family** (`camera_angle`, `camera_movement`, `composition`, \
`shot_size`, `depth_of_field`, `focal_length`, `time_mode`) describes \
HOW the shot is photographed.
   - **FX / style family** (`style`, `effect`, `transition`, \
`color_saturation`, `color_palette`) describes stylistic and \
post-production choices.
   - **Lighting family** (`lighting_tone`, `lighting_direction`, \
`lighting_intensity`) describes the lighting set-up.
   When in doubt, ask "is this label genuinely about THIS metric's focal \
target, or is it really describing something else in the frame?" If the \
latter, it does NOT belong here — predict it under its proper metric \
instead.
6. `confidence` is a float in **[0.0, 1.0]** expressing your subjective \
posterior that the label is correct (for [single]) or genuinely present \
(for [multi]). Use the full range — do NOT cluster around 0.5 or 0.99. \
The 3 confidences within one metric do NOT need to sum to 1.0.
7. The literal `"unpredictable"` is a valid `value` in any slot — use it \
when no allowed label plausibly fits or the dimension is unobservable. \
If the dimension is **fully** unobservable, place `unpredictable` in \
slot #1 with high confidence and still fill slots #2 / #3 with your best \
remaining guesses at low confidence (do NOT use `unpredictable` more than \
once per metric).
8. Only emit the keys the user turn asks for. Some samples intentionally \
skip metrics whose QC review did not pass — those keys must be omitted \
from your JSON object entirely.
9. Output MUST be exactly ONE JSON object — no prose, no markdown fences, \
no extra text before or after.{cot_rules}

{example_shape}

# Label Bank — all 27 metrics

{metrics_block}
"""


# Inserted into SYSTEM_PROMPT_TEMPLATE only when --enable-cot is on.
# Note the leading "\n\n": rule #9 ends without a trailing newline, so the
# CoT block opens its own blank line cleanly.
COT_RULES_TEMPLATE = """\


# Chain-of-Thought analysis (enabled)

10. **Reason step-by-step before emitting any metric prediction.** Your \
final JSON object MUST start with a top-level `"reasoning"` key whose \
value is an object containing exactly the following 5 string keys, in \
THIS fixed order (mirror the analysis flow: visual FX & style → environment \
→ subjects & physics → camera → lighting):

{cot_stage_list}

11. Each `reasoning` value is a SHORT English paragraph (1–3 sentences) \
describing what you actually observe in the video that is relevant to \
that stage's metrics **only**. Each paragraph MUST stay within its stage's \
focal target as defined in rule #5: the `subjects_and_physics` paragraph \
describes the SUBJECT itself, not the environment around it or the \
effects on top of it; the `environment` paragraph describes the SETTING, \
not the subject; the `camera` paragraph describes how the shot is \
photographed, not what is in front of the lens; etc. Do NOT smuggle \
observations from a different stage into this paragraph — they belong in \
their own paragraph and will drive their own metrics. Stay grounded in \
concrete visual evidence; do NOT name allowed labels yet — your prose is \
observational, the labels come later.
12. After the `reasoning` object, emit the metric keys in the SAME order \
and SAME shape as defined in rule #3 (top-3 ranked \
`{{"value": "<allowed_label>", "confidence": <0.0..1.0>}}` candidates), \
and respect both the [multi] internal-consistency constraint from rule #4 \
AND the metric focus discipline from rule #5. The metric prediction order \
MUST follow the Label Bank below — do NOT regroup or reorder metrics by \
stage.
13. **Reasoning grounds your predictions — they are NOT independent.** \
Every metric prediction MUST be consistent with your `reasoning` \
paragraphs. Specifically, any label emitted at confidence ≥ 0.5 MUST be \
directly supported by an explicit observation in your reasoning. If you \
catch yourself about to emit a label that contradicts your reasoning \
(e.g., reasoning says the subject is "completely motionless" yet you \
want to predict `action: falling` at conf 0.80 because snow is falling), \
you MUST do exactly one of:
   (a) Drop that label's confidence to a low value, or replace it with \
`unpredictable` — your reasoning was right, the temptation was wrong.
   (b) REVISE the relevant `reasoning` paragraph BEFORE emitting \
predictions so the two agree — your reasoning was wrong, fix it first. \
If you go this route, the new paragraph must accurately describe what \
you actually observe; do not retro-justify a guess.
   Doing NEITHER (i.e., shipping reasoning and predictions that openly \
contradict each other) is FORBIDDEN. Reasoning + predictions are a \
single coherent artefact and will be cross-checked label-by-label."""


# Two example-shape variants. These are substituted *into* the template as
# values, so their literal `{` / `}` characters are not subject to format
# escaping — single braces are correct here.
EXAMPLE_SHAPE_BASE = """\
# Example shape

```json
{
  "style": [
    {"value": "cinematic",   "confidence": 0.85},
    {"value": "minimalist",  "confidence": 0.10},
    {"value": "surrealist",  "confidence": 0.03}
  ],
  "subjects": [
    {"value": "human",              "confidence": 0.92},
    {"value": "robot",              "confidence": 0.05},
    {"value": "fictional creature", "confidence": 0.02}
  ],
  "camera_movement": [
    {"value": "tracking shot", "confidence": 0.70},
    {"value": "handheld",      "confidence": 0.55},
    {"value": "pan",           "confidence": 0.12}
  ],
  "depth_of_field": [
    {"value": "unpredictable", "confidence": 0.80},
    {"value": "shallow DOF",   "confidence": 0.12},
    {"value": "deep DOF",      "confidence": 0.05}
  ]
}
```"""


EXAMPLE_SHAPE_COT = """\
# Example shape (CoT)

```json
{
  "reasoning": {
    "fx_and_style":         "Painterly stylised look with warm flares; one hard cut between scenes.",
    "environment":          "Indoor warehouse at twilight; deep blue light pours through high windows.",
    "subjects_and_physics": "A single human figure stands still wearing dense fabric clothing.",
    "camera":               "Mostly static frame, then a gentle dolly-in toward the subject.",
    "lighting":             "Single warm key from screen-left, dim fill, high contrast."
  },
  "style": [
    {"value": "cinematic",   "confidence": 0.85},
    {"value": "minimalist",  "confidence": 0.10},
    {"value": "surrealist",  "confidence": 0.03}
  ],
  "subjects": [
    {"value": "human",              "confidence": 0.92},
    {"value": "robot",              "confidence": 0.05},
    {"value": "fictional creature", "confidence": 0.02}
  ],
  "camera_movement": [
    {"value": "tracking shot", "confidence": 0.70},
    {"value": "handheld",      "confidence": 0.55},
    {"value": "pan",           "confidence": 0.12}
  ],
  "depth_of_field": [
    {"value": "unpredictable", "confidence": 0.80},
    {"value": "shallow DOF",   "confidence": 0.12},
    {"value": "deep DOF",      "confidence": 0.05}
  ]
}
```"""


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


def build_system_prompt(
    allowed: Dict[str, List[str]],
    *,
    enable_cot: bool = False,
) -> str:
    if enable_cot:
        cot_rules = COT_RULES_TEMPLATE.format(
            cot_stage_list=_format_cot_stage_list(),
        )
        example_shape = EXAMPLE_SHAPE_COT
    else:
        cot_rules = ""
        example_shape = EXAMPLE_SHAPE_BASE

    return SYSTEM_PROMPT_TEMPLATE.format(
        cot_rules=cot_rules,
        example_shape=example_shape,
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
Evaluate the attached video (duration ~{duration_seconds}s).

Predict all {n_total} metrics from the Label Bank in your system \
instructions. Return a single JSON object with exactly those {n_total} keys.
"""

USER_PROMPT_WITH_SKIP_TEMPLATE = """\
Evaluate the attached video (duration ~{duration_seconds}s).

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
            duration_seconds=testcase_record.get("duration_seconds", ""),
            n_skip=len(skip_fields),
            n_predict=len(predict_fields),
            skip_list=", ".join(skip_fields),
            qc_notes=qc_notes,
        )
    else:
        user_prompt = USER_PROMPT_ALL_TEMPLATE.format(
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

TOP_K_CANDIDATES = 3


def build_response_schema(
    predict_fields: List[str],
    metric_catalog: Dict[str, Any],
    *,
    enable_cot: bool = False,
) -> Dict[str, Any]:
    """Build a JSON-Schema for the given subset of metrics.

    Designed for Gemini's ``response_schema`` / OpenAI's ``response_format``.
    Every metric is constrained to EXACTLY ``TOP_K_CANDIDATES`` ranked
    candidate objects of shape ``{"value": "<allowed>", "confidence": <0..1>}``,
    sorted by ``confidence`` descending.

    The [single] vs [multi] distinction is documented in the system prompt
    (semantics differ — see Output contract rule #3) but no longer changes
    the schema shape. Both kinds emit exactly 3 ranked candidates.

    Each candidate's ``value`` enum = the metric's ``allowed_values`` plus
    the fallback literal ``"unpredictable"``.

    When ``enable_cot=True``, a top-level ``"reasoning"`` object is added
    with one required string property per CoT stage (see ``COT_STAGES``).
    The metric properties remain unchanged so this stays a drop-in schema
    for CoT prompts.
    """
    properties: Dict[str, Any] = {}
    required: List[str] = []

    if enable_cot:
        properties["reasoning"] = {
            "type": "object",
            "properties": {
                key: {"type": "string", "minLength": 1}
                for key, _cn, _metrics in COT_STAGES
            },
            "required": [key for key, _cn, _metrics in COT_STAGES],
            "additionalProperties": False,
        }
        required.append("reasoning")

    for f in predict_fields:
        spec = metric_catalog[f]
        enum_vals = list(spec["allowed_values"]) + ["unpredictable"]
        candidate_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": enum_vals},
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
            },
            "required": ["value", "confidence"],
            "additionalProperties": False,
        }
        properties[f] = {
            "type": "array",
            "items": candidate_schema,
            "minItems": TOP_K_CANDIDATES,
            "maxItems": TOP_K_CANDIDATES,
        }
    required.extend(predict_fields)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
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
        "--system-out", type=str, default=None,
        help="Shared system prompt markdown file. Default: "
             "analyzer/vlm_system_prompt.md (or vlm_system_prompt_cot.md "
             "when --enable-cot is on).",
    )
    parser.add_argument(
        "--enable-cot", action="store_true",
        help="Embed Chain-of-Thought reasoning rules into the system prompt: "
             "the VLM is asked to think step-by-step through 5 ordered stages "
             "(visual FX/style → environment → subjects & physics → camera → "
             "lighting) and write its observations under a top-level "
             "`reasoning` object. Metric prediction order is unchanged. "
             "When set, the default --system-out becomes "
             "vlm_system_prompt_cot.md (--out is unchanged).",
    )
    parser.add_argument(
        "--show-sample", action="store_true",
        help="Print the system prompt and a sample user prompt to stdout.",
    )
    args = parser.parse_args()

    # Sentinel default for --system-out: switches filename based on CoT mode
    # without overriding an explicit user-supplied path.
    if args.system_out is None:
        args.system_out = (
            "vlm_system_prompt_cot.md" if args.enable_cot
            else "vlm_system_prompt.md"
        )

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

    system_prompt = build_system_prompt(allowed, enable_cot=args.enable_cot)
    bundles = [build_vlm_bundle(rec) for rec in gt_records]

    system_out_path.parent.mkdir(parents=True, exist_ok=True)
    system_out_path.write_text(system_prompt, encoding="utf-8")
    print(
        f"✓ Saved system prompt ({'CoT' if args.enable_cot else 'base'} mode): "
        f"{system_out_path}"
    )

    # v4    : top-3 ranked {value, confidence} per metric
    # v4-cot: v4 + leading top-level `reasoning` object (5 stages)
    output = {
        "system_prompt_file": system_out_path.name,
        "version": "v4-cot" if args.enable_cot else "v4",
        "cot_enabled": bool(args.enable_cot),
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
