# Hi Team! sv-benchmark 协作指南

感谢大家参与这个项目的协作！这份文档整理了基于这个 repo 的下一步操作流程，方便大家快速上手。如果有任何疑问，随时联系我们。

---

## 1. Repo 简介

sv-benchmark 是一个**视频生成 benchmark 测试用例流水线**，包含 5 个阶段：

| 阶段 | 说明 | 关键文件 |
|------|------|---------|
| Stage 1: Tag 采样 | 本地脚本，不调 API，生成结构化标签组合 | `sampler/sampling_v4.py`（v4，27 维度） |
| Stage 1b: 约束分析 | 量化 constraint repair 的有效性（ablation） | `sampler/constraint_analysis.py` |
| Stage 2: Testcase 编译 | 用 LLM 将标签组合编译成可评测的视频 storyboard | `prompts/testcase_compiler_*.txt` + `schemas/testcase_output_schema.json` |
| Stage 3: 质量打分 | LLM 对生成的 testcase 做 8 维度评分 | `prompts/testcase_judge_prompt.txt` |
| Stage 4: Metrics 分析 | 从 compiled testcase 的 `active_dimensions` 推导维度覆盖率 | `analyzer/metrics_analyzer.py` |

> **注意：** v3 的 `sampling_v3.py`（18 维度）仍保留作为 legacy baseline。新工作请使用 v4。

---

## 2. 你们的核心任务：Scale 生成 Testcase + 人工验证可行性

目标是**大规模生成 testcase，并由人工验证这些机器生成的 case 是否可行、合理**。不需要生成视频，重点在于确保 testcase 本身的质量。

**输入文件：** `outputs/compiled_testcases.json`（目前有 5 个示例 testcase，覆盖 S1–S5 难度，供参考）

### Step 1: 大规模生成 testcase

```bash
# 先跑 tag 采样（v4，27 维度；调整 n_per_level 控制每个难度级别的样本数）
cd sampler
python sampling_v4.py --n_per_level 200 --seed 42 --out_dir ../outputs

# （可选）跑约束分析，生成 ablation 数据用于论文
python constraint_analysis.py --n_per_level 200 --seed 42
```

然后对 `outputs/compiler_payloads_v4.json` 中的每个 payload，调用 LLM 编译：

1. 加载 `prompts/testcase_compiler_system_prompt.txt` 作为 system message
2. 将 payload JSON 替换进 `prompts/testcase_compiler_user_prompt.txt` 的 `{{COMPILER_PAYLOAD_JSON}}` 占位符
3. 发送给 LLM
4. 用 `schemas/testcase_output_schema.json` 验证输出格式（注意：v4 schema 要求输出包含 `active_dimensions` 数组和可选的 `environment_and_color` 字段）

> **模型选择：GPT 5.4 是我们的优选模型**，用于 testcase 编译（Stage 2）。它在结构化输出稳定性和 creativity / hallucination 平衡上表现最好。

### Step 2: 人工验证可行性（核心交付）

对每个 LLM 生成的 testcase，需要人工审核以下内容：

**A. 整体可行性判断**

- 这个 testcase 描述的场景是否能在 10–15 秒视频中合理呈现？
- story_logic 是否连贯、不矛盾？
- final_video_prompt 是否清晰、可被视频生成模型理解？

**B. 27 维度 Checklist 审核（v4）**

基于 `analyzer/metrics_checklists.json`，对每个 testcase 中 **active 的维度** 逐条检查。v4 在 v3 的 18 维度基础上新增了 9 个维度（标 ★）：

| 维度 | 对应字段 | 审核内容 |
|------|---------|---------|
| 画风 | style | testcase 是否正确体现了指定风格（写实/电影质感/哥特/卡通/动漫/水彩/油画/赛博朋克/黑白/超现实/极简/复古胶片） |
| 场景 | scenes | 场景设定是否合理（16 种具体场景：客厅/厨房/办公室/走廊/仓库/医院/城市街道/公园/森林/海滩/沙漠/雪地/山顶/水下/废墟/舞台） |
| 主体 | subjects | 主体描述是否清晰且类型正确（10 类：人类/哺乳动物/鸟类/水生动物/昆虫/机器人/车辆/自然元素/日常物品/虚构生物） |
| 物理状态 | physical_state | 物理状态描述是否合理（固体/液体/气体/等离子/刚体/非刚体/颗粒） |
| 物理规则 | physical_rule | 是否符合现实/科幻/魔幻/梦境设定 |
| 纹理 | texture | 纹理描述是否正确（光滑/粗糙/毛发/羽毛/金属/木质/布料/玻璃/石质/皮革） |
| 透光度 | opacity | 透光度描述是否正确（透明/半透明/不透明） |
| ★ 尺度 | scale | 主体尺度是否正确（微观/常规/巨型） |
| 空间布局 | spatial_layout | 空间关系是否清晰可视化（上下/左右/前后/内外/环绕/对角/层叠/散落） |
| 动作 | action | 动作描述是否明确、可执行（24 种动作） |
| 表情 | emotion | 表情描述是否可视化、强度匹配（7 种情绪 × 3 强度 + 无） |
| 特效 | effect | 特效描述是否合理可呈现（12 种特效） |
| 灯光色调 | lighting_tone | 灯光色调是否明确（暖光/冷光/中性/彩色混合） |
| 灯光方向 | lighting_direction | 灯光方向是否明确（顺光/侧光/逆光/顶光/底光/环境光） |
| ★ 灯光强度 | lighting_intensity | 灯光强度是否明确（高调/低调/正常） |
| ★ 色彩饱和度 | color_saturation | 饱和度是否正确（高饱和/低饱和/去色） |
| ★ 色彩主色调 | color_palette | 主色调是否正确（暖色系/冷色系/互补色/单色系） |
| 相机角度 | camera_angle | 相机角度是否明确（俯拍/仰拍/平拍/鸟瞰/荷兰角） |
| 相机运镜 | camera_movement | 运镜描述是否可执行（推/拉/摇/移/跟/升降/环绕/手持/甩/静止） |
| 构图 | composition | 构图要求是否可实现（三分法/对称/引导线/中心构图/框架构图/黄金螺旋） |
| 时间模式 | time_mode | 时间模式是否合理（常规/慢动作/延时/倒放/定格） |
| 景别 | shot_size | 景别是否明确（特写/近景/中景/全景/远景/大远景） |
| ★ 景深 | depth_of_field | 景深是否明确（浅景深/深景深/全景深） |
| ★ 焦距 | focal_length | 焦距是否合理（广角/标准/长焦/微距） |
| ★ 时段 | time_of_day | 环境时段是否明确（黎明/白天/黄昏/夜晚） |
| ★ 天气 | weather | 天气条件是否明确（晴天/阴天/雨天/雾天/雪天） |
| ★ 转场 | transition | 多镜头时转场方式是否合理（硬切/淡入淡出/溶解/擦除/匹配剪辑/无） |

每个 compiled testcase 的 `active_dimensions` 数组是维度覆盖的 **ground truth**——只有 compiler 实际保留在 `final_video_prompt` 中的维度才需要评测。`coverage_notes.must_show` 提供具体检查项供标注人员对照。

### Step 3: 跑 metrics 分析验证覆盖率

```bash
cd analyzer
# 推荐：双源模式（active_dimensions 优先，缺失时退化为 payload fallback）
python metrics_analyzer.py --payloads ../outputs/compiler_payloads_v4.json \
                           --compiled ../outputs/compiled_testcases.json

# 或：仅 payload 模式（compiled testcase 尚未生成时）
python metrics_analyzer.py --payloads ../outputs/compiler_payloads_v4.json
```

产出：
- `metrics_checklists.json` — 每个 testcase 激活了哪些维度（标注 `source: compiled` 或 `payload_fallback`）
- `tag_distribution_by_level.json` — 各难度级别的维度覆盖分布

---

## 3. 关键约定

| 项目 | 约定 |
|------|------|
| **LLM 模型** | GPT 5.4（优选），用于 testcase 编译和质量判断 |
| **NSFW** | 不做此类场景，明确排除 |
| **输出格式** | 严格遵循 `schemas/testcase_output_schema.json`，不要自定义字段 |
| **采样版本** | v4（27 维度、205 个值、10 类主体），v3 仅作 legacy baseline |
| **难度分级** | S1–S5，每级的主体数/场景数/镜头数约束见 README |
| **视频时长** | 10–15 秒 |
| **语言** | 所有 prompt 和 testcase 内容为英文；Tag 原始定义为中文（CN→EN 映射表见 README） |

---

## 4. 人工验证交付格式

每个 testcase 审核完成后，请按以下 JSON 格式输出：

```json
{
  "testcase_id": "S1-1-realistic-indoor-human-explosion-walk",
  "feasibility": {
    "is_feasible": true,
    "duration_ok": true,
    "story_coherent": true,
    "prompt_clear": true,
    "issues": []
  },
  "metric_review": {
    "style":              { "pass": true,  "note": "" },
    "scenes":             { "pass": true,  "note": "" },
    "subjects":           { "pass": true,  "note": "" },
    "physical_state":     { "pass": true,  "note": "" },
    "physical_rule":      { "pass": true,  "note": "" },
    "texture":            { "pass": true,  "note": "" },
    "opacity":            { "pass": true,  "note": "" },
    "scale":              { "pass": true,  "note": "" },
    "spatial_layout":     { "pass": true,  "note": "" },
    "action":             { "pass": false, "note": "backflip and walking simultaneously is unrealistic in 12s" },
    "emotion":            { "pass": true,  "note": "" },
    "effect":             { "pass": true,  "note": "" },
    "lighting_tone":      { "pass": true,  "note": "" },
    "lighting_direction": { "pass": true,  "note": "" },
    "lighting_intensity": { "pass": true,  "note": "" },
    "color_saturation":   { "pass": true,  "note": "" },
    "color_palette":      { "pass": true,  "note": "" },
    "camera_angle":       { "pass": true,  "note": "" },
    "camera_movement":    { "pass": true,  "note": "" },
    "composition":        { "pass": true,  "note": "" },
    "time_mode":          { "pass": true,  "note": "" },
    "shot_size":          { "pass": true,  "note": "" },
    "depth_of_field":     { "pass": true,  "note": "" },
    "focal_length":       { "pass": true,  "note": "" },
    "time_of_day":        { "pass": true,  "note": "" },
    "weather":            { "pass": true,  "note": "" },
    "transition":         { "pass": true,  "note": "" }
  },
  "overall_pass": false,
  "annotator": "标注人员姓名",
  "timestamp": "2026-04-16T10:00:00Z"
}
```

**说明：**
- 仅对该 testcase 中 **active 的维度** 需要评测（ground truth 为 compiled testcase 的 `active_dimensions` 数组；也可参考 `metrics_checklists.json` 中 `active: true` 的项）
- `note` 字段在 `pass: false` 时**必须填写**具体问题描述
- `feasibility.issues` 记录整体层面的问题（如场景矛盾、时长不够等）
- 所有标注结果汇总为一个 JSON 数组文件提交

---

## 5. 快速上手 Checklist

- [ ] Clone repo，阅读 README
- [ ] 安装依赖：`pip install httpx pyjwt python-dotenv`
- [ ] 跑一遍 `sampler/sampling_v4.py` 确认 tag 采样正常（v4，27 维度）
- [ ] （可选）跑 `sampler/constraint_analysis.py` 生成约束分析数据
- [ ] 用 GPT 5.4 对 `compiler_payloads_v4.json` 批量编译 testcase
- [ ] 用 `testcase_output_schema.json` 校验每个输出
- [ ] 按 27 维度 checklist + 可行性维度进行人工审核
- [ ] 提交标注结果 JSON

---

## 6. v4 更新说明

v4 是 tag schema 的影视级扩展，核心变化如下：

**Tag Schema 扩展（18 → 27 维度）**
- 场景从 2 个（室内/室外）扩展到 **16 个**具体场景
- 主体从 3 类扩展到 **10 类**（新增鸟类/水生动物/昆虫/机器人/车辆/自然元素/虚构生物等）
- 动作从 9 个扩展到 **24 个**（新增跳舞/游泳/攀爬/烹饪/书写/弹奏乐器等）
- 新增 9 个维度：尺度、灯光强度、色彩饱和度、色彩主色调、景深、焦距、时段、天气、转场
- 详见 README 中的完整 tag 参考表

**Constraint-Aware Sampling（方向 B 研究贡献）**
- 依赖矩阵形式化为 CSP（约束满足问题）
- 新增 `constraint_analysis.py`，可生成 ablation 数据
- 核心发现：naive 随机采样有 **99.52%** 的组合违反约束；constraint repair 降至 **0%**，仅损失 2.75% 熵值
- 输出在 `analysis/` 文件夹下

**对协作的影响**
- 人工审核从 18 维度扩展到 **27 维度**（新增的 9 个维度标 ★，见上方 checklist）
- 标注 JSON 格式已更新，新增 9 个字段
- 使用 `sampling_v4.py` 替代 `sampling_v3.py` 进行采样
- Compiled testcase 新增 `active_dimensions` 字段（required）和 `environment_and_color` 字段（optional），标注时以 `active_dimensions` 为评测范围的 ground truth
- `metrics_analyzer.py` 不再使用关键词正则检测，改为直接读取 `active_dimensions`（双源策略：compiled testcase 优先，payload fallback）

如果在操作过程中遇到任何问题，欢迎随时沟通！
