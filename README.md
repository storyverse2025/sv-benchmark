# sv-benchmark

Benchmark testcase generation pipeline for video generation models.

**v2 was a sampler. v3 is a full pipeline: sampler + testcase compiler + judge. v4 is a cinema-grade taxonomy expansion with constraint-aware sampling analysis.**

---

## What This Repo Does

Generates standardized, evaluable video testcases by:
1. Sampling structured tag combinations across difficulty levels
2. Compiling them into storyboard-style testcases via LLM
3. Judging testcase quality via LLM

This enables fair, repeatable comparison of video generation models.

---

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: Tag Sampling  (local, no API)                    │
│                    sampler/sampling_v3.py                                    │
│                                                                             │
│  TAG_SCHEMA ──▶ Balanced Sampling ──▶ Dependency Repair ──▶ Feasibility    │
│  (CN tags)      (per S1–S5)           (matrix-based)       Check + Score   │
│                                                                             │
│                         ┌─────────────────────┐                             │
│                         │  CN → EN Translation │                             │
│                         │  + Payload Assembly  │                             │
│                         └──────────┬──────────┘                             │
│                                    ▼                                        │
│                     outputs/                                                │
│                     ├── tag_samples_v3.json      (CN raw samples)           │
│                     ├── tag_samples_v3.csv       (CN flat for review)       │
│                     ├── compiler_payloads_v3.json (EN, LLM-ready)          │
│                     └── summary_v3.json          (stats)                   │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │  Feed each payload object
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 2: Testcase Compilation  (LLM)                      │
│                                                                             │
│  System prompt:  prompts/testcase_compiler_system_prompt.txt                │
│  User template:  prompts/testcase_compiler_user_prompt.txt                  │
│                  └── insert payload into {{COMPILER_PAYLOAD_JSON}}          │
│  Output schema:  schemas/testcase_output_schema.json                       │
│                                                                             │
│  LLM produces per sample:                                                   │
│  { testcase_id, core_intent, duration_seconds, story_logic,                │
│    shot_plan: [...], final_video_prompt, negative_prompt,                   │
│    coverage_notes: { must_show, soft_interpretations, tradeoffs },         │
│    active_dimensions: ["style", "scenes", ...] }                           │
│                                                                             │
│  Example:  examples/example_compiler_payload.json                          │
│         → examples/example_compiled_testcase.json                          │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │  Feed payload + testcase
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 3: Quality Judging  (LLM)                           │
│                                                                             │
│  Prompt:  prompts/testcase_judge_prompt.txt                                │
│                                                                             │
│  Scores 8 dimensions (1–5):                                                │
│  schema_validity · english_naturalness · tag_coverage ·                    │
│  visual_observability · cinematic_feasibility · story_coherence ·          │
│  shot_continuity · prompt_model_usability                                  │
│                                                                             │
│  Hard-fail: invalid schema | not 10–15s | wrong shot count |               │
│             missing primary tags | prose-only output                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Repo Structure

```
sv-benchmark/
├── sampler/
│   ├── sampling_v3.py              # v3 tag sampling (18 dimensions, legacy)
│   ├── sampling_v4.py              # v4 cinema-grade sampling (27 dimensions)
│   └── constraint_analysis.py      # Constraint-aware sampling ablation
├── analyzer/
│   └── metrics_analyzer.py         # Metric detection (active_dimensions + payload)
├── prompts/
│   ├── testcase_compiler_system_prompt.txt
│   ├── testcase_compiler_user_prompt.txt
│   └── testcase_judge_prompt.txt
├── schemas/
│   └── testcase_output_schema.json  # JSON Schema for compiler output
├── examples/
│   ├── example_compiler_payload.json
│   └── example_compiled_testcase.json
├── outputs/                         # Generated data (gitignored for large runs)
│   ├── tag_samples_v4.json
│   ├── tag_samples_v4.csv
│   ├── compiler_payloads_v4.json
│   └── summary_v4.json
├── analysis/                        # Constraint analysis outputs
│   ├── constraint_graph.json
│   └── ablation_report.json
└── README.md
```

---

## Quick Start

### Step 1 — Sample tags

```bash
cd sampler
python sampling_v3.py --n_per_level 40 --seed 42 --out_dir ../outputs
```

### Step 2 — Compile testcases (LLM)

For each object in `outputs/compiler_payloads_v3.json`:
1. Load `prompts/testcase_compiler_system_prompt.txt` as system message
2. Substitute the payload into `{{COMPILER_PAYLOAD_JSON}}` in `prompts/testcase_compiler_user_prompt.txt`
3. Send to LLM
4. Validate output against `schemas/testcase_output_schema.json`

### Step 3 — Judge quality (LLM)

Feed the compiler payload + generated testcase into `prompts/testcase_judge_prompt.txt`.

---

## Stage Details

### Stage 1: Tag Sampling

**What it does:**
1. Defines a Chinese tag schema (source of truth) covering style, scene, subject, physics, action, emotion, effects, lighting, camera
2. Samples balanced tag combinations across 5 difficulty levels
3. Auto-repairs invalid combinations via dependency matrix (e.g., objects can't have emotions)
4. Runs cinematic feasibility checks and tags visibility warnings
5. Assigns tag priorities: primary / secondary / stylistic
6. Computes a promptability score per sample
7. Translates to English and assembles compiler-ready payloads

#### Difficulty Levels

| Level | Label | Subject | Scene | Shot |
|-------|-------|---------|-------|------|
| S1 | 最简 (Simplest) | 1 | 1 | 1 |
| S2 | 简单 (Simple) | 1 | 2–3 | 1 |
| S3 | 中等 (Medium) | 2–3 | 1 | 1 |
| S4 | 复杂 (Complex) | 2–3 | 2–3 | 1 |
| S5 | 极复杂 (Very Complex) | 2–3 | 2–3 | 2–3 |

#### Promptability Scoring

| Bucket | Score | Meaning |
|--------|-------|---------|
| `easy_to_visualize` | >= 0.80 | Safe to batch-compile |
| `borderline` | 0.55–0.79 | May need stronger model or manual review |
| `conflict_heavy` | < 0.55 | Route to human review or strongest model |

#### Tag Priorities

| Priority | Rule | Fields |
|----------|------|--------|
| **primary** | Do not drop | subject(s), scene(s), action, camera_movement, shot_size, camera_angle, time_mode |
| **secondary** | Preserve if visually testable | effect, emotion, spatial_layout, lighting_tone, lighting_direction, composition |
| **stylistic** | Keep if no harm to clarity | style, physical_state, physical_rule, texture, opacity |

### Stage 2: Testcase Compilation

**Compiler priorities (in order):**
1. Cinematic feasibility
2. Visual observability of the tags
3. Story coherence
4. Faithful tag coverage
5. Natural English phrasing

The compiler produces a structured testcase with `shot_plan`, `final_video_prompt`, `negative_prompt`, `coverage_notes`, and `active_dimensions` — not raw prose. The `active_dimensions` array lists which of the 27 tag dimensions were actually preserved in the final prompt; it serves as the ground truth for downstream metric evaluation.

### Stage 3: Quality Judging

| Dimension | What it measures |
|-----------|-----------------|
| `schema_validity` | Output conforms to JSON schema |
| `english_naturalness` | Reads like fluent English |
| `tag_coverage` | All required tags represented |
| `visual_observability` | Tags are visually checkable, not just implied |
| `cinematic_feasibility` | Scene filmable in 10–15s |
| `story_coherence` | Narrative sense |
| `shot_continuity` | Shots flow naturally |
| `prompt_model_usability` | Final prompt works for video gen models |

---

## Complete Tag Reference (CN → EN)

### 画风 (Style)

| Chinese | English |
|---------|---------|
| 写实 | realistic |
| 哥特 | gothic |
| 卡通 | cartoon |

### 场景 (Scene)

| Chinese | English |
|---------|---------|
| 室内 | indoor |
| 室外 | outdoor |

### 主体 (Subject)

| Chinese | English |
|---------|---------|
| 人类 | human |
| 动物 | animal |
| 物体 | object |

### 物理属性 (Physical Properties)

**状态 (State)**

| Chinese | English |
|---------|---------|
| 固体 | solid |
| 液体 | liquid |
| 气体 | gas |
| 刚体 | rigid |
| 非刚体 | non-rigid |

**规则 (Rule)**

| Chinese | English |
|---------|---------|
| 现实 | real-world |
| 科幻 | sci-fi |

**纹理 (Texture)**

| Chinese | English |
|---------|---------|
| 光滑 | smooth |
| 毛发 | hair/fur |

**透光度 (Opacity)**

| Chinese | English |
|---------|---------|
| 透明 | transparent |
| 半透明 | semi-transparent |
| 不透明 | opaque |

### 空间布局 (Spatial Layout)

| Chinese | English |
|---------|---------|
| 上下 | vertical relation |
| 左右 | left-right relation |
| 前后 | foreground-background relation |
| 内外关系 | inside-outside relation |

### 动作 (Action)

| Chinese | English |
|---------|---------|
| 走路 | walking |
| 跑步 | running |
| 跳跃 | jumping |
| 打斗 | fighting |
| 后空翻 | backflip |
| 武术 | martial arts |
| 对话 | dialogue |
| 唱歌 | singing |
| 无 | none |

### 表情 (Emotion)

| Chinese | English |
|---------|---------|
| 喜:强 | strong joy |
| 喜:中 | moderate joy |
| 喜:弱 | subtle joy |
| 怒:强 | strong anger |
| 怒:中 | moderate anger |
| 怒:弱 | subtle anger |
| 哀:强 | strong sadness |
| 哀:中 | moderate sadness |
| 哀:弱 | subtle sadness |
| 乐:强 | strong delight |
| 乐:中 | moderate delight |
| 乐:弱 | subtle delight |
| 无 | none |

### 特效 (Effect)

| Chinese | English |
|---------|---------|
| 爆炸 | explosion |
| 光效 | light effect |
| 无 | none |

### 灯光 (Lighting)

**色调 (Tone)**

| Chinese | English |
|---------|---------|
| 暖光 | warm |
| 冷光 | cool |
| 中性 | neutral |

**方向 (Direction)**

| Chinese | English |
|---------|---------|
| 顺光 | front light |
| 侧光 | side light |
| 逆光 | backlight |
| 顶光 | top light |

### 相机 (Camera)

**角度 (Angle)**

| Chinese | English |
|---------|---------|
| 俯拍 | high angle |
| 仰拍 | low angle |
| 平拍 | eye level |

**运镜 (Movement)**

| Chinese | English |
|---------|---------|
| 推 | push in |
| 拉 | pull out |
| 摇 | pan |
| 移 | truck |
| 跟 | tracking shot |
| 升降 | crane |
| 静止 | static |

**构图 (Composition)**

| Chinese | English |
|---------|---------|
| 三分法 | rule of thirds |
| 对称 | symmetrical |
| 引导线 | leading lines |

**时间 (Time Mode)**

| Chinese | English |
|---------|---------|
| 常规速度 | real-time |
| 慢动作 | slow motion |
| 延时摄影 | timelapse |
| 倒放 | reverse motion |

**景别 (Shot Size)**

| Chinese | English |
|---------|---------|
| 远景 | long shot |
| 全景 | full shot |
| 中景 | medium shot |
| 近景 | close shot |

---

## Subject Dependency Matrix

Not all tag combinations are valid. The sampler auto-repairs based on:

| Subject | State | Texture | Opacity | Action | Emotion |
|---------|-------|---------|---------|--------|---------|
| **Human** | solid | smooth, hair/fur | opaque | all 8 + none | all 12 + none |
| **Animal** | solid | smooth, hair/fur | opaque | walk, run, jump, fight, none | subtle only + none |
| **Object** | all 5 | smooth | all 3 | none | none |

---

## v2 → v3 Evolution

We kept the v2 core sampling logic intact and added layers on top:

1. **Preserved v2 sampling logic** — difficulty tiers, balanced pool, dependency matrix, repair — all unchanged.

2. **Added feasibility / testability layer** — beyond tag validity, v3 checks whether a tag combo can produce a coherent, evaluable 10–15s video. Added `promptability_score`, visibility warnings, and feasibility checks to filter out hard-to-evaluate samples.

3. **English-normalized compiler payloads** — since we focus on English scenarios, added a CN→EN mapping layer so payloads can be directly fed to GPT / Gemini / Claude for testcase generation.

4. **Three-stage pipeline instead of one-shot** — moved from "tags → prose prompt" to `sampling → compiler → judge`. Generate a structured testcase first, then compress into a final video prompt.

5. **Added schemas and prompts** — compiler system prompt, compiler user prompt, judge prompt, output JSON schema, example cases. Enables batch runs and automated evaluation.

---

## v3 → v4 Evolution

v4 is a **cinema-grade taxonomy expansion** grounded in professional film/animation production references:

| Source Domain | Reference |
|---|---|
| Visual Style | Art direction / production design categories |
| Camera | ASC Cinematographer's Manual; Brown, *Cinematography: Theory & Practice* |
| Action | Williams, *The Animator's Survival Kit* |
| Emotion | Ekman's 6 basic emotions + valence-arousal model |
| Color | Van Hurkman, *Color Correction Handbook* |
| Lighting | Three-point + motivated lighting theory |
| Environment | Script-supervisor continuity practice |
| Transition | Dmytryk, *On Film Editing* |

### Schema expansion summary

| | v3 | v4 | Change |
|---|---|---|---|
| **Evaluation dimensions** | 18 | **27** | +50% |
| **Unique tag values** | ~73 | **205** | +181% |
| **Scene types** | 2 | **16** | indoor/outdoor → 16 locations |
| **Subject types** | 3 | **10** | human/animal/object → 10 asset classes |
| **Actions** | 9 | **24** | +dancing, swimming, climbing, cooking, ... |
| **Emotions** | 13 | **22** | +surprise, fear, disgust (Ekman's 6) |
| **Effects** | 3 | **12** | +flame, smoke, rain, snow, lightning, ... |
| **Dependency matrix** | 3×5 | **10×6** | 10 subject types × 6 dependent attributes |
| **Feasibility rules** | 7 | **17** | cross-dimension conflict checks |

### 9 new dimensions in v4

| Dimension | CN | Grounding | Values |
|---|---|---|---|
| Scale | 物理属性.尺度 | VFX scale pipeline | microscopic, normal, giant |
| Light Intensity | 灯光.强度 | Three-point lighting | high-key, low-key, normal |
| Color Saturation | 色彩.饱和度 | Color grading theory | high, low, desaturated |
| Color Palette | 色彩.主色调 | Color grading theory | warm, cool, complementary, monochromatic |
| Depth of Field | 相机.景深 | ASC manual | shallow DOF, deep DOF, pan-focus |
| Focal Length | 相机.焦距 | ASC manual | wide-angle, standard, telephoto, macro |
| Time of Day | 环境.时段 | Continuity practice | dawn, daytime, dusk, night |
| Weather | 环境.天气 | Continuity practice | clear, overcast, rainy, foggy, snowy |
| Transition | 转场 | Editorial grammar | hard cut, fade, dissolve, wipe, match cut, none |

### v4 Tag Priorities

| Priority | Rule | Fields |
|----------|------|--------|
| **primary** | Do not drop | subjects, scenes, action, camera_movement, shot_size, camera_angle, time_mode |
| **secondary** | Preserve if visually testable | effect, emotion, spatial_layout, lighting_tone, lighting_direction, lighting_intensity, composition, depth_of_field, focal_length |
| **stylistic** | Keep if no harm to clarity | style, physical_state, physical_rule, texture, opacity, scale, color_saturation, color_palette, time_of_day, weather, transition |

### v4 Subject Dependency Matrix (10 × 6)

| Subject | State | Texture | Opacity | Scale | Action | Emotion |
|---|---|---|---|---|---|---|
| **Human** | solid | smooth, hair/fur, fabric, leather | opaque | normal | all 24 | all 22 |
| **Mammal** | solid | smooth, hair/fur, leather | opaque | micro, normal, giant | walk, run, jump, fight, swim, climb, fall, spin, none | subtle only + none |
| **Bird** | solid | smooth, feathered | opaque | micro, normal | walk, jump, hover, spin, none | none |
| **Aquatic** | solid | smooth | opaque, semi-trans | micro, normal, giant | swim, jump, spin, hover, none | none |
| **Insect** | solid | smooth, rough | opaque, semi-trans | micro, normal | walk, jump, hover, climb, none | none |
| **Robot** | solid, rigid | smooth, metallic | opaque | normal, giant | walk, run, jump, fight, spin, hover, fall, wave, bow, none | none |
| **Vehicle** | solid, rigid | smooth, metallic | opaque | normal, giant | drive, spin, fall, none | none |
| **Natural Element** | liquid, gas, plasma, particle, soft | smooth, rough, glass | all 3 | micro, normal, giant | hover, spin, none | none |
| **Everyday Object** | solid, liquid, rigid, soft | smooth, rough, metallic, wooden, fabric, glass, stone | all 3 | micro, normal | spin, fall, hover, none | none |
| **Fictional Creature** | solid, soft, gas | smooth, rough, hair/fur, feathered, leather | all 3 | micro, normal, giant | walk, run, jump, fight, swim, climb, hover, spin, fall, none | subtle–moderate joy/anger, subtle sadness/delight/surprise/fear, none |

---

## Constraint-Aware Sampling Analysis

v4 formalizes the dependency matrix as a **Constraint Satisfaction Problem (CSP)** and quantifies the benefit of constraint repair.

### CSP Formulation

```
CSP = (X, D, C)
  X = 27 tag-dimension variables
  D = domain per variable (205 total values)
  C = 60 conditional domain restrictions (10 subjects × 6 dependent attributes)
```

The constraint graph is **star-shaped**: the subject variable is the hub, and 6 dependent attributes are spokes. The remaining 20 dimensions are independent (no cross-constraints).

### Why constraint repair matters

Run `sampler/constraint_analysis.py` to reproduce:

```bash
cd sampler
python constraint_analysis.py --n_per_level 200 --seed 42
```

**Analytical result (closed-form):** Under naive uniform random sampling, **99.52%** of samples violate at least one dependency constraint.

**Empirical result (N=1000):**

| Metric | Naive | Repaired |
|---|---|---|
| Violation rate | 94.6% | **0.0%** |
| Avg violations / sample | 2.72 | 0.00 |
| Mean normalized entropy | 0.976 | 0.949 |
| Entropy change | — | **−2.75%** |
| Valid + easy_to_visualize | 3.2% | **59.2%** |

**Conclusion:** Constraint repair eliminates all violations while preserving sampling diversity (only 2.75% entropy loss).

### Per-subject constraint tightness

| Subject | P(valid \| naive) | P(≥1 violation) |
|---|---|---|
| Fictional Creature | 3.6526% | 96.35% |
| Human | 0.6349% | 99.37% |
| Everyday Object | 0.2020% | 99.80% |
| Mammal | 0.1705% | 99.83% |
| Natural Element | 0.1218% | 99.88% |
| Robot | 0.0241% | 99.98% |
| Vehicle | 0.0096% | 99.99% |
| Aquatic Animal | 0.0090% | 99.99% |
| Insect | 0.0120% | 99.99% |
| Bird | 0.0060% | 99.99% |

---

## Suggested Model Routing

| Role | Recommendation |
|------|---------------|
| Main batch compiler | Most stable structured-output model |
| Difficult / rewrite pass | Stronger high-quality model |
| Judge | Separate model or second pass |
