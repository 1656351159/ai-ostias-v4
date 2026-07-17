# AI-OSTIAS V4 技术手册（评审稿）

> 版本：v0.1-draft ｜ 状态：待评审 ｜ 日期：2026-07-17
> 本手册描述任务调度器第四版（V4）的技术方案：**V2（crawl4more）整理为独立 Skill，由 OpenClaw Runtime 调用，前端按《调研与方案》实现可视交互**。
> 请重点评审：第 4 章总体架构、第 5 章 Skill 边界、第 8 章前端方案是否符合预期。

---

## 1. 背景与版本演进

| 版本 | 形态 | 问题 / 结论 |
|---|---|---|
| V1 | 自研模拟 Runtime（自己写 Agent Loop、Prompt 拼接、Session 管理） | 重复造轮子，放弃 |
| V2 | crawl4more：单机 Python 时间片轮换多任务爬虫调度器（crawl4ai + LangChain + SQLite） | 调度内核验证通过，但无 Agent 编排、无 UI |
| V3 | 以 OpenClaw Runtime 为核心：Runtime Adapter → OpenClaw CLI → Research Agent，契约与安全门控完备 | **"不可看"**：零前端、零 Web 框架，唯一交互面是 CLI + evidence JSON，无法向用户展示执行过程 |
| **V4** | **OpenClaw Runtime 作为执行大脑，crawl4more 收敛为其可调用的独立 Skill，前端按《调研与方案》实现三阶段可视交互** | 本手册 |

**V4 要解决的核心问题**

1. V3 的能力链路（Adapter → Runtime → Agent）已经验证，保留；缺的是"脸"——用户看得见、可干预的界面。
2. V2 的调度能力（时间片轮换、Job 锁、URL 发现回填、审计日志）是已跑通的资产，不重写，**整理为最小核心文件的独立 Skill** 供 OpenClaw 调用。
3. 前端不是聊天窗，而是《调研与方案》定义的三阶段交互：任务配置 → 执行可视化 → 结果交付与人工干预。

**设计原则**

- OpenClaw 是大脑，前端是脸，Skill 是手；Orchestrator（后端服务）是连接三者的神经。
- 安全沿用 V3 哲学：工具硬策略在 Runtime/Agent 侧强制，Prompt 只是 advisory，任务无法越权。
- 数据库（SQLite 起步，可迁移 PostgreSQL）是前后端与执行层之间的**唯一状态事实源**，前端通过轮询数据库实现过程可视化。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| OpenClaw Runtime | Agent 执行基础设施：Agent Loop、Tool/Skill 装配、Prompt 装配、Session 管理 |
| Skill | 目录形式的技能包（SKILL.md + 支撑脚本），由 OpenClaw Agent 按指引调用 |
| Runtime Adapter | 业务层与 OpenClaw 之间的唯一接口（V3 已验证），`submit(task) -> RuntimeResult` |
| Orchestrator | 后端业务编排服务（FastAPI）：任务理解、工作流选择、状态管理、结果整合 |
| Job / SubTask | 作业（一次完整爬取请求）/ 子任务（单个 URL 处理单元），与 V2 数据模型一致 |
| 时间片轮换 | 多 Job 公平调度：Job 运行 slice_timeout 秒后挂起，轮换下一个 |

---

## 3. 总体架构

```
┌─────────────────────────────── 前端（Vue 3 + Element Plus）──────────────────────────────┐
│  标签页1 对话式任务     标签页2 自定义流程编排     标签页3 情报展示     标签页4 系统设置     │
│  （阶段一）            （阶段一）               （阶段三）          （配置/状态）         │
│        队列监控 + 任务控制（阶段二） ｜ 结果展示/筛选/导出/人工干预（阶段三）                 │
└──────────────────────────────┬───────────────────────────────────────────────────────────┘
                               │ REST（提交/查询/控制）+ 固定时间片轮询任务状态
┌──────────────────────────────▼───────────────────────────────────────────────────────────┐
│                     Orchestrator 后端服务（FastAPI）                                       │
│  任务理解（自然语言→任务参数 JSON）｜参数确认回写｜Job/SubTask CRUD｜人工干预（改库即控制）      │
│  状态查询 API（前端轮询）｜结果查询/导出 API                                                 │
└──────────────────────────────┬───────────────────────────────────────────────────────────┘
                               │ RuntimeAdapter.submit(task) -> RuntimeResult（V3 资产）
┌──────────────────────────────▼───────────────────────────────────────────────────────────┐
│                     OpenClaw Runtime（Gateway WebSocket/RPC 长连接，已决策）                 │
│  Research Agent（最小权限）── 读取 SKILL.md ── 调用 crawl4more Skill 入口脚本               │
└──────────────────────────────┬───────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────────────────────┐
│              crawl4more Skill（V2 整理版，独立目录，仅核心文件）                             │
│  TimeSliceScheduler → TaskManager（唯一 DB 访问层）→ CrawlerWorker（crawl4ai + LLM 抽取）    │
└──────────────────────────────┬───────────────────────────────────────────────────────────┘
                               │
                     ┌─────────▼─────────┐        ┌──────────────────┐
                     │ SQLite（状态事实源）│        │ LLM（抽取/分析）   │
                     │ jobs/subtasks/logs│        │ （兼容多厂商）     │
                     └───────────────────┘        └──────────────────┘
```

**关键链路说明**

- **提交链**：前端 → Orchestrator → Adapter → OpenClaw Agent → Skill。Agent 按 SKILL.md 指引执行 Skill 入口脚本，Skill 自主完成调度与爬取。
- **可视链**：Skill 运行中持续写 SQLite（Job/SubTask/日志状态）→ 前端固定时间片轮询 Orchestrator 查询 API → 实时展示。这直接解决 V3"不可看"的问题。
- **控制链**：前端修改数据库中 Job/SubTask 的状态或参数（如 max_pages）→ Scheduler 下一轮取任务时生效 → 实现"人在回路"。

---

## 4. 核心组件一：crawl4more Skill（V2 整理版）

### 4.1 保留的核心文件（仅这些进入 Skill 目录）

```
crawl4more-skill/
├── SKILL.md                      # 新增：技能说明与调用契约（见 4.3）
├── requirements.txt              # 修正版（见 4.4）
├── run.py                        # 新增：Skill 统一入口（原 start.py 简化改造）
├── models/
│   ├── job.py                    # CrawlJob 数据模型
│   ├── subtask.py                # CrawlSubtask 数据模型
│   └── extraction_schema.py      # LLM 抽取 schema + 科技情报抽取指令
├── services/
│   ├── task_manager.py           # 唯一 DB 访问层：CRUD、锁、去重、状态机、审计
│   └── scheduler.py              # 时间片轮换调度循环
├── crawler/
│   └── worker.py                 # crawl4ai 抓取 + LLM 抽取 + URL 发现回填
├── db/
│   ├── schema.sql                # 3 表 DDL
│   └── init_db.py                # 建库/清库
└── utils/
    └── llm_factory.py            # 多厂商 LLM 适配工厂
```

### 4.2 剔除的文件（不进入 Skill）

| 剔除项 | 原因 |
|---|---|
| `demo_main.py`、`testdemo.py`、`test_playwright.py` | 假数据演示 / 测试脚本 |
| `batch_submit_jobs.py` | 硬编码演示目标站，逻辑并入 `run.py` 参数化 |
| `check_db.py`、`view_json.py` | 开发辅助小工具 |
| `__pycache__/`、`db/crawler.db` | 运行产物 |
| `README.md`（原 2 行） | 由 SKILL.md 取代 |

### 4.3 代码改造清单（Skill 化必须完成的修复）

| # | 改造项 | 说明 |
|---|---|---|
| 1 | 修复 dotenv 未加载 | 现代码从不调用 `load_dotenv()`，`.env` 实际不生效；`run.py` 启动时显式加载 |
| 2 | 补依赖声明 | `requirements.txt` 缺 `langchain-openai`、`langchain-anthropic`（代码实际 import）；移除未使用的 `rich` |
| 3 | 删除重复函数 | `worker.py` 中 `_discover_new_urls()` 重复定义两次，删其一 |
| 4 | DB 路径参数化 | 数据库路径改为环境变量 / 启动参数，避免 Skill 目录内写运行产物 |
| 5 | 补 `__init__.py` | `models/`、`crawler/` 目前缺失，Skill 化后按正规包处理 |
| 6 | 清理 .env 冗余键 | `MODEL_NAME`（代码读 `AI_MODEL_NAME`）、`CRAWLER_LOAD_IMAGES`（代码硬编码禁图）删除或对齐 |
| 7 | 统一入口 `run.py` | 参数：`--start-url --max-pages --priority --slice-timeout --db-path --job-id`；支持"创建 Job 并运行调度器"与"仅加入队列"两种模式 |
| 8 | 结果汇总输出 | 运行结束（或达到 max_pages）后向 stdout 输出一份归一化 JSON 摘要（job 状态、已处理页数、抽取条数、失败原因），供 Adapter 解析 |

### 4.4 Skill 接口契约（SKILL.md 核心内容）

**触发条件**：任务类型为网页情报采集 / 多 URL 队列爬取 / 站点监控。

**输入**（由 OpenClaw Agent 按任务 JSON 组装为入口脚本参数）：

```json
{
  "job_id": "uuid，由 Orchestrator 生成并预先写入 Job 表",
  "start_url": "起始 URL",
  "extra_urls": ["可选，队列追加"],
  "max_pages": 15,
  "priority": 0,
  "slice_timeout": 30,
  "extraction_instruction": "可选，覆盖默认科技情报抽取指令"
}
```

**输出**（stdout 单行 JSON + 数据库落库）：

```json
{
  "job_id": "...",
  "status": "completed | failed | paused",
  "processed_pages": 12,
  "extracted_count": 8,
  "failed_subtasks": 1,
  "error": null
}
```

**副作用声明**：写入指定 SQLite 库（jobs/subtasks/logs 三表）；发起网络请求（需 `allow_network: true`）；启动无头 Chromium。

---

## 5. 核心组件二：OpenClaw 对 Skill 的调用

> 沿用 V3 已验证的 `RuntimeAdapter.submit(task) -> RuntimeResult` 唯一接口，仅做 Skill 化扩展。

### 5.1 调用链

```
Orchestrator → RuntimeAdapter.submit(task)
  → 任务校验（validate_task，新增 skill 声明字段）
  → preflight（环境/工具策略检查）
  → 工具硬门控（见 5.3，需扩展）
  → Gateway WebSocket/RPC 调用 Agent（session_id = job_id；凭据经子进程环境变量注入）
  → Agent 读取 workspace 内 crawl4more-skill/SKILL.md
  → Agent 执行 Skill 入口脚本（run.py），等待结束
  → 解析 Skill 输出 JSON → validate_agent_result → RuntimeResult
```

### 5.2 相对 V3 的改造点（已逐项确认）

| 位置 | 改造 |
|---|---|
| OpenClaw Agent 配置 | crawl4more-skill 目录置于 Agent workspace 内（满足 `tools.fs.workspaceOnly=true`）；Agent `tools.allow` 增加执行 Skill 入口所需的最小工具（如受控的 `exec`/进程工具），`DANGEROUS_TOOLS` 归类随之重估 |
| `runtime_adapter.py` | `PRACTICAL_RESEARCH_TOOLS` 常量参数化；`tool_policy_allows_exact()` 基准集合更新为新白名单；`_build_research_prompt()` 加入 Skill 位置与触发指引；`_session_tool_names()` 审计解析确认能识别 Skill 子进程调用 |
| `models.py` | `validate_task()` 新增 `constraints.skill`（如 `"crawl4more"`）字段与校验；`output_schema` 扩展为 Skill 输出契约 |
| 测试 | 新增 `skill_wiring_test`（类比 V3 `tool_wiring_test`）：强制 Agent 通过 Skill 完成任务，并以 Session 审计 + 数据库状态双重证实；更新 `prompt_cannot_expand_allowed_tools` 安全用例 |
| 安全约束 | 保持"预配置 Agent + Adapter 校验一致性"路线，**不支持任务级动态声明新工具**——这是 V3 安全哲学，V4 不变 |

### 5.3 Session 与任务对齐

`session_id` 直接使用 `job_id`：同一 Job 的多次续跑（时间片轮换后恢复）复用同一 Session，保证上下文连续；不同 Job 天然隔离。

---

## 6. 核心组件三：状态可视化（解决 V3"不可看"）

### 6.1 数据库表（事实源，与《调研与方案》一致）

| 表 | 用途 | 来源 |
|---|---|---|
| `crawl_jobs` 作业表 | 一次完整爬取请求：状态、进度、参数、锁 | V2 已有，前端轮询主表 |
| `crawl_subtasks` 子任务表 | 每个 URL 的处理单元：排队/运行/完成/失败 | V2 已有 |
| `crawl_task_logs` 任务日志表 | 审计追踪：状态变更流水 | V2 已有，前端"流式操作反馈"的数据来源 |

> V4 不需新建表；前端阶段二"将 Agent 每个关键步骤实时展现"= 轮询日志表 + 子任务表。

### 6.2 可视化机制

- **轮询**：前端以固定时间片（默认 5s，可配）调用 `GET /api/jobs/{id}/status`，返回 Job + SubTask 列表 + 最近 N 条日志。
- **手动刷新**：任务监控面板提供刷新按钮，立即发起一次查询（《调研与方案》明确要求）。
- **流式反馈文案**：日志表的 `action`/`message` 直接映射为"正在抓取第 3 页…""发现 5 个新链接…"等用户可读提示。
- **异常处理**：子任务 failed 时前端展示错误原因，并提供"重试 / 跳过"操作（写库改状态即生效）。

### 6.3 人工干预（人在回路）

| 干预动作 | 实现 |
|---|---|
| 暂停 / 恢复 / 取消任务 | 改 `crawl_jobs.status`，Scheduler 下一轮取任务时生效 |
| 修改参数（如 max_pages） | 改 `crawl_jobs` 参数字段，Worker 循环每轮重读 |
| 重试 / 跳过单个子任务 | 改 `crawl_subtasks.status` / `retry_count` |
| 敏感操作前确认 | Orchestrator 在提交前返回参数确认页（阶段一流程内） |

---

## 7. Orchestrator 后端服务（FastAPI）

V3 demo 中 `runtime_demo.py` 是验证器替身，V4 需要正式的轻量 Orchestrator：

| 模块 | 职责 |
|---|---|
| `POST /api/tasks/parse` | 自然语言 → 任务参数草案（调 Agent 提取，如"不少于5篇科技情报"→ max_pages=5, keyword=科技情报） |
| `POST /api/jobs` | 参数确认后创建 Job（预写库）→ 调 Adapter.submit 启动 |
| `GET /api/jobs` / `GET /api/jobs/{id}/status` | 前端轮询接口 |
| `POST /api/jobs/{id}/control` | 暂停/恢复/取消/改参（写库） |
| `GET /api/results` | 结果查询：按创建时间、分类、URL、站点筛选与排序 |
| `GET /api/results/export` | 导出（CSV/JSON/报告） |
| `GET /api/system/status` | 数据库连接状态、OpenClaw preflight 状态 |

---

## 8. 前端方案（依据《调研与方案》）

### 8.1 三阶段交互流程

**阶段一：意图表达与任务配置**（两个标签页）

- *对话标签页*：对话框 + 任务 URL 列表（对话框上方）。用户自然语言输入 → 后端提取参数 → **参数确认卡片**（临时出现在对话框下方，用户修改/校对/确认后才启动）。
- *自定义任务标签页*：流程图设计画布，提供"单次爬取、队列调度器、数据保存"等操作块，按规则组合 + 提交前审核校验。

**阶段二：执行过程与状态可视化**

- *队列监控*（临时出现在对话框下方）：各子任务四态展示（排队/运行/暂停/完成）+ 进度条 + 骨架屏加载态。
- *任务控制*（队列监控下方）：暂停/恢复/取消/改参，写库生效。
- 异常友好处理：超时/验证码等明确提示 + 重试/跳过路径，任务不卡死。

**阶段三：结果交付与可干预性**

- *结果展示*：表格/卡片呈现抽取结果（标题、日期、来源、正文摘要），支持按创建时间/标题/来源/URL 筛选排序，支持勾选操作与导出。
- *分类分析*（新标签页）：用户自定义分类，可将不同分类文章发送给 Agent 分析。
- *插入情报*：页面底端提供手动插入文章的入口。

### 8.2 页面结构（对应调研文档功能列表）

| 标签页 | 组件 | 调研文档依据 |
|---|---|---|
| 对话 / 自定义任务 | 对话框、任务列表、参数确认、队列监控、任务控制、结果展示、新手指导弹窗 | 任务相关交互表 1–8 |
| 情报展示 | 展示列表、条件筛选（左侧）、分类分析、插入情报 | 情报展示交互表 1–4 |
| 系统设置 | 预设 URL、Agent 配置（API）、数据库状态、参考提示词 | 系统设置表 1–4 |

### 8.3 技术选型

Vue 3 + Element Plus + Pinia + Vue Flow（流程编排画布）；轮询用 `setInterval` + 可配置时间片；构建 Vite。与后端 REST 对接，不引入 WebSocket（V4 轮询即可满足，留作后续优化项）。

---

## 9. 端到端时序（一次对话式任务）

1. 用户在对话标签页输入"查找不少于 5 篇关于科技情报的文章"并填入目标 URL。
2. 前端 `POST /api/tasks/parse` → Orchestrator 调 Agent 提取参数 → 返回参数确认卡片。
3. 用户确认 → `POST /api/jobs` → Orchestrator 写 Job 表 → `RuntimeAdapter.submit(task)`。
4. OpenClaw Agent 读取 SKILL.md → 执行 `run.py` → Scheduler/Worker 开始时间片调度爬取，持续写库。
5. 前端进入阶段二：每 5s 轮询状态，展示队列监控与流式日志；用户可随时暂停/改参。
6. Job 完成 → Skill 输出汇总 JSON → Adapter 归一化返回 → Orchestrator 更新 Job 终态。
7. 前端进入阶段三：结果列表展示，用户筛选/导出/送分类分析。

---

## 10. 非功能设计

- **安全**：工具硬门控（集合相等校验）；网络访问必须 `allow_network: true` 显式声明；凭据只经子进程环境变量传递；所有对外输出递归脱敏（token/password/api_key/cookie）。
- **错误码**：沿用 V3 稳定错误码（`invalid_task / tool_policy_unenforced / timeout / agent_execution_failed / invalid_agent_result …`），新增 `skill_execution_failed`。
- **重试**：子任务级重试 2 次（间隔 3s）已有；Job 级失败由 Orchestrator 记录并允许前端手动重试。
- **性能**：V4 保持 Scheduler 串行（Semaphore=1）+ 时间片轮换；并发爬取留作后续版本。
- **可移植**：SQLite 起步；表结构兼容后续迁移 PostgreSQL。

---

## 11. 实施计划（建议）

| 里程碑 | 内容 | 预估 |
|---|---|---|
| M1 Skill 化 | 按 4.1/4.2 整理目录，完成 4.3 全部 8 项改造，`run.py` 本地跑通 | 1–2 天 ✅ **已完成**（mock 模式端到端验证通过，真实模式待装依赖后复测） |
| M2 OpenClaw 接入 | Agent 配置 + Adapter 改造 + `skill_wiring_test` 通过 | 2–3 天 |
| M3 Orchestrator | FastAPI 7 个接口 + 参数确认流 | 2–3 天 |
| M4 前端 | 对话标签页 + 队列监控 + 结果展示（先砍流程编排画布） | 3–5 天 |
| M5 联调验收 | 端到端时序跑通 + 异常/干预路径验证 | 1–2 天 |

> 建议 MVP 裁剪：阶段一的"流程图编排画布"和阶段三的"分类分析"后置，先交付对话式全链路。

---

## 12. 评审决策（2026-07-17 已确认）

**运行环境实测**：OpenClaw **2026.7.1**（`/opt/homebrew/bin/openclaw`）；Gateway **已在运行**（LaunchAgent，pid 81698，端口 18789，configAudit 无异常）；Gateway URL 与 Token 由用户在本地 `.env` 自行配置；LLM 使用 **Kimi**；演示目标站点沿用 V2 的三个站点（sesc.org.cn、pubs.cstam.org.cn/lxjz、cars.org.cn）；前端为中文界面。

1. **Skill 调用形态** ✅ Agent 通过受控 `exec` 工具执行 Skill 入口脚本（`run.py`）。Agent 硬策略中 `exec` 需收敛到仅允许该入口（后续评估是否升级为 OpenClaw 原生 Tool）。
2. **Agent workspace 约定** ✅ V3 researcher workspace 的约定文件（AGENTS.md / SOUL.md / BOOTSTRAP.md / IDENTITY.md / USER.md / TOOLS.md / HEARTBEAT.md）**全套保留**进 V4 Agent workspace，crawl4more-skill 目录置于其内；TOOLS.md 记录 Skill 本地配置细节。
3. **前端范围** ✅ 流程图编排画布与新手指导弹窗**后置**；本期交付对话式任务全链路 + 情报展示 + 系统设置。
4. **部署形态** ✅ 采用 **Gateway 模式**：Runtime Adapter 的 transport 从 CLI 子进程切换为 Gateway WebSocket/RPC 长连接（V3 方案的阶段 C 路径），`submit()`/`RuntimeResult` 契约保持不变；凭据经 `OPENCLAW_GATEWAY_URL` / `OPENCLAW_GATEWAY_TOKEN` 环境变量注入。
5. **数据库** ✅ 演示期使用 SQLite，表结构保持兼容后续迁移 PostgreSQL。

---

## 附录 A：参考资料

- V2 源码：`ref/crawl4more/`（任务调度器 Demo）
- V3 源码与接入方案：`ref/openclaw-runtime-demo/`（含《OpenClaw_Runtime实用级接入方案.md》）
- 版本文档：`ref/docs/1系统架构.md`、`2实践.md`、`3openclaw-runtime.md`、`4Intelligence-Orchestrator.md`
- 前端依据：`ref/调研与方案.docx`（AI-OSTIAS 的交互需求以及同类型系统的调研）

## 附录 B：Skill 配置项（环境变量键名）

`AI_API_KEY`、`AI_BASE_URL`、`AI_MODEL_NAME`、`CRAWLER_HEADLESS`、`CRAWLER_VIEWPORT_WIDTH`、`CRAWLER_VIEWPORT_HEIGHT`、`CRAWLER_TEXT_MODE`、`CRAWLER_VERBOSE`、`USE_MOCK_CRAWLER`、`OPENCLAW_GATEWAY_URL`、`OPENCLAW_GATEWAY_TOKEN`、（新增）`CRAWLER_DB_PATH`
