"""
Tag Sample Sampler v3 + Testcase Compiler Prep
=============================================

What is new in v3:
1. Keeps v2's hard difficulty constraints and dependency repair.
2. Adds cinematic feasibility / visibility checks.
3. Adds tag priorities: primary / secondary / stylistic.
4. Adds promptability scoring for downstream compiler routing.
5. Adds English-normalized prompt payloads for LLM testcase compilation.

This script does NOT directly call any model APIs.
It prepares high-quality tag samples and compiler-ready payloads.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from itertools import combinations, permutations
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union


# ============================================================
# 1. Source tag schema (CN source-of-truth)
# ============================================================

TAG_SCHEMA = {
    "画风": ["写实", "哥特", "卡通"],
    "场景": ["室内", "室外"],
    "主体": ["人类", "动物", "物体"],
    "物理属性": {
        "状态": ["固体", "液体", "气体", "刚体", "非刚体"],
        "规则": ["现实", "科幻"],
        "纹理": ["光滑", "毛发"],
        "透光度": ["透明", "半透明", "不透明"],
    },
    "空间布局": ["上下", "左右", "前后", "内外关系"],
    "动作": [
        "走路", "跑步", "跳跃", "打斗", "后空翻",
        "武术", "对话", "唱歌", "无",
    ],
    "表情": [
        "喜:强", "喜:中", "喜:弱",
        "怒:强", "怒:中", "怒:弱",
        "哀:强", "哀:中", "哀:弱",
        "乐:强", "乐:中", "乐:弱",
        "无",
    ],
    "特效": ["爆炸", "光效", "无"],
    "灯光": {
        "色调": ["暖光", "冷光", "中性"],
        "方向": ["顺光", "侧光", "逆光", "顶光"],
    },
    "相机": {
        "角度": ["俯拍", "仰拍", "平拍"],
        "运镜": ["推", "拉", "摇", "移", "跟", "升降", "静止"],
        "构图": ["三分法", "对称", "引导线"],
        "时间": ["常规速度", "慢动作", "延时摄影", "倒放"],
        "景别": ["远景", "全景", "中景", "近景"],
    },
}

DIFFICULTY_DEFS = {
    "S1": {"label": "S1 最简",   "desc": "单物品/单人物 + 单场景 + 单镜头"},
    "S2": {"label": "S2 简单",   "desc": "单物品/单人物 + 多场景 + 单镜头"},
    "S3": {"label": "S3 中等",   "desc": "多物品/多人物 + 单场景 + 单镜头"},
    "S4": {"label": "S4 复杂",   "desc": "多物品/多人物 + 多场景 + 单镜头"},
    "S5": {"label": "S5 极复杂", "desc": "多物品/多人物 + 多场景 + 多镜头"},
}

LEVEL_KEYS = ["S1", "S2", "S3", "S4", "S5"]

DEPENDENCY_MATRIX = {
    "人类": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑", "毛发"],
        "物理属性.透光度": ["不透明"],
        "动作": ["走路", "跑步", "跳跃", "打斗", "后空翻", "武术", "对话", "唱歌", "无"],
        "表情": [
            "喜:强", "喜:中", "喜:弱",
            "怒:强", "怒:中", "怒:弱",
            "哀:强", "哀:中", "哀:弱",
            "乐:强", "乐:中", "乐:弱",
            "无",
        ],
    },
    "动物": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑", "毛发"],
        "物理属性.透光度": ["不透明"],
        "动作": ["走路", "跑步", "跳跃", "打斗", "无"],
        "表情": ["喜:弱", "怒:弱", "哀:弱", "乐:弱", "无"],
    },
    "物体": {
        "物理属性.状态": ["固体", "液体", "气体", "刚体", "非刚体"],
        "物理属性.纹理": ["光滑"],
        "物理属性.透光度": ["透明", "半透明", "不透明"],
        "动作": ["无"],
        "表情": ["无"],
    },
}

DEPENDENT_ATTRS = [
    "物理属性.状态", "物理属性.纹理", "物理属性.透光度", "动作", "表情",
]

# ============================================================
# 2. English normalization maps
# ============================================================

MAP = {
    "画风": {"写实": "realistic", "哥特": "gothic", "卡通": "cartoon"},
    "场景": {"室内": "indoor", "室外": "outdoor"},
    "主体": {"人类": "human", "动物": "animal", "物体": "object"},
    "物理属性.状态": {"固体": "solid", "液体": "liquid", "气体": "gas", "刚体": "rigid", "非刚体": "non-rigid"},
    "物理属性.规则": {"现实": "real-world", "科幻": "sci-fi"},
    "物理属性.纹理": {"光滑": "smooth", "毛发": "hair/fur"},
    "物理属性.透光度": {"透明": "transparent", "半透明": "semi-transparent", "不透明": "opaque"},
    "空间布局": {"上下": "vertical relation", "左右": "left-right relation", "前后": "foreground-background relation", "内外关系": "inside-outside relation"},
    "动作": {"走路": "walking", "跑步": "running", "跳跃": "jumping", "打斗": "fighting", "后空翻": "backflip", "武术": "martial arts", "对话": "dialogue", "唱歌": "singing", "无": "none"},
    "表情": {
        "喜:强": "strong joy", "喜:中": "moderate joy", "喜:弱": "subtle joy",
        "怒:强": "strong anger", "怒:中": "moderate anger", "怒:弱": "subtle anger",
        "哀:强": "strong sadness", "哀:中": "moderate sadness", "哀:弱": "subtle sadness",
        "乐:强": "strong delight", "乐:中": "moderate delight", "乐:弱": "subtle delight",
        "无": "none",
    },
    "特效": {"爆炸": "explosion", "光效": "light effect", "无": "none"},
    "灯光.色调": {"暖光": "warm", "冷光": "cool", "中性": "neutral"},
    "灯光.方向": {"顺光": "front light", "侧光": "side light", "逆光": "backlight", "顶光": "top light"},
    "相机.角度": {"俯拍": "high angle", "仰拍": "low angle", "平拍": "eye level"},
    "相机.运镜": {"推": "push in", "拉": "pull out", "摇": "pan", "移": "truck", "跟": "tracking shot", "升降": "crane", "静止": "static"},
    "相机.构图": {"三分法": "rule of thirds", "对称": "symmetrical", "引导线": "leading lines"},
    "相机.时间": {"常规速度": "real-time", "慢动作": "slow motion", "延时摄影": "timelapse", "倒放": "reverse motion"},
    "相机.景别": {"远景": "long shot", "全景": "full shot", "中景": "medium shot", "近景": "close shot"},
}

PRIMARY_FIELDS = {
    "subject", "subjects", "scene", "scenes", "action", "camera_movement", "shot_size", "camera_angle", "time_mode"
}
SECONDARY_FIELDS = {
    "effect", "emotion", "spatial_layout", "lighting_tone", "lighting_direction", "composition"
}
STYLISTIC_FIELDS = {
    "style", "physical_state", "physical_rule", "texture", "opacity"
}


# ============================================================
# 3. Parsing and balancing utilities
# ============================================================


def parse_difficulty(level_key: str) -> Dict[str, Union[int, Tuple[int, int]]]:
    desc = DIFFICULTY_DEFS[level_key]["desc"]
    result: Dict[str, Union[int, Tuple[int, int]]] = {}

    if "单物品/单人物" in desc:
        result["subject_count"] = 1
    elif "多物品/多人物" in desc:
        result["subject_count"] = (2, min(3, len(TAG_SCHEMA["主体"])))
    else:
        raise ValueError(f"Cannot parse subject count from: {desc}")

    if "单场景" in desc:
        result["scene_count"] = 1
    elif "多场景" in desc:
        result["scene_count"] = (2, min(3, len(TAG_SCHEMA["场景"])))
    else:
        raise ValueError(f"Cannot parse scene count from: {desc}")

    if "单镜头" in desc:
        result["shot_count"] = 1
    elif "多镜头" in desc:
        result["shot_count"] = (2, 3)
    else:
        raise ValueError(f"Cannot parse shot count from: {desc}")

    return result


def get_valid_domain(subjects: List[str], attr_key: str) -> List[str]:
    seen, result = set(), []
    for subj in subjects:
        for val in DEPENDENCY_MATRIX[subj][attr_key]:
            if val not in seen:
                result.append(val)
                seen.add(val)
    return result


def balanced_pool(values: List[Any], n: int) -> List[Any]:
    if not values:
        return []
    pool = [values[i % len(values)] for i in range(n)]
    random.shuffle(pool)
    return pool


def balanced_combo_pool(base_values: List[str], n: int, count_spec: Union[int, Tuple[int, int]]) -> List[List[str]]:
    if isinstance(count_spec, int):
        counts = [count_spec] * n
    else:
        lo, hi = count_spec
        counts = balanced_pool(list(range(lo, hi + 1)), n)

    max_k = len(base_values)
    counts = [min(c, max_k) for c in counts]

    result: List[List[str]] = [None] * n  # type: ignore
    groups: Dict[int, List[int]] = {}
    for i, c in enumerate(counts):
        groups.setdefault(c, []).append(i)

    for c, indices in groups.items():
        combos = [list(x) for x in combinations(base_values, c)]
        pool = balanced_pool(combos, len(indices))
        for j, idx in enumerate(indices):
            picked = list(pool[j])
            random.shuffle(picked)
            result[idx] = picked

    return result


def balanced_seq_pool(base_values: List[str], n: int, count_spec: Union[int, Tuple[int, int]]) -> List[List[str]]:
    if isinstance(count_spec, int):
        counts = [count_spec] * n
    else:
        lo, hi = count_spec
        counts = balanced_pool(list(range(lo, hi + 1)), n)

    max_k = len(base_values)
    counts = [min(c, max_k) for c in counts]

    result: List[List[str]] = [None] * n  # type: ignore
    groups: Dict[int, List[int]] = {}
    for i, c in enumerate(counts):
        groups.setdefault(c, []).append(i)

    for c, indices in groups.items():
        if c == 1:
            pool = balanced_pool(base_values, len(indices))
            for j, idx in enumerate(indices):
                result[idx] = [pool[j]]
        else:
            perms = [list(x) for x in permutations(base_values, c)]
            pool = balanced_pool(perms, len(indices))
            for j, idx in enumerate(indices):
                result[idx] = list(pool[j])
    return result


def repair_pool(pool: List[str], valid_per_sample: List[List[str]]) -> List[str]:
    counts = Counter(pool)
    result = list(pool)
    for i, val in enumerate(result):
        valid = valid_per_sample[i]
        if val not in valid:
            valid_counts = {v: counts.get(v, 0) for v in valid}
            replacement = min(valid_counts, key=valid_counts.get)
            counts[val] -= 1
            result[i] = replacement
            counts[replacement] = counts.get(replacement, 0) + 1
    return result


def _flat_domain(dotted_key: str) -> List[str]:
    parts = dotted_key.split(".")
    node: Any = TAG_SCHEMA
    for p in parts:
        node = node[p]
    return node


# ============================================================
# 4. v3 additions: feasibility, visibility, priorities
# ============================================================


def compute_cinematic_flags(sample: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    warnings: List[str] = []
    suggestions: List[str] = []

    shots = sample["相机"]["景别"]
    time_mode = sample["相机"]["时间"]
    emotion = sample["表情"]
    texture = sample["物理属性"]["纹理"]
    light_dir = sample["灯光"]["方向"]
    effect = sample["特效"]
    action = sample["动作"]
    shot_count = len(sample["相机"]["运镜"])

    if emotion.endswith("弱") and any(s in {"远景", "全景"} for s in shots):
        warnings.append("subtle emotion may be hard to verify in long/full shots")
        suggestions.append("express weak emotion through posture, silhouette, or gaze rather than face detail")

    if texture == "毛发" and any(s == "远景" for s in shots) and light_dir == "逆光":
        warnings.append("hair/fur texture may be weakly visible in long-shot backlit compositions")
        suggestions.append("keep texture secondary unless the model can render clear silhouette detail")

    if time_mode == "延时摄影" and emotion != "无":
        warnings.append("timelapse can reduce readable emotional acting")
        suggestions.append("use environmental motion to signal timelapse and keep character motion simple")

    if time_mode == "延时摄影" and action in {"对话", "唱歌", "打斗", "武术"}:
        issues.append("timelapse conflicts with fine-grained performance-heavy action")

    if effect == "爆炸" and action == "无":
        warnings.append("explosion with no subject action is valid but needs clear staging to remain coherent")
        suggestions.append("make the explosion distant or environmental while the subject stays still")

    if shot_count == 1 and len(sample["场景"]) > 1:
        warnings.append("single-shot multi-scene cases are hard and should rely on continuous traversal or threshold framing")

    if action in {"对话", "唱歌"} and all(s in {"远景", "全景"} for s in shots):
        issues.append("dialogue or singing is weakly testable in very wide framing")

    score = 1.0
    score -= 0.22 * len(issues)
    score -= 0.08 * len(warnings)
    score = max(0.0, round(score, 2))

    if score >= 0.8:
        bucket = "easy_to_visualize"
    elif score >= 0.55:
        bucket = "borderline"
    else:
        bucket = "conflict_heavy"

    return {
        "issues": issues,
        "warnings": warnings,
        "suggestions": suggestions,
        "promptability_score": score,
        "promptability_bucket": bucket,
    }


def build_tag_priority(sample_en: Dict[str, Any]) -> Dict[str, List[str]]:
    primary = []
    secondary = []
    stylistic = []
    for key, value in sample_en.items():
        if key in {"id", "difficulty", "difficulty_desc"}:
            continue
        if key in PRIMARY_FIELDS:
            primary.append(key)
        elif key in SECONDARY_FIELDS:
            secondary.append(key)
        elif key in STYLISTIC_FIELDS:
            stylistic.append(key)
    return {
        "primary_tags": primary,
        "secondary_tags": secondary,
        "stylistic_tags": stylistic,
    }


def normalize_to_english(sample: Dict[str, Any]) -> Dict[str, Any]:
    def m(path: str, value: str) -> str:
        return MAP[path][value]

    return {
        "id": sample["id"],
        "difficulty": sample["难度等级"].split()[0],
        "difficulty_desc": sample["难度描述"],
        "style": m("画风", sample["画风"]),
        "scenes": [m("场景", x) for x in sample["场景"]],
        "subjects": [m("主体", x) for x in sample["主体"]],
        "physical_state": m("物理属性.状态", sample["物理属性"]["状态"]),
        "physical_rule": m("物理属性.规则", sample["物理属性"]["规则"]),
        "texture": m("物理属性.纹理", sample["物理属性"]["纹理"]),
        "opacity": m("物理属性.透光度", sample["物理属性"]["透光度"]),
        "spatial_layout": m("空间布局", sample["空间布局"]),
        "action": m("动作", sample["动作"]),
        "emotion": m("表情", sample["表情"]),
        "effect": m("特效", sample["特效"]),
        "lighting_tone": m("灯光.色调", sample["灯光"]["色调"]),
        "lighting_direction": m("灯光.方向", sample["灯光"]["方向"]),
        "camera_angle": m("相机.角度", sample["相机"]["角度"]),
        "camera_movement": [m("相机.运镜", x) for x in sample["相机"]["运镜"]],
        "composition": m("相机.构图", sample["相机"]["构图"]),
        "time_mode": m("相机.时间", sample["相机"]["时间"]),
        "shot_size": [m("相机.景别", x) for x in sample["相机"]["景别"]],
    }


def build_compiler_payload(sample: Dict[str, Any]) -> Dict[str, Any]:
    en = normalize_to_english(sample)
    cinematic = compute_cinematic_flags(sample)
    priority = build_tag_priority(en)
    return {
        **en,
        **priority,
        "cinematic_feasibility": cinematic,
        "compiler_notes": {
            "language": "English",
            "target_duration_seconds": [10, 15],
            "goal": "compile tags into an evaluable storyboard testcase before compressing into a final video prompt",
            "rule": "preserve primary tags first, then secondary tags, then stylistic tags",
        },
    }


# ============================================================
# 5. Generation and validation
# ============================================================


def generate_samples(n_per_level: int = 5, seed: int = 42) -> List[Dict[str, Any]]:
    random.seed(seed)
    all_samples: List[Dict[str, Any]] = []
    gid = 1

    for level_key in LEVEL_KEYS:
        n = n_per_level
        constraints = parse_difficulty(level_key)
        info = DIFFICULTY_DEFS[level_key]

        p_style = balanced_pool(TAG_SCHEMA["画风"], n)
        p_effect = balanced_pool(TAG_SCHEMA["特效"], n)
        p_layout = balanced_pool(TAG_SCHEMA["空间布局"], n)
        p_phys_rule = balanced_pool(TAG_SCHEMA["物理属性"]["规则"], n)
        p_light_tone = balanced_pool(TAG_SCHEMA["灯光"]["色调"], n)
        p_light_dir = balanced_pool(TAG_SCHEMA["灯光"]["方向"], n)
        p_cam_angle = balanced_pool(TAG_SCHEMA["相机"]["角度"], n)
        p_cam_comp = balanced_pool(TAG_SCHEMA["相机"]["构图"], n)
        p_cam_time = balanced_pool(TAG_SCHEMA["相机"]["时间"], n)

        p_subject = balanced_combo_pool(TAG_SCHEMA["主体"], n, constraints["subject_count"])
        p_scene = balanced_combo_pool(TAG_SCHEMA["场景"], n, constraints["scene_count"])
        p_cam_move = balanced_seq_pool(TAG_SCHEMA["相机"]["运镜"], n, constraints["shot_count"])
        p_cam_shot = balanced_seq_pool(TAG_SCHEMA["相机"]["景别"], n, constraints["shot_count"])

        raw = {attr: balanced_pool(_flat_domain(attr), n) for attr in DEPENDENT_ATTRS}
        valid = {attr: [get_valid_domain(p_subject[i], attr) for i in range(n)] for attr in DEPENDENT_ATTRS}
        repaired = {attr: repair_pool(raw[attr], valid[attr]) for attr in DEPENDENT_ATTRS}

        for i in range(n):
            sample = {
                "id": gid,
                "难度等级": info["label"],
                "难度描述": info["desc"],
                "画风": p_style[i],
                "场景": p_scene[i],
                "主体": p_subject[i],
                "物理属性": {
                    "状态": repaired["物理属性.状态"][i],
                    "规则": p_phys_rule[i],
                    "纹理": repaired["物理属性.纹理"][i],
                    "透光度": repaired["物理属性.透光度"][i],
                },
                "空间布局": p_layout[i],
                "动作": repaired["动作"][i],
                "表情": repaired["表情"][i],
                "特效": p_effect[i],
                "灯光": {
                    "色调": p_light_tone[i],
                    "方向": p_light_dir[i],
                },
                "相机": {
                    "角度": p_cam_angle[i],
                    "运镜": p_cam_move[i],
                    "构图": p_cam_comp[i],
                    "时间": p_cam_time[i],
                    "景别": p_cam_shot[i],
                },
            }
            sample["v3_meta"] = {
                **compute_cinematic_flags(sample),
                **build_tag_priority(normalize_to_english(sample)),
            }
            all_samples.append(sample)
            gid += 1

    return all_samples


def validate_samples(samples: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    for s in samples:
        sid = s["id"]
        level = s["难度等级"][:2]
        constraints = parse_difficulty(level)
        subjects = s["主体"]

        def _check_count(actual: List[Any], spec: Union[int, Tuple[int, int]], dim_name: str):
            if isinstance(spec, int):
                if len(actual) != spec:
                    errors.append(f"#{sid} {dim_name}: expected {spec}, got {len(actual)}")
            else:
                lo, hi = spec
                if not (lo <= len(actual) <= hi):
                    errors.append(f"#{sid} {dim_name}: expected [{lo},{hi}], got {len(actual)}")

        _check_count(subjects, constraints["subject_count"], "subject_count")
        _check_count(s["场景"], constraints["scene_count"], "scene_count")
        _check_count(s["相机"]["运镜"], constraints["shot_count"], "camera_move_count")
        _check_count(s["相机"]["景别"], constraints["shot_count"], "shot_size_count")

        for attr in DEPENDENT_ATTRS:
            valid_vals = set(get_valid_domain(subjects, attr))
            node: Any = s
            for p in attr.split("."):
                node = node[p]
            if node not in valid_vals:
                errors.append(f"#{sid} {attr}: '{node}' not in valid domain {sorted(valid_vals)}")

    return len(errors) == 0, errors


CSV_FIELDS = [
    "id", "难度等级", "难度描述", "画风", "场景", "主体",
    "物理属性.状态", "物理属性.规则", "物理属性.纹理", "物理属性.透光度",
    "空间布局", "动作", "表情", "特效",
    "灯光.色调", "灯光.方向",
    "相机.角度", "相机.运镜", "相机.构图", "相机.时间", "相机.景别",
    "promptability_score", "promptability_bucket", "primary_tags", "secondary_tags", "stylistic_tags",
]


def flatten_sample(sample: Dict[str, Any]) -> Dict[str, str]:
    meta = sample.get("v3_meta", {})
    return {
        "id": str(sample["id"]),
        "难度等级": sample["难度等级"],
        "难度描述": sample["难度描述"],
        "画风": sample["画风"],
        "场景": " / ".join(sample["场景"]),
        "主体": " / ".join(sample["主体"]),
        "物理属性.状态": sample["物理属性"]["状态"],
        "物理属性.规则": sample["物理属性"]["规则"],
        "物理属性.纹理": sample["物理属性"]["纹理"],
        "物理属性.透光度": sample["物理属性"]["透光度"],
        "空间布局": sample["空间布局"],
        "动作": sample["动作"],
        "表情": sample["表情"],
        "特效": sample["特效"],
        "灯光.色调": sample["灯光"]["色调"],
        "灯光.方向": sample["灯光"]["方向"],
        "相机.角度": sample["相机"]["角度"],
        "相机.运镜": " → ".join(sample["相机"]["运镜"]),
        "相机.构图": sample["相机"]["构图"],
        "相机.时间": sample["相机"]["时间"],
        "相机.景别": " → ".join(sample["相机"]["景别"]),
        "promptability_score": str(meta.get("promptability_score", "")),
        "promptability_bucket": meta.get("promptability_bucket", ""),
        "primary_tags": " | ".join(meta.get("primary_tags", [])),
        "secondary_tags": " | ".join(meta.get("secondary_tags", [])),
        "stylistic_tags": " | ".join(meta.get("stylistic_tags", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n_per_level", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out_dir", type=str, default="../outputs")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    samples = generate_samples(n_per_level=args.n_per_level, seed=args.seed)
    passed, errs = validate_samples(samples)
    if not passed:
        raise SystemExit("Validation failed:\n" + "\n".join(errs[:50]))

    payloads = [build_compiler_payload(s) for s in samples]

    (out_dir / "tag_samples_v3.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "compiler_payloads_v3.json").write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out_dir / "tag_samples_v3.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows([flatten_sample(s) for s in samples])

    summary = {
        "total_samples": len(samples),
        "levels": {lk: sum(1 for s in samples if s["难度等级"] == DIFFICULTY_DEFS[lk]["label"]) for lk in LEVEL_KEYS},
        "promptability_distribution": dict(Counter(s["v3_meta"]["promptability_bucket"] for s in samples)),
    }
    (out_dir / "summary_v3.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
