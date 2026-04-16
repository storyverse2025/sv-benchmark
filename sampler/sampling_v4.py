"""
Tag Sample Sampler v4 — Cinema-Grade Evaluation Taxonomy
=========================================================

Evolution from v3 (18 dimensions, ~73 values):
  → v4: 27 dimensions, 205 unique values

Grounding sources:
  - Visual Style       → Art direction / production design categories
  - Scene / Location   → Professional location scouting taxonomy
  - Subject            → Character & asset pipeline classes (film / animation)
  - Camera             → ASC Cinematographer's Manual; Brown, "Cinematography"
  - Lighting           → Three-point + motivated lighting theory
  - Action             → Animator's action library (Williams, "Animator's Survival Kit")
  - Emotion            → Ekman's 6 basic emotions + valence-arousal model
  - Color              → Color grading theory (Van Hurkman, "Color Correction Handbook")
  - Environment        → Time-of-day / weather continuity (script-supervisor practice)
  - Transition         → Editorial grammar (Dmytryk, "On Film Editing")

Dimension summary (27 total):
  v3 kept (18):  style, scene, subject, phys_state, phys_rule, texture,
                 opacity, spatial_layout, action, emotion, effect,
                 light_tone, light_dir, cam_angle, cam_move, composition,
                 time_mode, shot_size
  NEW (9):       scale, light_intensity, color_saturation, color_palette,
                 depth_of_field, focal_length, time_of_day, weather, transition
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
# 1. TAG_SCHEMA — cinema-grade (CN source-of-truth)
# ============================================================

TAG_SCHEMA: Dict[str, Any] = {

    # ── Art Direction ──────────────────────────────────────────
    "画风": [
        "写实", "电影质感", "哥特", "卡通", "日式动漫", "水彩",
        "油画", "赛博朋克", "黑白", "超现实", "极简", "复古胶片",
    ],

    # ── Location / Setting (scouting taxonomy) ────────────────
    "场景": [
        "客厅", "厨房", "办公室", "走廊", "仓库", "医院",
        "城市街道", "公园", "森林", "海滩", "沙漠", "雪地",
        "山顶", "水下", "废墟", "舞台",
    ],

    # ── Subject / Asset class ─────────────────────────────────
    "主体": [
        "人类", "哺乳动物", "鸟类", "水生动物", "昆虫",
        "机器人", "车辆", "自然元素", "日常物品", "虚构生物",
    ],

    # ── Physical Properties ───────────────────────────────────
    "物理属性": {
        "状态": ["固体", "液体", "气体", "等离子", "刚体", "非刚体", "颗粒"],
        "规则": ["现实", "科幻", "魔幻", "梦境"],
        "纹理": [
            "光滑", "粗糙", "毛发", "羽毛", "金属",
            "木质", "布料", "玻璃", "石质", "皮革",
        ],
        "透光度": ["透明", "半透明", "不透明"],
        "尺度": ["微观", "常规", "巨型"],
    },

    # ── Spatial Layout ────────────────────────────────────────
    "空间布局": [
        "上下", "左右", "前后", "内外", "环绕", "对角", "层叠", "散落",
    ],

    # ── Action (animator's taxonomy) ──────────────────────────
    "动作": [
        "走路", "跑步", "跳跃", "打斗", "后空翻", "武术",
        "跳舞", "游泳", "攀爬", "骑行", "驾驶",
        "烹饪", "书写", "弹奏乐器", "投掷",
        "拥抱", "鞠躬", "对话", "唱歌",
        "倒下", "悬浮", "旋转", "挥手", "无",
    ],

    # ── Emotion (Ekman 6 + delight × 3 intensity) ────────────
    "表情": [
        "喜:强", "喜:中", "喜:弱",
        "怒:强", "怒:中", "怒:弱",
        "哀:强", "哀:中", "哀:弱",
        "乐:强", "乐:中", "乐:弱",
        "惊:强", "惊:中", "惊:弱",
        "恐:强", "恐:中", "恐:弱",
        "厌:强", "厌:中", "厌:弱",
        "无",
    ],

    # ── VFX (production VFX taxonomy) ─────────────────────────
    "特效": [
        "爆炸", "光效", "火焰", "烟雾", "雨", "雪",
        "闪电", "魔法粒子", "全息投影", "碎裂", "水花", "无",
    ],

    # ── Lighting ──────────────────────────────────────────────
    "灯光": {
        "色调": ["暖光", "冷光", "中性", "彩色混合"],
        "方向": ["顺光", "侧光", "逆光", "顶光", "底光", "环境光"],
        "强度": ["高调", "低调", "正常"],
    },

    # ── Color Grading ─────────────────────────────────────────
    "色彩": {
        "饱和度": ["高饱和", "低饱和", "去色"],
        "主色调": ["暖色系", "冷色系", "互补色", "单色系"],
    },

    # ── Camera ────────────────────────────────────────────────
    "相机": {
        "角度": ["俯拍", "仰拍", "平拍", "鸟瞰", "荷兰角"],
        "运镜": [
            "推", "拉", "摇", "移", "跟",
            "升降", "环绕", "手持", "甩", "静止",
        ],
        "构图": ["三分法", "对称", "引导线", "中心构图", "框架构图", "黄金螺旋"],
        "时间": ["常规速度", "慢动作", "延时摄影", "倒放", "定格"],
        "景别": ["特写", "近景", "中景", "全景", "远景", "大远景"],
        "景深": ["浅景深", "深景深", "全景深"],
        "焦距": ["广角", "标准", "长焦", "微距"],
    },

    # ── Environment ───────────────────────────────────────────
    "环境": {
        "时段": ["黎明", "白天", "黄昏", "夜晚"],
        "天气": ["晴天", "阴天", "雨天", "雾天", "雪天"],
    },

    # ── Transition (multi-shot only) ──────────────────────────
    "转场": ["硬切", "淡入淡出", "溶解", "擦除", "匹配剪辑", "无"],
}


# ============================================================
# 2. Difficulty definitions (same S1-S5 structure)
# ============================================================

DIFFICULTY_DEFS = {
    "S1": {"label": "S1 最简",   "desc": "单物品/单人物 + 单场景 + 单镜头"},
    "S2": {"label": "S2 简单",   "desc": "单物品/单人物 + 多场景 + 单镜头"},
    "S3": {"label": "S3 中等",   "desc": "多物品/多人物 + 单场景 + 单镜头"},
    "S4": {"label": "S4 复杂",   "desc": "多物品/多人物 + 多场景 + 单镜头"},
    "S5": {"label": "S5 极复杂", "desc": "多物品/多人物 + 多场景 + 多镜头"},
}

LEVEL_KEYS = ["S1", "S2", "S3", "S4", "S5"]


# ============================================================
# 3. Dependency matrix (10 subjects × 6 dependent attributes)
# ============================================================

DEPENDENCY_MATRIX: Dict[str, Dict[str, List[str]]] = {
    "人类": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑", "毛发", "布料", "皮革"],
        "物理属性.透光度": ["不透明"],
        "物理属性.尺度": ["常规"],
        "动作": [
            "走路", "跑步", "跳跃", "打斗", "后空翻", "武术",
            "跳舞", "游泳", "攀爬", "骑行", "驾驶",
            "烹饪", "书写", "弹奏乐器", "投掷",
            "拥抱", "鞠躬", "对话", "唱歌",
            "倒下", "悬浮", "旋转", "挥手", "无",
        ],
        "表情": [
            "喜:强", "喜:中", "喜:弱",
            "怒:强", "怒:中", "怒:弱",
            "哀:强", "哀:中", "哀:弱",
            "乐:强", "乐:中", "乐:弱",
            "惊:强", "惊:中", "惊:弱",
            "恐:强", "恐:中", "恐:弱",
            "厌:强", "厌:中", "厌:弱",
            "无",
        ],
    },
    "哺乳动物": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑", "毛发", "皮革"],
        "物理属性.透光度": ["不透明"],
        "物理属性.尺度": ["微观", "常规", "巨型"],
        "动作": [
            "走路", "跑步", "跳跃", "打斗", "游泳",
            "攀爬", "倒下", "旋转", "无",
        ],
        "表情": ["喜:弱", "怒:弱", "哀:弱", "乐:弱", "惊:弱", "恐:弱", "无"],
    },
    "鸟类": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑", "羽毛"],
        "物理属性.透光度": ["不透明"],
        "物理属性.尺度": ["微观", "常规"],
        "动作": ["走路", "跳跃", "悬浮", "旋转", "无"],
        "表情": ["无"],
    },
    "水生动物": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑"],
        "物理属性.透光度": ["不透明", "半透明"],
        "物理属性.尺度": ["微观", "常规", "巨型"],
        "动作": ["游泳", "跳跃", "旋转", "悬浮", "无"],
        "表情": ["无"],
    },
    "昆虫": {
        "物理属性.状态": ["固体"],
        "物理属性.纹理": ["光滑", "粗糙"],
        "物理属性.透光度": ["不透明", "半透明"],
        "物理属性.尺度": ["微观", "常规"],
        "动作": ["走路", "跳跃", "悬浮", "攀爬", "无"],
        "表情": ["无"],
    },
    "机器人": {
        "物理属性.状态": ["固体", "刚体"],
        "物理属性.纹理": ["光滑", "金属"],
        "物理属性.透光度": ["不透明"],
        "物理属性.尺度": ["常规", "巨型"],
        "动作": [
            "走路", "跑步", "跳跃", "打斗",
            "旋转", "悬浮", "倒下", "挥手", "鞠躬", "无",
        ],
        "表情": ["无"],
    },
    "车辆": {
        "物理属性.状态": ["固体", "刚体"],
        "物理属性.纹理": ["光滑", "金属"],
        "物理属性.透光度": ["不透明"],
        "物理属性.尺度": ["常规", "巨型"],
        "动作": ["驾驶", "旋转", "倒下", "无"],
        "表情": ["无"],
    },
    "自然元素": {
        "物理属性.状态": ["液体", "气体", "等离子", "颗粒", "非刚体"],
        "物理属性.纹理": ["光滑", "粗糙", "玻璃"],
        "物理属性.透光度": ["透明", "半透明", "不透明"],
        "物理属性.尺度": ["微观", "常规", "巨型"],
        "动作": ["悬浮", "旋转", "无"],
        "表情": ["无"],
    },
    "日常物品": {
        "物理属性.状态": ["固体", "液体", "刚体", "非刚体"],
        "物理属性.纹理": ["光滑", "粗糙", "金属", "木质", "布料", "玻璃", "石质"],
        "物理属性.透光度": ["透明", "半透明", "不透明"],
        "物理属性.尺度": ["微观", "常规"],
        "动作": ["旋转", "倒下", "悬浮", "无"],
        "表情": ["无"],
    },
    "虚构生物": {
        "物理属性.状态": ["固体", "非刚体", "气体"],
        "物理属性.纹理": ["光滑", "粗糙", "毛发", "羽毛", "皮革"],
        "物理属性.透光度": ["透明", "半透明", "不透明"],
        "物理属性.尺度": ["微观", "常规", "巨型"],
        "动作": [
            "走路", "跑步", "跳跃", "打斗", "游泳",
            "攀爬", "悬浮", "旋转", "倒下", "无",
        ],
        "表情": [
            "喜:弱", "喜:中", "怒:弱", "怒:中",
            "哀:弱", "乐:弱", "惊:弱", "恐:弱", "无",
        ],
    },
}

DEPENDENT_ATTRS = [
    "物理属性.状态", "物理属性.纹理", "物理属性.透光度", "物理属性.尺度",
    "动作", "表情",
]


# ============================================================
# 4. CN → EN translation maps
# ============================================================

MAP: Dict[str, Dict[str, str]] = {
    "画风": {
        "写实": "photorealistic", "电影质感": "cinematic", "哥特": "gothic",
        "卡通": "cartoon", "日式动漫": "anime", "水彩": "watercolor",
        "油画": "oil painting", "赛博朋克": "cyberpunk", "黑白": "black & white",
        "超现实": "surrealist", "极简": "minimalist", "复古胶片": "vintage film",
    },
    "场景": {
        "客厅": "living room", "厨房": "kitchen", "办公室": "office",
        "走廊": "hallway", "仓库": "warehouse", "医院": "hospital",
        "城市街道": "city street", "公园": "park", "森林": "forest",
        "海滩": "beach", "沙漠": "desert", "雪地": "snowfield",
        "山顶": "mountain peak", "水下": "underwater", "废墟": "ruins",
        "舞台": "theater stage",
    },
    "主体": {
        "人类": "human", "哺乳动物": "mammal", "鸟类": "bird",
        "水生动物": "aquatic animal", "昆虫": "insect",
        "机器人": "robot", "车辆": "vehicle",
        "自然元素": "natural element", "日常物品": "everyday object",
        "虚构生物": "fictional creature",
    },
    "物理属性.状态": {
        "固体": "solid", "液体": "liquid", "气体": "gas",
        "等离子": "plasma", "刚体": "rigid body", "非刚体": "soft body",
        "颗粒": "particle",
    },
    "物理属性.规则": {
        "现实": "real-world", "科幻": "sci-fi", "魔幻": "fantasy", "梦境": "dreamlike",
    },
    "物理属性.纹理": {
        "光滑": "smooth", "粗糙": "rough", "毛发": "hair/fur",
        "羽毛": "feathered", "金属": "metallic", "木质": "wooden",
        "布料": "fabric", "玻璃": "glass", "石质": "stone", "皮革": "leather",
    },
    "物理属性.透光度": {
        "透明": "transparent", "半透明": "semi-transparent", "不透明": "opaque",
    },
    "物理属性.尺度": {
        "微观": "microscopic", "常规": "normal scale", "巨型": "giant",
    },
    "空间布局": {
        "上下": "vertical", "左右": "left-right",
        "前后": "foreground-background", "内外": "inside-outside",
        "环绕": "encircling", "对角": "diagonal",
        "层叠": "layered/stacked", "散落": "scattered",
    },
    "动作": {
        "走路": "walking", "跑步": "running", "跳跃": "jumping",
        "打斗": "fighting", "后空翻": "backflip", "武术": "martial arts",
        "跳舞": "dancing", "游泳": "swimming", "攀爬": "climbing",
        "骑行": "cycling", "驾驶": "driving",
        "烹饪": "cooking", "书写": "writing", "弹奏乐器": "playing instrument",
        "投掷": "throwing", "拥抱": "hugging", "鞠躬": "bowing",
        "对话": "dialogue", "唱歌": "singing",
        "倒下": "falling", "悬浮": "hovering", "旋转": "spinning",
        "挥手": "waving", "无": "none",
    },
    "表情": {
        "喜:强": "strong joy", "喜:中": "moderate joy", "喜:弱": "subtle joy",
        "怒:强": "strong anger", "怒:中": "moderate anger", "怒:弱": "subtle anger",
        "哀:强": "strong sadness", "哀:中": "moderate sadness", "哀:弱": "subtle sadness",
        "乐:强": "strong delight", "乐:中": "moderate delight", "乐:弱": "subtle delight",
        "惊:强": "strong surprise", "惊:中": "moderate surprise", "惊:弱": "subtle surprise",
        "恐:强": "strong fear", "恐:中": "moderate fear", "恐:弱": "subtle fear",
        "厌:强": "strong disgust", "厌:中": "moderate disgust", "厌:弱": "subtle disgust",
        "无": "none",
    },
    "特效": {
        "爆炸": "explosion", "光效": "light effect", "火焰": "flame",
        "烟雾": "smoke", "雨": "rain", "雪": "snow",
        "闪电": "lightning", "魔法粒子": "magic particles", "全息投影": "hologram",
        "碎裂": "shattering", "水花": "water splash", "无": "none",
    },
    "灯光.色调": {
        "暖光": "warm", "冷光": "cool", "中性": "neutral", "彩色混合": "multi-color",
    },
    "灯光.方向": {
        "顺光": "front light", "侧光": "side light", "逆光": "backlight",
        "顶光": "top light", "底光": "under light", "环境光": "ambient light",
    },
    "灯光.强度": {
        "高调": "high-key", "低调": "low-key", "正常": "normal",
    },
    "色彩.饱和度": {
        "高饱和": "high saturation", "低饱和": "low saturation", "去色": "desaturated",
    },
    "色彩.主色调": {
        "暖色系": "warm palette", "冷色系": "cool palette",
        "互补色": "complementary", "单色系": "monochromatic",
    },
    "相机.角度": {
        "俯拍": "high angle", "仰拍": "low angle", "平拍": "eye level",
        "鸟瞰": "bird's eye", "荷兰角": "dutch angle",
    },
    "相机.运镜": {
        "推": "push in", "拉": "pull out", "摇": "pan", "移": "truck",
        "跟": "tracking shot", "升降": "crane", "环绕": "orbit",
        "手持": "handheld", "甩": "whip pan", "静止": "static",
    },
    "相机.构图": {
        "三分法": "rule of thirds", "对称": "symmetrical",
        "引导线": "leading lines", "中心构图": "center framing",
        "框架构图": "frame within frame", "黄金螺旋": "golden spiral",
    },
    "相机.时间": {
        "常规速度": "real-time", "慢动作": "slow motion",
        "延时摄影": "timelapse", "倒放": "reverse", "定格": "freeze frame",
    },
    "相机.景别": {
        "特写": "extreme close-up", "近景": "close-up", "中景": "medium shot",
        "全景": "full shot", "远景": "long shot", "大远景": "extreme long shot",
    },
    "相机.景深": {
        "浅景深": "shallow DOF", "深景深": "deep DOF", "全景深": "pan-focus",
    },
    "相机.焦距": {
        "广角": "wide-angle", "标准": "standard lens", "长焦": "telephoto", "微距": "macro",
    },
    "环境.时段": {
        "黎明": "dawn", "白天": "daytime", "黄昏": "dusk", "夜晚": "night",
    },
    "环境.天气": {
        "晴天": "clear", "阴天": "overcast", "雨天": "rainy", "雾天": "foggy", "雪天": "snowy",
    },
    "转场": {
        "硬切": "hard cut", "淡入淡出": "fade", "溶解": "dissolve",
        "擦除": "wipe", "匹配剪辑": "match cut", "无": "none",
    },
}


# ============================================================
# 5. Priority fields
# ============================================================

PRIMARY_FIELDS = {
    "subjects", "scenes", "action",
    "camera_movement", "shot_size", "camera_angle", "time_mode",
}
SECONDARY_FIELDS = {
    "effect", "emotion", "spatial_layout",
    "lighting_tone", "lighting_direction", "lighting_intensity",
    "composition", "depth_of_field", "focal_length",
}
STYLISTIC_FIELDS = {
    "style", "physical_state", "physical_rule", "texture", "opacity", "scale",
    "color_saturation", "color_palette", "time_of_day", "weather", "transition",
}


# ============================================================
# 6. Parsing and balancing utilities
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


def balanced_combo_pool(
    base_values: List[str], n: int, count_spec: Union[int, Tuple[int, int]],
) -> List[List[str]]:
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


def balanced_seq_pool(
    base_values: List[str], n: int, count_spec: Union[int, Tuple[int, int]],
) -> List[List[str]]:
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
# 7. Cinematic feasibility checks (expanded)
# ============================================================

def compute_cinematic_flags(sample: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    warnings: List[str] = []
    suggestions: List[str] = []

    shots = sample["相机"]["景别"]
    time_mode = sample["相机"]["时间"]
    emotion = sample["表情"]
    texture = sample["物理属性"]["纹理"]
    scale = sample["物理属性"]["尺度"]
    light_dir = sample["灯光"]["方向"]
    light_int = sample["灯光"]["强度"]
    effect = sample["特效"]
    action = sample["动作"]
    scenes = sample["场景"]
    dof = sample["相机"]["景深"]
    focal = sample["相机"]["焦距"]
    weather = sample["环境"]["天气"]
    time_of_day = sample["环境"]["时段"]
    cam_moves = sample["相机"]["运镜"]
    subjects = sample["主体"]
    shot_count = len(cam_moves)

    # ── Emotion × shot size ──
    if emotion != "无" and emotion.endswith("弱") and any(
        s in {"远景", "大远景", "全景"} for s in shots
    ):
        warnings.append("subtle emotion hard to verify in wide shots")
        suggestions.append("express weak emotion through posture or silhouette")

    # ── Texture × shot size × lighting ──
    if texture in {"毛发", "羽毛", "粗糙"} and any(
        s in {"远景", "大远景"} for s in shots
    ) and light_dir == "逆光":
        warnings.append("fine texture invisible in wide backlit shots")

    # ── Timelapse conflicts ──
    if time_mode == "延时摄影" and emotion != "无":
        warnings.append("timelapse reduces readable emotional acting")
    if time_mode == "延时摄影" and action in {
        "对话", "唱歌", "打斗", "武术", "弹奏乐器", "跳舞",
    }:
        issues.append("timelapse conflicts with performance-heavy action")

    # ── Freeze frame + action ──
    if time_mode == "定格" and action not in {"无", "悬浮"}:
        warnings.append("freeze frame reduces action readability")

    # ── Indoor weather effects ──
    indoor_scenes = {"客厅", "厨房", "办公室", "走廊", "仓库", "医院", "舞台"}
    if effect in {"雨", "雪", "闪电"} and all(s in indoor_scenes for s in scenes):
        issues.append(f"weather VFX '{effect}' conflicts with fully indoor setting")

    # ── Scene × action coherence ──
    if action == "游泳" and not any(s in {"水下", "海滩", "公园"} for s in scenes):
        warnings.append("swimming uncommon outside aquatic scenes")
    if action == "驾驶" and all(s in indoor_scenes for s in scenes):
        issues.append("driving conflicts with indoor room setting")
    if action == "攀爬" and all(s in {"沙漠", "海滩", "水下"} for s in scenes):
        warnings.append("climbing uncommon in flat/aquatic environments")
    if action == "骑行" and all(s in indoor_scenes for s in scenes):
        warnings.append("cycling indoors is unusual unless scripted")

    # ── Scale × shot size ──
    if scale == "微观" and any(s in {"远景", "全景", "大远景"} for s in shots):
        issues.append("microscopic subject invisible in wide shots")
        suggestions.append("use close-up or extreme close-up for micro subjects")
    if scale == "巨型" and all(s == "特写" for s in shots):
        warnings.append("extreme close-up may lose sense of giant scale")

    # ── Focal length × shot size ──
    if focal == "微距" and not any(s in {"特写", "近景"} for s in shots):
        warnings.append("macro lens typically paired with close framing")
    if focal == "广角" and all(s == "特写" for s in shots):
        warnings.append("wide-angle close-up causes perspective distortion")

    # ── DOF × multi-subject ──
    if dof == "浅景深" and len(subjects) > 2:
        warnings.append("shallow DOF may obscure multiple subjects")

    # ── Weather × scene ──
    if weather == "雪天" and any(s == "沙漠" for s in scenes):
        issues.append("snow weather conflicts with desert setting")
    if weather == "雨天" and any(s == "水下" for s in scenes):
        warnings.append("rain is invisible in underwater scenes")

    # ── Time of day × lighting ──
    if time_of_day == "夜晚" and light_int == "高调":
        warnings.append("high-key at night needs strong motivated light source")
    if time_of_day == "黎明" and light_dir == "顶光":
        warnings.append("top light unusual at dawn; sun is near horizon")

    # ── Multi-scene single-shot ──
    if shot_count == 1 and len(scenes) > 1:
        warnings.append("single-shot multi-scene requires continuous traversal")

    # ── Dialogue/singing in wide shots ──
    if action in {"对话", "唱歌"} and all(
        s in {"远景", "全景", "大远景"} for s in shots
    ):
        issues.append("dialogue/singing weakly testable in very wide framing")

    # ── Handheld × timelapse ──
    if "手持" in cam_moves and time_mode == "延时摄影":
        warnings.append("handheld movement unusual in timelapse")

    # ── Whip pan × slow motion ──
    if "甩" in cam_moves and time_mode == "慢动作":
        warnings.append("whip pan contradicts slow-motion intent")

    # ── Effect × style ──
    if sample["画风"] == "极简" and effect not in {"无", "光效"}:
        warnings.append("heavy VFX may conflict with minimalist style")

    # ── Score ──
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


# ============================================================
# 8. English normalization & compiler payload
# ============================================================

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
        "scale": m("物理属性.尺度", sample["物理属性"]["尺度"]),
        "spatial_layout": m("空间布局", sample["空间布局"]),
        "action": m("动作", sample["动作"]),
        "emotion": m("表情", sample["表情"]),
        "effect": m("特效", sample["特效"]),
        "lighting_tone": m("灯光.色调", sample["灯光"]["色调"]),
        "lighting_direction": m("灯光.方向", sample["灯光"]["方向"]),
        "lighting_intensity": m("灯光.强度", sample["灯光"]["强度"]),
        "color_saturation": m("色彩.饱和度", sample["色彩"]["饱和度"]),
        "color_palette": m("色彩.主色调", sample["色彩"]["主色调"]),
        "camera_angle": m("相机.角度", sample["相机"]["角度"]),
        "camera_movement": [m("相机.运镜", x) for x in sample["相机"]["运镜"]],
        "composition": m("相机.构图", sample["相机"]["构图"]),
        "time_mode": m("相机.时间", sample["相机"]["时间"]),
        "shot_size": [m("相机.景别", x) for x in sample["相机"]["景别"]],
        "depth_of_field": m("相机.景深", sample["相机"]["景深"]),
        "focal_length": m("相机.焦距", sample["相机"]["焦距"]),
        "time_of_day": m("环境.时段", sample["环境"]["时段"]),
        "weather": m("环境.天气", sample["环境"]["天气"]),
        "transition": m("转场", sample["转场"]),
    }


def build_tag_priority(sample_en: Dict[str, Any]) -> Dict[str, List[str]]:
    primary, secondary, stylistic = [], [], []
    for key in sample_en:
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
            "goal": (
                "compile tags into an evaluable storyboard testcase "
                "before compressing into a final video prompt"
            ),
            "rule": "preserve primary tags first, then secondary, then stylistic",
        },
    }


# ============================================================
# 9. Sample generation
# ============================================================

def generate_samples(
    n_per_level: int = 5, seed: int = 42, *, repair: bool = True,
) -> List[Dict[str, Any]]:
    random.seed(seed)
    all_samples: List[Dict[str, Any]] = []
    gid = 1

    for level_key in LEVEL_KEYS:
        n = n_per_level
        constraints = parse_difficulty(level_key)
        info = DIFFICULTY_DEFS[level_key]

        # ── Independent scalar attributes ──
        p_style      = balanced_pool(TAG_SCHEMA["画风"], n)
        p_effect     = balanced_pool(TAG_SCHEMA["特效"], n)
        p_layout     = balanced_pool(TAG_SCHEMA["空间布局"], n)
        p_phys_rule  = balanced_pool(TAG_SCHEMA["物理属性"]["规则"], n)
        p_light_tone = balanced_pool(TAG_SCHEMA["灯光"]["色调"], n)
        p_light_dir  = balanced_pool(TAG_SCHEMA["灯光"]["方向"], n)
        p_light_int  = balanced_pool(TAG_SCHEMA["灯光"]["强度"], n)
        p_color_sat  = balanced_pool(TAG_SCHEMA["色彩"]["饱和度"], n)
        p_color_pal  = balanced_pool(TAG_SCHEMA["色彩"]["主色调"], n)
        p_cam_angle  = balanced_pool(TAG_SCHEMA["相机"]["角度"], n)
        p_cam_comp   = balanced_pool(TAG_SCHEMA["相机"]["构图"], n)
        p_cam_time   = balanced_pool(TAG_SCHEMA["相机"]["时间"], n)
        p_cam_dof    = balanced_pool(TAG_SCHEMA["相机"]["景深"], n)
        p_cam_fl     = balanced_pool(TAG_SCHEMA["相机"]["焦距"], n)
        p_env_time   = balanced_pool(TAG_SCHEMA["环境"]["时段"], n)
        p_env_weath  = balanced_pool(TAG_SCHEMA["环境"]["天气"], n)

        # ── Combo / sequence attributes ──
        p_subject  = balanced_combo_pool(TAG_SCHEMA["主体"], n, constraints["subject_count"])
        p_scene    = balanced_combo_pool(TAG_SCHEMA["场景"], n, constraints["scene_count"])
        p_cam_move = balanced_seq_pool(TAG_SCHEMA["相机"]["运镜"], n, constraints["shot_count"])
        p_cam_shot = balanced_seq_pool(TAG_SCHEMA["相机"]["景别"], n, constraints["shot_count"])

        # ── Transition: meaningful only for multi-shot ──
        is_multi_shot = (
            isinstance(constraints["shot_count"], tuple) or constraints["shot_count"] > 1
        )
        p_transition = (
            balanced_pool(TAG_SCHEMA["转场"], n) if is_multi_shot
            else ["无"] * n
        )

        # ── Dependent attributes (balanced, then optionally repaired) ──
        raw = {
            attr: balanced_pool(_flat_domain(attr), n) for attr in DEPENDENT_ATTRS
        }
        if repair:
            valid = {
                attr: [get_valid_domain(p_subject[i], attr) for i in range(n)]
                for attr in DEPENDENT_ATTRS
            }
            resolved = {
                attr: repair_pool(raw[attr], valid[attr]) for attr in DEPENDENT_ATTRS
            }
        else:
            resolved = raw

        # ── Assemble samples ──
        for i in range(n):
            sample: Dict[str, Any] = {
                "id": gid,
                "难度等级": info["label"],
                "难度描述": info["desc"],
                "画风": p_style[i],
                "场景": p_scene[i],
                "主体": p_subject[i],
                "物理属性": {
                    "状态": resolved["物理属性.状态"][i],
                    "规则": p_phys_rule[i],
                    "纹理": resolved["物理属性.纹理"][i],
                    "透光度": resolved["物理属性.透光度"][i],
                    "尺度": resolved["物理属性.尺度"][i],
                },
                "空间布局": p_layout[i],
                "动作": resolved["动作"][i],
                "表情": resolved["表情"][i],
                "特效": p_effect[i],
                "灯光": {
                    "色调": p_light_tone[i],
                    "方向": p_light_dir[i],
                    "强度": p_light_int[i],
                },
                "色彩": {
                    "饱和度": p_color_sat[i],
                    "主色调": p_color_pal[i],
                },
                "相机": {
                    "角度": p_cam_angle[i],
                    "运镜": p_cam_move[i],
                    "构图": p_cam_comp[i],
                    "时间": p_cam_time[i],
                    "景别": p_cam_shot[i],
                    "景深": p_cam_dof[i],
                    "焦距": p_cam_fl[i],
                },
                "环境": {
                    "时段": p_env_time[i],
                    "天气": p_env_weath[i],
                },
                "转场": p_transition[i],
            }
            sample["v4_meta"] = {
                **compute_cinematic_flags(sample),
                **build_tag_priority(normalize_to_english(sample)),
            }
            all_samples.append(sample)
            gid += 1

    return all_samples


# ============================================================
# 10. Validation
# ============================================================

def validate_samples(samples: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    for s in samples:
        sid = s["id"]
        level = s["难度等级"][:2]
        constraints = parse_difficulty(level)
        subjects = s["主体"]

        def _check_count(
            actual: List[Any], spec: Union[int, Tuple[int, int]], dim: str,
        ) -> None:
            if isinstance(spec, int):
                if len(actual) != spec:
                    errors.append(f"#{sid} {dim}: expected {spec}, got {len(actual)}")
            else:
                lo, hi = spec
                if not (lo <= len(actual) <= hi):
                    errors.append(f"#{sid} {dim}: expected [{lo},{hi}], got {len(actual)}")

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
                errors.append(
                    f"#{sid} {attr}: '{node}' not in valid domain {sorted(valid_vals)}"
                )

        is_single = isinstance(constraints["shot_count"], int) and constraints["shot_count"] == 1
        if is_single and s["转场"] != "无":
            errors.append(f"#{sid} 转场: single-shot but transition='{s['转场']}'")

    return len(errors) == 0, errors


# ============================================================
# 11. CSV export
# ============================================================

CSV_FIELDS = [
    "id", "难度等级", "难度描述", "画风", "场景", "主体",
    "物理属性.状态", "物理属性.规则", "物理属性.纹理", "物理属性.透光度", "物理属性.尺度",
    "空间布局", "动作", "表情", "特效",
    "灯光.色调", "灯光.方向", "灯光.强度",
    "色彩.饱和度", "色彩.主色调",
    "相机.角度", "相机.运镜", "相机.构图", "相机.时间", "相机.景别",
    "相机.景深", "相机.焦距",
    "环境.时段", "环境.天气",
    "转场",
    "promptability_score", "promptability_bucket",
    "primary_tags", "secondary_tags", "stylistic_tags",
]


def flatten_sample(sample: Dict[str, Any]) -> Dict[str, str]:
    meta = sample.get("v4_meta", {})
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
        "物理属性.尺度": sample["物理属性"]["尺度"],
        "空间布局": sample["空间布局"],
        "动作": sample["动作"],
        "表情": sample["表情"],
        "特效": sample["特效"],
        "灯光.色调": sample["灯光"]["色调"],
        "灯光.方向": sample["灯光"]["方向"],
        "灯光.强度": sample["灯光"]["强度"],
        "色彩.饱和度": sample["色彩"]["饱和度"],
        "色彩.主色调": sample["色彩"]["主色调"],
        "相机.角度": sample["相机"]["角度"],
        "相机.运镜": " → ".join(sample["相机"]["运镜"]),
        "相机.构图": sample["相机"]["构图"],
        "相机.时间": sample["相机"]["时间"],
        "相机.景别": " → ".join(sample["相机"]["景别"]),
        "相机.景深": sample["相机"]["景深"],
        "相机.焦距": sample["相机"]["焦距"],
        "环境.时段": sample["环境"]["时段"],
        "环境.天气": sample["环境"]["天气"],
        "转场": sample["转场"],
        "promptability_score": str(meta.get("promptability_score", "")),
        "promptability_bucket": meta.get("promptability_bucket", ""),
        "primary_tags": " | ".join(meta.get("primary_tags", [])),
        "secondary_tags": " | ".join(meta.get("secondary_tags", [])),
        "stylistic_tags": " | ".join(meta.get("stylistic_tags", [])),
    }


# ============================================================
# 12. Schema stats helper
# ============================================================

def schema_stats() -> Dict[str, Any]:
    dims = 0
    total_vals = 0
    detail: Dict[str, int] = {}
    for key, value in TAG_SCHEMA.items():
        if isinstance(value, list):
            dims += 1
            total_vals += len(value)
            detail[key] = len(value)
        elif isinstance(value, dict):
            for subkey, subvalue in value.items():
                dims += 1
                total_vals += len(subvalue)
                detail[f"{key}.{subkey}"] = len(subvalue)
    return {
        "total_dimensions": dims,
        "total_unique_values": total_vals,
        "per_dimension": detail,
    }


# ============================================================
# 13. Constraint-aware sampling: formal analysis utilities
# ============================================================

def count_violations(sample: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a list of dependency-constraint violations in *sample*."""
    violations: List[Dict[str, Any]] = []
    subjects = sample["主体"]
    for attr in DEPENDENT_ATTRS:
        valid_vals = set(get_valid_domain(subjects, attr))
        node: Any = sample
        for p in attr.split("."):
            node = node[p]
        if node not in valid_vals:
            violations.append({
                "attribute": attr,
                "actual": node,
                "valid": sorted(valid_vals),
                "subjects": subjects,
            })
    return violations


def build_constraint_graph() -> Dict[str, Any]:
    """
    Formalize the dependency matrix as a Constraint Satisfaction Problem.

    CSP = (X, D, C)
      X  = 27 tag-dimension variables
      D_i = domain of variable x_i
      C  = conditional domain restrictions triggered by x_subject
    """
    variables: Dict[str, int] = {}
    for key, value in TAG_SCHEMA.items():
        if isinstance(value, list):
            variables[key] = len(value)
        elif isinstance(value, dict):
            for subkey, subvalue in value.items():
                variables[f"{key}.{subkey}"] = len(subvalue)

    dep_set = set(DEPENDENT_ATTRS)
    independent = sorted(set(variables) - dep_set - {"主体"})

    edges: List[Dict[str, Any]] = []
    for subject in TAG_SCHEMA["主体"]:
        for attr in DEPENDENT_ATTRS:
            full_sz = variables[attr]
            valid_sz = len(DEPENDENCY_MATRIX[subject][attr])
            edges.append({
                "subject": subject,
                "attribute": attr,
                "valid_count": valid_sz,
                "full_domain_size": full_sz,
                "restriction_ratio": round(1 - valid_sz / full_sz, 4),
            })

    per_subj: Dict[str, Dict[str, Any]] = {}
    for subject in TAG_SCHEMA["主体"]:
        freedom = 1.0
        for attr in DEPENDENT_ATTRS:
            freedom *= len(DEPENDENCY_MATRIX[subject][attr]) / variables[attr]
        per_subj[subject] = {
            "joint_valid_ratio": round(freedom, 8),
            "joint_valid_pct": round(freedom * 100, 4),
        }

    avg_restr = sum(e["restriction_ratio"] for e in edges) / len(edges)

    return {
        "formulation": "CSP = (X, D, C): 27 variables, conditional domain restrictions",
        "n_variables": len(variables),
        "n_independent": len(independent),
        "n_dependent": len(DEPENDENT_ATTRS),
        "n_subjects": len(TAG_SCHEMA["主体"]),
        "n_constraint_edges": len(edges),
        "condition_variable": "主体",
        "dependent_attributes": list(DEPENDENT_ATTRS),
        "independent_attributes": independent,
        "avg_restriction_ratio": round(avg_restr, 4),
        "per_subject_freedom": per_subj,
        "edges": edges,
    }


def analytical_violation_rate() -> Dict[str, Any]:
    """
    Compute P(≥1 violation) under naive uniform random sampling (closed-form).

    For subject s:  P(valid|s) = ∏_a |valid(s,a)| / |domain(a)|
    Overall:        P(valid)   = (1/|S|) Σ_s P(valid|s)
    """
    subjects = TAG_SCHEMA["主体"]
    p_subj = 1.0 / len(subjects)

    per_subject: Dict[str, Dict[str, Any]] = {}
    overall_p_valid = 0.0

    for subject in subjects:
        per_attr: Dict[str, Dict[str, float]] = {}
        p_all_valid = 1.0
        for attr in DEPENDENT_ATTRS:
            full_domain = _flat_domain(attr)
            valid = DEPENDENCY_MATRIX[subject][attr]
            pv = len(valid) / len(full_domain)
            p_all_valid *= pv
            per_attr[attr] = {
                "valid_count": len(valid),
                "domain_size": len(full_domain),
                "p_valid": round(pv, 4),
            }
        per_subject[subject] = {
            "per_attribute": per_attr,
            "p_all_valid": round(p_all_valid, 8),
            "p_violation": round(1 - p_all_valid, 8),
        }
        overall_p_valid += p_subj * p_all_valid

    return {
        "per_subject": per_subject,
        "overall_p_valid": round(overall_p_valid, 8),
        "overall_p_violation": round(1 - overall_p_valid, 8),
    }


# ============================================================
# 14. Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="sv-benchmark v4 sampler — cinema-grade tag taxonomy",
    )
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

    (out_dir / "tag_samples_v4.json").write_text(
        json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (out_dir / "compiler_payloads_v4.json").write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    with (out_dir / "tag_samples_v4.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows([flatten_sample(s) for s in samples])

    stats = schema_stats()
    summary = {
        "total_samples": len(samples),
        "schema": stats,
        "levels": {
            lk: sum(1 for s in samples if s["难度等级"] == DIFFICULTY_DEFS[lk]["label"])
            for lk in LEVEL_KEYS
        },
        "promptability_distribution": dict(
            Counter(s["v4_meta"]["promptability_bucket"] for s in samples)
        ),
    }
    (out_dir / "summary_v4.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
