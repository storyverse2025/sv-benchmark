# sv-benchmark

Benchmark testcase generation & evaluation pipeline for video generation models.

**v2 was a sampler. v3 added the full pipeline (sampler + compiler + judge). v4 expanded to a cinema-grade 27-dimension taxonomy with constraint-aware sampling. Now includes end-to-end video generation via Kling v3 and Seedance 2.0.**

---

## What This Repo Does

Generates standardized, evaluable video testcases and benchmarks video generation models by:
1. Sampling structured tag combinations across difficulty levels (27 dimensions, 205 values)
2. Compiling them into storyboard-style testcases via LLM
3. Judging testcase quality via LLM
4. Analyzing metric coverage via `active_dimensions`
5. **Generating actual videos** via Kling v3-omni and Volcengine Seedance 2.0, then comparing outputs

This enables fair, repeatable comparison of video generation models across multiple providers.

---

## Pipeline Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: Tag Sampling  (local, no API)                    │
│                    sampler/sampling_v4.py  (27 dimensions, 205 values)       │
│                                                                             │
│  TAG_SCHEMA ──▶ Balanced Sampling ──▶ Dependency Repair ──▶ Feasibility    │
│  (CN tags)      (per S1–S5)           (10×6 matrix)        Check + Score   │
│                                                                             │
│                         ┌─────────────────────┐                             │
│                         │  CN → EN Translation │                             │
│                         │  + Payload Assembly  │                             │
│                         └──────────┬──────────┘                             │
│                                    ▼                                        │
│                     outputs/                                                │
│                     ├── tag_samples_v4.json      (CN raw samples)           │
│                     ├── tag_samples_v4.csv       (CN flat for review)       │
│                     ├── compiler_payloads_v4.json (EN, LLM-ready)          │
│                     └── summary_v4.json          (stats)                   │
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
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │  compiled_testcases.json
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 4: Metrics Analysis  (local)                        │
│                    analyzer/metrics_analyzer.py                              │
│                                                                             │
│  Dual-source: active_dimensions (primary) + payload tags (fallback)        │
│                                                                             │
│  Output:                                                                    │
│  ├── metrics_checklists.json        (per-testcase active metrics)          │
│  └── tag_distribution_by_level.json (per-difficulty coverage)              │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │  final_video_prompt from each testcase
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STAGE 5: Video Generation  (API)                          │
│                    generate_videos.py                                        │
│                                                                             │
│  Reads compiled_testcases.json, submits final_video_prompt to:             │
│  ├── Kling v3-omni  (api-beijing.klingai.com, JWT auth)                    │
│  └── Seedance 2.0   (ark.cn-beijing.volces.com, Bearer token)             │
│                                                                             │
│  - All testcases submitted concurrently to both providers                  │
│  - Polls for completion, downloads mp4 files                               │
│  - Supports configurable duration (Kling: 3-15s, Seedance: 4-15s)         │
│                                                                             │
│  Output:  outputs/videos/                                                   │
│  ├── {testcase_id}_kling.mp4                                               │
│  ├── {testcase_id}_seedance.mp4                                            │
│  └── generation_results.json                                               │
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
│   ├── metrics_analyzer.py         # Metric detection (active_dimensions + payload)
│   ├── metrics_checklists.json     # Per-testcase active metrics + tag values
│   └── tag_distribution_by_level.json
├── prompts/
│   ├── testcase_compiler_system_prompt.txt
│   ├── testcase_compiler_user_prompt.txt
│   └── testcase_judge_prompt.txt
├── schemas/
│   └── testcase_output_schema.json  # JSON Schema for compiler output
├── examples/
│   ├── example_compiler_payload.json
│   └── example_compiled_testcase.json
├── outputs/
│   ├── tag_samples_v4.json          # CN raw samples
│   ├── tag_samples_v4.csv           # CN flat for review
│   ├── compiler_payloads_v4.json    # EN, LLM-ready
│   ├── summary_v4.json              # Stats
│   ├── compiled_testcases.json      # 5 compiled testcases (S1–S5)
│   └── videos/                      # Generated benchmark videos
│       ├── {testcase_id}_kling.mp4
│       ├── {testcase_id}_seedance.mp4
│       └── generation_results.json
├── analysis/                        # Constraint analysis outputs
│   ├── constraint_graph.json
│   └── ablation_report.json
├── generate_videos.py               # Stage 5: Kling + Seedance video generation
├── rerun_seedance.py                # Re-run Seedance only (e.g., fix duration)
├── HANDOFF_GUIDE.md
└── README.md
```

---

## Quick Start

### Step 1 — Sample tags

```bash
cd sampler
python sampling_v4.py --n_per_level 40 --seed 42 --out_dir ../outputs

# (Optional) Run constraint analysis for ablation data
python constraint_analysis.py --n_per_level 200 --seed 42
```

### Step 2 — Compile testcases (LLM)

For each object in `outputs/compiler_payloads_v4.json`:
1. Load `prompts/testcase_compiler_system_prompt.txt` as system message
2. Substitute the payload into `{{COMPILER_PAYLOAD_JSON}}` in `prompts/testcase_compiler_user_prompt.txt`
3. Send to LLM
4. Validate output against `schemas/testcase_output_schema.json`

### Step 3 — Judge quality (LLM)

Feed the compiler payload + generated testcase into `prompts/testcase_judge_prompt.txt`.

### Step 4 — Analyze metrics

```bash
cd analyzer
python metrics_analyzer.py --payloads ../outputs/compiler_payloads_v4.json \
                           --compiled ../outputs/compiled_testcases.json
```

### Step 5 — Generate videos

```bash
# Create .env with API keys (see below), then:
pip install httpx pyjwt python-dotenv
python generate_videos.py
```

Required `.env` file:
```
KLING_ACCESS_KEY=your_key
KLING_SECRET_KEY=your_secret
ARK_API_KEY=your_key
```

This submits all testcases to Kling v3-omni and Seedance 2.0 concurrently, polls for completion, and downloads the resulting mp4 files to `outputs/videos/`.

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

#### Tag Priorities (v4)

| Priority | Rule | Fields |
|----------|------|--------|
| **primary** | Do not drop | subjects, scenes, action, camera_movement, shot_size, camera_angle, time_mode |
| **secondary** | Preserve if visually testable | effect, emotion, spatial_layout, lighting_tone, lighting_direction, lighting_intensity, composition, depth_of_field, focal_length |
| **stylistic** | Keep if no harm to clarity | style, physical_state, physical_rule, texture, opacity, scale, color_saturation, color_palette, time_of_day, weather, transition |

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

### Stage 4: Metrics Analysis

Uses a **dual-source strategy** to determine which dimensions are active per testcase:
- **Primary**: reads `active_dimensions` from compiled testcases (compiler self-reports which tags it preserved)
- **Fallback**: infers from `compiler_payloads_v4.json` (all non-"none" tags treated as active)

Outputs per-testcase checklists and per-difficulty distribution stats.

### Stage 5: Video Generation

Submits `final_video_prompt` from each compiled testcase to two video generation providers concurrently:

| Provider | Model | API | Duration | Auth |
|----------|-------|-----|----------|------|
| Kling | v3-omni | api-beijing.klingai.com | 3–15s | JWT (HS256) |
| Seedance | 2.0 (doubao-seedance-2-0-260128) | ark.cn-beijing.volces.com | 4–15s | Bearer token |

- All testcases are submitted in parallel (both providers simultaneously)
- Automatically polls until completion, then downloads mp4 files
- `rerun_seedance.py` available for re-running Seedance only (e.g., to fix duration)

---

## Complete Tag Reference (CN → EN, v4)

### 画风 (Style) — 12 values

| Chinese | English |
|---------|---------|
| 写实 | photorealistic |
| 电影质感 | cinematic |
| 哥特 | gothic |
| 卡通 | cartoon |
| 日式动漫 | anime |
| 水彩 | watercolor |
| 油画 | oil painting |
| 赛博朋克 | cyberpunk |
| 黑白 | black & white |
| 超现实 | surrealist |
| 极简 | minimalist |
| 复古胶片 | vintage film |

### 场景 (Scene) — 16 values

| Chinese | English |
|---------|---------|
| 客厅 | living room |
| 厨房 | kitchen |
| 办公室 | office |
| 走廊 | hallway |
| 仓库 | warehouse |
| 医院 | hospital |
| 城市街道 | city street |
| 公园 | park |
| 森林 | forest |
| 海滩 | beach |
| 沙漠 | desert |
| 雪地 | snowfield |
| 山顶 | mountain peak |
| 水下 | underwater |
| 废墟 | ruins |
| 舞台 | theater stage |

### 主体 (Subject) — 10 values

| Chinese | English |
|---------|---------|
| 人类 | human |
| 哺乳动物 | mammal |
| 鸟类 | bird |
| 水生动物 | aquatic animal |
| 昆虫 | insect |
| 机器人 | robot |
| 车辆 | vehicle |
| 自然元素 | natural element |
| 日常物品 | everyday object |
| 虚构生物 | fictional creature |

### 物理属性 (Physical Properties)

**状态 (State) — 7 values**

| Chinese | English |
|---------|---------|
| 固体 | solid |
| 液体 | liquid |
| 气体 | gas |
| 等离子 | plasma |
| 刚体 | rigid body |
| 非刚体 | soft body |
| 颗粒 | particle |

**规则 (Rule) — 4 values**

| Chinese | English |
|---------|---------|
| 现实 | real-world |
| 科幻 | sci-fi |
| 魔幻 | fantasy |
| 梦境 | dreamlike |

**纹理 (Texture) — 10 values**

| Chinese | English |
|---------|---------|
| 光滑 | smooth |
| 粗糙 | rough |
| 毛发 | hair/fur |
| 羽毛 | feathered |
| 金属 | metallic |
| 木质 | wooden |
| 布料 | fabric |
| 玻璃 | glass |
| 石质 | stone |
| 皮革 | leather |

**透光度 (Opacity) — 3 values**

| Chinese | English |
|---------|---------|
| 透明 | transparent |
| 半透明 | semi-transparent |
| 不透明 | opaque |

**尺度 (Scale) — 3 values** (v4 new)

| Chinese | English |
|---------|---------|
| 微观 | microscopic |
| 常规 | normal scale |
| 巨型 | giant |

### 空间布局 (Spatial Layout) — 8 values

| Chinese | English |
|---------|---------|
| 上下 | vertical |
| 左右 | left-right |
| 前后 | foreground-background |
| 内外 | inside-outside |
| 环绕 | encircling |
| 对角 | diagonal |
| 层叠 | layered/stacked |
| 散落 | scattered |

### 动作 (Action) — 24 values

| Chinese | English |
|---------|---------|
| 走路 | walking |
| 跑步 | running |
| 跳跃 | jumping |
| 打斗 | fighting |
| 后空翻 | backflip |
| 武术 | martial arts |
| 跳舞 | dancing |
| 游泳 | swimming |
| 攀爬 | climbing |
| 骑行 | cycling |
| 驾驶 | driving |
| 烹饪 | cooking |
| 书写 | writing |
| 弹奏乐器 | playing instrument |
| 投掷 | throwing |
| 拥抱 | hugging |
| 鞠躬 | bowing |
| 对话 | dialogue |
| 唱歌 | singing |
| 倒下 | falling |
| 悬浮 | hovering |
| 旋转 | spinning |
| 挥手 | waving |
| 无 | none |

### 表情 (Emotion) — 22 values

7 emotions (Ekman's 6 + delight) × 3 intensities + none:

| Chinese | English |
|---------|---------|
| 喜:强/中/弱 | strong/moderate/subtle joy |
| 怒:强/中/弱 | strong/moderate/subtle anger |
| 哀:强/中/弱 | strong/moderate/subtle sadness |
| 乐:强/中/弱 | strong/moderate/subtle delight |
| 惊:强/中/弱 | strong/moderate/subtle surprise |
| 恐:强/中/弱 | strong/moderate/subtle fear |
| 厌:强/中/弱 | strong/moderate/subtle disgust |
| 无 | none |

### 特效 (Effect) — 12 values

| Chinese | English |
|---------|---------|
| 爆炸 | explosion |
| 光效 | light effect |
| 火焰 | flame |
| 烟雾 | smoke |
| 雨 | rain |
| 雪 | snow |
| 闪电 | lightning |
| 魔法粒子 | magic particles |
| 全息投影 | hologram |
| 碎裂 | shattering |
| 水花 | water splash |
| 无 | none |

### 灯光 (Lighting)

**色调 (Tone) — 4 values**

| Chinese | English |
|---------|---------|
| 暖光 | warm |
| 冷光 | cool |
| 中性 | neutral |
| 彩色混合 | multi-color |

**方向 (Direction) — 6 values**

| Chinese | English |
|---------|---------|
| 顺光 | front light |
| 侧光 | side light |
| 逆光 | backlight |
| 顶光 | top light |
| 底光 | under light |
| 环境光 | ambient light |

**强度 (Intensity) — 3 values** (v4 new)

| Chinese | English |
|---------|---------|
| 高调 | high-key |
| 低调 | low-key |
| 正常 | normal |

### 色彩 (Color) (v4 new)

**饱和度 (Saturation) — 3 values**

| Chinese | English |
|---------|---------|
| 高饱和 | high saturation |
| 低饱和 | low saturation |
| 去色 | desaturated |

**主色调 (Palette) — 4 values**

| Chinese | English |
|---------|---------|
| 暖色系 | warm palette |
| 冷色系 | cool palette |
| 互补色 | complementary |
| 单色系 | monochromatic |

### 相机 (Camera)

**角度 (Angle) — 5 values**

| Chinese | English |
|---------|---------|
| 俯拍 | high angle |
| 仰拍 | low angle |
| 平拍 | eye level |
| 鸟瞰 | bird's eye |
| 荷兰角 | dutch angle |

**运镜 (Movement) — 10 values**

| Chinese | English |
|---------|---------|
| 推 | push in |
| 拉 | pull out |
| 摇 | pan |
| 移 | truck |
| 跟 | tracking shot |
| 升降 | crane |
| 环绕 | orbit |
| 手持 | handheld |
| 甩 | whip pan |
| 静止 | static |

**构图 (Composition) — 6 values**

| Chinese | English |
|---------|---------|
| 三分法 | rule of thirds |
| 对称 | symmetrical |
| 引导线 | leading lines |
| 中心构图 | center framing |
| 框架构图 | frame within frame |
| 黄金螺旋 | golden spiral |

**时间 (Time Mode) — 5 values**

| Chinese | English |
|---------|---------|
| 常规速度 | real-time |
| 慢动作 | slow motion |
| 延时摄影 | timelapse |
| 倒放 | reverse |
| 定格 | freeze frame |

**景别 (Shot Size) — 6 values**

| Chinese | English |
|---------|---------|
| 特写 | extreme close-up |
| 近景 | close-up |
| 中景 | medium shot |
| 全景 | full shot |
| 远景 | long shot |
| 大远景 | extreme long shot |

**景深 (Depth of Field) — 3 values** (v4 new)

| Chinese | English |
|---------|---------|
| 浅景深 | shallow DOF |
| 深景深 | deep DOF |
| 全景深 | pan-focus |

**焦距 (Focal Length) — 4 values** (v4 new)

| Chinese | English |
|---------|---------|
| 广角 | wide-angle |
| 标准 | standard lens |
| 长焦 | telephoto |
| 微距 | macro |

### 环境 (Environment) (v4 new)

**时段 (Time of Day) — 4 values**

| Chinese | English |
|---------|---------|
| 黎明 | dawn |
| 白天 | daytime |
| 黄昏 | dusk |
| 夜晚 | night |

**天气 (Weather) — 5 values**

| Chinese | English |
|---------|---------|
| 晴天 | clear |
| 阴天 | overcast |
| 雨天 | rainy |
| 雾天 | foggy |
| 雪天 | snowy |

### 转场 (Transition) — 6 values (v4 new)

| Chinese | English |
|---------|---------|
| 硬切 | hard cut |
| 淡入淡出 | fade |
| 溶解 | dissolve |
| 擦除 | wipe |
| 匹配剪辑 | match cut |
| 无 | none |

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
