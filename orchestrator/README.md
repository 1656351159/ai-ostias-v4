# AI-OSTIAS V4 Orchestrator（M3）

FastAPI 后端服务，连接前端、OpenClaw Runtime 与 crawl4more Skill（技术手册第 7 章）。
中文错误提示；SQLite 为唯一状态事实源。

## 运行

```bash
cd v4/orchestrator
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
CRAWLER_DB_PATH=$(pwd)/../data/crawler.db .venv/bin/python main.py --port 8000
# 或 .venv/bin/uvicorn main:app --port 8000
```

环境变量：`CRAWLER_DB_PATH`（默认 `<v4>/data/crawler.db`，须与 Skill 同库）、
`OPENCLAW_AGENT_ID`（默认 `researcher-v4`）、`ORCH_PARSE_TIMEOUT`（默认 120s）、
`ORCH_PREFLIGHT_TTL`（默认 60s）。

## 接口

| 接口 | 说明 | 落点 |
|---|---|---|
| `POST /api/tasks/parse` | 自然语言→参数草案（Agent 提取，失败降级规则提取，带 `source` 字段） | `routers/tasks.py` + `services/parse_service.py` |
| `POST /api/jobs` | 确认参数后创建 Job：生成 job_id(uuid)→组装契约→**异步** Adapter.submit，立即返回 `{"job_id","status":"submitted"}` | `routers/jobs.py` |
| `GET /api/jobs` | Job 列表（DB，created_at 倒序，附 adapter_state） | 同上 |
| `GET /api/jobs/{id}/status` | 轮询主接口：Job + SubTask + 最近 50 条日志 + Adapter 内存状态 | 同上 |
| `POST /api/jobs/{id}/control` | `pause/resume/cancel/update`（写库即生效） | 同上 + `services/db_service.py` |
| `GET /api/results` | 结果查询：时间区间/url 关键词/站点/task_type 筛选，created_at/url 排序，page/size 分页；extracted_data 平铺 title/pub_date/site_name | `routers/results.py` |
| `GET /api/results/export?format=csv\|json` | 文件下载（Content-Disposition） | 同上 |
| `GET /api/system/status` | `{"db", "openclaw"(preflight 摘要，60s 缓存), "version": "v4-m3"}` | `routers/system.py` |

## 关键设计决策

1. **异步方式**：`Adapter.submit` 是同步阻塞调用（30–60s 起），经
   `asyncio.create_task` + `asyncio.to_thread` 跑在线程池；接口立即返回。
   运行状态记录在内存任务表（`submitted/running/done/failed` + RuntimeResult
   摘要），DB 轮询为佐证。
2. **不预写 Job 行**（M1 对齐方案）：job_id 由 Orchestrator 生成放进任务契约，
   Agent 执行 run.py 时由 Skill 自己建行；DB 行出现前 /status 以内存状态作答。
3. **DB 连接策略**：沿用 TaskManager 模式——每次操作新建连接、用完即关，
   无跨线程共享连接，天然规避 `check_same_thread` 问题。Skill 代码只加载
   不复制（`services/adapter_service.py` 用 importlib 按文件位置装配，
   解决了 runtime-adapter `models.py` 模块与 Skill `models/` 包的顶层名冲突）。
4. **parse 降级逻辑**：先调 Agent（prompt 要求只回单行 JSON，宽容解析围栏/
   首尾文本）；任何失败 → 规则提取（URL 正则 + "不少于N篇/N页" 数字提取 +
   其余文本作关键词）。草案不做默认值兜底，提取不到为 null，`source` 标明
   `agent`/`fallback`，降级时附 `agent_error`。
5. **人工暂停要加锁**：Skill 调度器 `get_next_runnable_job` 会捞取
   `paused` 状态的 Job（时间片轮换语义），只改 status 会被自动复活。因此
   `pause` 同时写 `locked_by='manual_pause', locked_until='9999-12-31'`，
   调度器跳过该 Job；`resume` 清锁并回 `running`（未跑过的回 `pending`）。
   `cancel`/`update` 直接写库，Worker 每轮重读生效。

## 状态值

Job：pending/running/paused/completed/failed/cancelled；
SubTask：pending/running/completed/failed。控制接口校验状态迁移合法性，
终态任务拒绝一切操作（409，中文提示）。
