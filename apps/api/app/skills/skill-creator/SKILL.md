---
id: skill-creator
name: 技能创建师
description: >-
  引导创建/沉淀/迭代可复用的分析 Skill。当用户想「创建一个新技能」「把刚才这套方法做成技能」
  「沉淀这次的流程」「新建分析技能」「升级脚本为技能」时命中；先澄清需求，再判定形态并用
  create_skill 落盘、validate_skill 校验。
core_question: >-
  我想创建一个可复用的 Skill（分析技能）。请按 skill-creator 方法论引导我：
  先澄清我的需求，再判定带脚本/流程引导形态并用 create_skill 落盘、validate_skill 校验。
  开始吧——先问我第一个问题。
keywords:
  - 创建技能
  - 沉淀技能
  - 创建skill
  - skill-creator
  - 把方法存成技能
  - 新技能
  - 制作技能
  - 保存方法
  - 升级为技能
  - 生成技能
required_data: []
outputs:
  - 新技能目录（SKILL.md + skill.json + scripts/main.py）
  - 校验结果
  - 可复用分析能力
indicators:
  - name: 需求澄清
    data: 用户需求描述
    mvp_depth: 引导式
    status: ready
    steps:
      - 向用户提 1-2 个关键问题
      - 确认技能功能 / 触发词 / 所需数据
  - name: 形态判定
    data: 需求描述
    mvp_depth: 引导式
    status: ready
    steps:
      - 判断确定性计算 or 纯流程
      - 确定带脚本 / 流程引导形态
  - name: 落盘
    data: 技能定义
    mvp_depth: 真实计算
    status: ready
    steps:
      - create_skill(skill_id) 写入工作区 skills 目录
      - 写 SKILL.md frontmatter（触发词进 description）
      - 可执行形态写 scripts/main.py 并真跑
  - name: 校验
    data: 技能文件
    mvp_depth: 真实计算
    status: ready
    steps:
      - validate_skill(skill_id) 契约静态校验
      - execute_skill_script(skill_id) 实跑出真结果
---

# 技能创建师（skill-creator）

为 UrbanScope 创建 / 沉淀 / 迭代可复用分析技能的方法论（移植自 Anthropic 官方
skill-creator）。当用户说「创建/沉淀/生成一个技能」「把刚才这套方法存成技能」「新技能」时触发。

## 技能是什么

技能 = 一个目录，两种形态：

| 形态 | 结构 | 执行方式 | 什么时候用 |
|---|---|---|---|
| **带脚本** | SKILL.md + skill.json（契约）+ scripts/main.py | `execute_skill_script` 确定性跑代码 | 需要确定性计算 / 固定算法 / 反复改写同一段代码 |
| **流程引导** | 只有 SKILL.md（frontmatter + 正文） | Planner 命中后按提示编排工具 | 纯流程 / 模板 / 引导型分析专题 |

## 核心原则（写任何技能都遵守）

- **触发词写进 description，不是正文**：frontmatter 的 `name` + `description` 是技能唯一
  「被看到」的字段，是命中机制；正文只有命中后才加载。所以 description 必须同时写清
  「做什么 + 何时用」（含触发场景），正文里再写「何时用」没用。
- **简洁是美德（Concise is Key）**：上下文是公共资源。只写模型不知道的东西，能用例子
  说明就别长篇解释，逐句问「这句值得占 token 吗」。
- **自由度匹配**：确定性操作（易碎、要一致）给低自由度（具体脚本、少参数）；启发式流程
  给高自由度（文字指引）。不要给每一步都写死脚本。
- **渐进披露**：SKILL.md 正文保持精简（<500 行），细节放 references/ 按需读取；
  正文里用一行说明「何时读哪个 reference」。
- **不塞文档垃圾**：技能只装干活需要的文件。不要 README / CHANGELOG / 安装指南等
  与任务无关的辅助文档。
- **真计算，不编造**：产出图层/指标/统计必须真算，宁可多跑一次 execute_skill_script /
  execute_code 验证；演示口径全部真值。
- **平台红线**：可执行技能脚本只能读写工作区，platform_data 只读，写平台数据会被拒。

## 创建流程（六步）

### 第 1 步：理解需求（少而准地问）

先向用户提 1-2 个关键问题（一次别超过 2-3 个，避免轰炸）。可参考的问题：

- 这个技能要做什么？（功能）
- 用户什么样的话会触发它？（→ 将来写进 description 触发词）
- 需要哪些数据 / 期待什么输出？（图层 / 指标卡 / 图表）

对已有技能的迭代可以跳过，但「技能要做什么」的结论要落到一句话。

### 第 2 步：规划可复用内容

把使用场景拆成可复用的资源，判断需要哪些：

- **scripts/**：同一段代码反复改写 / 需要确定性 → 写 `scripts/main.py`
- **references/**：有需要在执行时参考的文档（schema、指标口径）→ 放 references/
- **assets/**：输出要用的模板/图标 → 放 assets/

### 第 3 步：落盘

用 `create_skill` 工具创建（id 用小写 kebab-case，如 `elderly-facility-check`），
写入 `.workdir/skills/<id>/`。可执行形态会生成 skill.json + scripts/main.py 骨架。

### 第 4 步：编辑

- 写 SKILL.md：frontmatter 只放 `name` + `description`（触发词，见上）；正文用
  祈使句写步骤，逐步披露细节。
- 可执行形态写 `scripts/main.py`：按 `--input`（input.json 的 params）→ `--output`
  契约读参数、产图层/统计（参考 kernel-density 示例）。
- **脚本必须真跑一次**确认无 bug、输出符合预期；同族脚本可只测代表样本。

### 第 5 步：校验

- `validate_skill(skill_id)`：契约静态校验（frontmatter / 命名 / 目录 / 代码语法）。
- 可执行形态 `execute_skill_script(skill_id, params)`：真实子进程跑出真结果，
  确认产物图层/指标符合预期后再交付。

### 第 6 步：迭代

放到真实任务上用，根据「哪里不顺」改 SKILL.md / 脚本，再测。用户常在刚用完技能、
上下文还热的时候提改进。

## 常用工具

- `create_skill(skill_id, name, description, ...)` — 落盘新技能（对应官方 init_skill.py）
- `edit_skill(skill_id, ...)` — 迭代修改
- `validate_skill(skill_id)` — 契约静态校验（对应官方 package_skill.py 的校验阶段）
- `execute_skill_script(skill_id, params)` — 确定性执行（可执行形态，对应官方运行）
- `promote_script_to_skill(script_name, ...)` — 把 execute_code 跑通的脚本提升为技能
- `list_skills()` / `get_skill(skill_id)` — 查看现有技能，避免重复造轮子

## 参考

- 输出格式要求严格（如指标口径统一）→ 在技能里写「固定模板」，不灵活；启发式输出 → 给默认格式但允许按场景调整。
- 输出质量依赖样例 → 在技能里给输入/输出成对示例，比纯文字描述更能对齐风格。
- 顺序流程 / 条件分支 → 在 SKILL.md 开头给一个流程总览，再分节展开。
