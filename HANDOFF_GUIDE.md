# 对方团队操作指南 — sv-benchmark 下一步

## 1. Repo 概述

sv-benchmark 是一个**视频生成 benchmark 测试用例流水线**，包含 4 个阶段：

| 阶段 | 说明 | 关键文件 |
|------|------|---------|
| Stage 1: Tag 采样 | 本地脚本，不调 API，生成结构化标签组合 | `sampler/sampling_v3.py` |
| Stage 2: Testcase 编译 | 用 LLM 将标签组合编译成可评测的视频 storyboard | `prompts/testcase_compiler_*.txt` + `schemas/testcase_output_schema.json` |
| Stage 3: 质量打分 | LLM 对生成的 testcase 做 8 维度评分 | `prompts/testcase_judge_prompt.txt` |
| Stage 4: Metrics 分析 | 关键词检测，统计 18 个维度的覆盖率 | `analyzer/metrics_analyzer.py` |

---

## 2. 核心任务：Scale 生成 + 人工打标

**输入文件：** `outputs/compiled_testcases.json`（目前有 5 个示例 testcase，覆盖 S1–S5 难度）

### Step 1: 大规模生成 testcase

```bash
# 先跑 tag 采样（调整 n_per_level 控制每个难度级别的样本数）
cd sampler
python sampling_v3.py --n_per_level 200 --seed 42 --out_dir ../outputs
```

然后对 `outputs/compiler_payloads_v3.json` 中的每个 payload，调用 LLM 编译：

1. 加载 `prompts/testcase_compiler_system_prompt.txt` 作为 system message
2. 将 payload JSON 替换进 `prompts/testcase_compiler_user_prompt.txt` 的 `{{COMPILER_PAYLOAD_JSON}}` 占位符
3. 发送给 LLM
4. 用 `schemas/testcase_output_schema.json` 验证输出格式

> **模型选择：GPT 5.4 是我们的优选模型**，用于 testcase 编译（Stage 2）。它在结构化输出稳定性和 creativity / hallucination 平衡上表现最好。

### Step 2: 用生成的 testcase 生成视频

参考 `generate_videos.py` 的模式，用每个 testcase 的 `final_video_prompt` 字段调用视频生成模型。目前已支持：

- **Kling v3**（快影 API）
- **Seedance 2.0**（火山引擎 API）

可以按同样模式接入其他待测模型。

### Step 3: 人工打标（核心交付）

对每个生成的视频，基于 `analyzer/metrics_checklists.json` 中的 **18 个维度 checklist** 进行人工评测：

| 维度 | 对应字段 | 评测内容 |
|------|---------|---------|
| 画风 | style | 视频是否符合指定风格（写实/哥特/卡通） |
| 场景 | scenes | 场景是否正确（室内/室外） |
| 主体 | subjects | 主体是否出现且类型正确（人类/动物/物体） |
| 物理状态 | physical_state | 固体/液体/气体/刚体/非刚体表现是否合理 |
| 物理规则 | physical_rule | 是否符合现实/科幻设定 |
| 纹理 | texture | 光滑/毛发纹理是否正确 |
| 透光度 | opacity | 透明/半透明/不透明是否正确 |
| 空间布局 | spatial_layout | 上下/左右/前后/内外关系是否正确 |
| 动作 | action | 指定动作是否被执行 |
| 表情 | emotion | 表情是否可见且强度匹配 |
| 特效 | effect | 爆炸/光效等特效是否呈现 |
| 灯光色调 | lighting_tone | 暖光/冷光/中性是否符合 |
| 灯光方向 | lighting_direction | 顺光/侧光/逆光/顶光是否正确 |
| 相机角度 | camera_angle | 俯拍/仰拍/平拍是否正确 |
| 相机运镜 | camera_movement | 推/拉/摇/移/跟/升降/静止是否正确 |
| 构图 | composition | 三分法/对称/引导线是否体现 |
| 时间模式 | time_mode | 常规/慢动作/延时/倒放是否正确 |
| 景别 | shot_size | 远景/全景/中景/近景是否正确 |

每个 testcase 的 `coverage_notes.must_show` 字段中已列明具体检查项，标注人员需要**逐条检查**。

### Step 4: 跑 metrics 分析验证覆盖率

```bash
cd analyzer
python metrics_analyzer.py --testcases ../outputs/compiled_testcases.json
```

产出：
- `metrics_checklists.json` — 每个 testcase 激活了哪些维度
- `tag_distribution_by_level.json` — 各难度级别的维度覆盖分布

---

## 3. 关键约定

| 项目 | 约定 |
|------|------|
| **LLM 模型** | GPT 5.4（优选），用于 testcase 编译和质量判断 |
| **NSFW** | 不做此类场景，明确排除 |
| **输出格式** | 严格遵循 `schemas/testcase_output_schema.json`，不要自定义字段 |
| **难度分级** | S1–S5，每级的主体数/场景数/镜头数约束见 README |
| **视频时长** | 10–15 秒 |
| **语言** | 所有 prompt 和 testcase 内容为英文；Tag 原始定义为中文（CN→EN 映射表见 README） |

---

## 4. 人工打标交付格式

每个视频标注完成后，按以下 JSON 格式输出：

```json
{
  "testcase_id": "S1-1-realistic-indoor-human-explosion-walk",
  "model": "kling-v3",
  "video_path": "outputs/videos/S1-1-..._kling.mp4",
  "human_scores": {
    "style":              { "pass": true,  "note": "" },
    "scenes":             { "pass": true,  "note": "" },
    "subjects":           { "pass": true,  "note": "" },
    "physical_state":     { "pass": true,  "note": "" },
    "physical_rule":      { "pass": true,  "note": "" },
    "texture":            { "pass": true,  "note": "" },
    "opacity":            { "pass": true,  "note": "" },
    "spatial_layout":     { "pass": true,  "note": "" },
    "action":             { "pass": false, "note": "walking not clearly visible" },
    "emotion":            { "pass": true,  "note": "" },
    "effect":             { "pass": true,  "note": "" },
    "lighting_tone":      { "pass": true,  "note": "" },
    "lighting_direction": { "pass": true,  "note": "" },
    "camera_angle":       { "pass": true,  "note": "" },
    "camera_movement":    { "pass": true,  "note": "" },
    "composition":        { "pass": true,  "note": "" },
    "time_mode":          { "pass": true,  "note": "" },
    "shot_size":          { "pass": true,  "note": "" }
  },
  "overall_pass": false,
  "annotator": "标注人员姓名",
  "timestamp": "2026-04-16T10:00:00Z"
}
```

**说明：**
- 仅对该 testcase 中 **active 的维度** 需要评测（参考 `metrics_checklists.json` 中 `active: true` 的项）
- `note` 字段在 `pass: false` 时**必须填写**失败原因
- 所有标注结果汇总为一个 JSON 数组文件提交

---

## 5. 快速上手 Checklist

- [ ] Clone repo，阅读 README
- [ ] 安装依赖：`pip install httpx pyjwt python-dotenv`
- [ ] 跑一遍 `sampler/sampling_v3.py` 确认 tag 采样正常
- [ ] 用 GPT 5.4 对 `compiler_payloads_v3.json` 批量编译 testcase
- [ ] 用 `testcase_output_schema.json` 校验每个输出
- [ ] 调用视频生成模型，生成视频
- [ ] 按 18 维度 checklist 进行人工打标
- [ ] 提交标注结果 JSON
