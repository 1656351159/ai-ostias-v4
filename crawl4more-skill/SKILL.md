# crawl4more

单机 Python 时间片轮换多任务爬虫调度器（crawl4ai + LangChain + SQLite），面向网页情报采集场景。由 OpenClaw Agent 通过受控 `exec` 调用本目录下的 `run.py` 入口脚本完成任务。

## 触发场景

当任务类型属于以下任一情况时使用本技能：

- **网页情报采集**：从指定站点抓取文章正文（标题、日期、来源、正文），由 LLM 做结构化抽取
- **多 URL 队列爬取**：给定一批 URL，排队调度、自动发现新链接回填队列
- **站点监控**：对目标站点做周期性/限量爬取（`--max-pages` 控制规模）

## 调用方式

```bash
# 标准模式：创建 Job 并运行调度器直到完成 / 自停 / 超时
python run.py --start-url https://example.com --max-pages 15

# 批量 URL + 指定数据库 + 自定义超时
python run.py --start-url https://example.com \
  --extra-urls https://example.com/news https://example.com/blog \
  --max-pages 20 --priority 1 --slice-timeout 45 \
  --db-path /path/to/crawler.db --overall-timeout 900

# 仅入队模式：只建 Job 和子任务后退出（由其他进程另行调度）
python run.py --start-url https://example.com --enqueue-only

# mock 模式（无网络/无浏览器/无 API key 的联调验证）
USE_MOCK_CRAWLER=true python run.py --start-url https://example.com --max-pages 3
```

## 输入参数

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--start-url` | 是 | — | 起始 URL，作为种子子任务入队 |
| `--extra-urls` | 否 | `[]` | 追加到队列的额外 URL，可多个 |
| `--max-pages` | 否 | `15` | 本 Job 最大处理页数，达到即标记 completed |
| `--priority` | 否 | `0` | 优先级，数字越大越先被调度 |
| `--slice-timeout` | 否 | `30` | 时间片长度（秒），到期挂起轮换 |
| `--db-path` | 否 | `$CRAWLER_DB_PATH` 或 `<skill>/../data/crawler.db` | SQLite 数据库路径 |
| `--job-id` | 否 | 自动生成 uuid | 指定 job_uuid（Orchestrator 预写库时对齐用） |
| `--enqueue-only` | 否 | 关闭 | 只创建 Job 和子任务后退出，不运行调度器 |
| `--overall-timeout` | 否 | `600` | 整体超时秒数，超时强停调度器 |
| `--extraction-instruction` | 否 | 内置默认指令 | 覆盖 LLM 抽取指令（默认取 `models/extraction_schema.py` 的 `get_default_extraction_instruction()`；仅真实爬取模式生效，mock 模式下仅参数通路可用） |

运行中每 10 秒向 stderr 打印一次进度；环境变量从 `.env` 加载（见 `.env.example`）。

## 输出契约

**stdout 最后一行**为单行 JSON 摘要，供上层 Adapter 解析：

```json
{"job_id": "uuid", "status": "completed|failed|paused|cancelled", "processed_pages": 12, "extracted_count": 8, "failed_subtasks": 1, "error": null}
```

- `status`：`cancelled` 表示 Job 被用户在 DB 侧取消（语义为"已取消"而非"失败"，Adapter 会区分上报）

- `processed_pages`：Job 已处理页数（取自 `crawl_jobs.processed_pages`）
- `extracted_count`：有 `extracted_data` 的已完成子任务数（从 DB 统计）
- `failed_subtasks`：失败子任务数
- `error`：整体错误信息（如超时强停、Job 级失败原因）；正常完成为 `null`

中途日志打到 stderr（或前面的 stdout 行），**只有最后一行是 JSON**。

同时写入 SQLite 三张表（状态事实源，前端/上层可轮询）：

- `crawl_jobs`：作业表（状态、进度、参数、锁）
- `crawl_subtasks`：子任务表（每个 URL 的处理单元，含 `extracted_data` JSON）
- `crawl_task_logs`：任务日志表（状态变更审计流水）

## 副作用声明

- **网络请求**：真实模式抓取目标站点（需 `allow_network: true`）
- **启动无头 Chromium**：真实模式由 crawl4ai 启动浏览器进程
- **写数据库**：向指定 SQLite 库写入 jobs / subtasks / logs 三表（默认路径在 Skill 目录外的 `../data/`）
- **LLM 调用**：真实模式每页调用一次 LLM 做结构化抽取（消耗 API 额度）

## 前置条件

1. 复制 `.env.example` 为 `.env` 并填入 `AI_API_KEY` / `AI_BASE_URL`（真实模式必填；mock 模式可空）
2. `pip install -r requirements.txt`
3. 浏览器安装：`crawl4ai-setup`（或 `playwright install chromium`）
4. mock 模式（`USE_MOCK_CRAWLER=true`）无以上 2、3 步硬性要求，仅需 Python 3.10+ 标准库即可跑通

## 失败排查要点

| 现象 | 排查方向 |
|---|---|
| 启动即报 `crawl4ai` ImportError | 未装依赖或未装浏览器：执行 `pip install -r requirements.txt && crawl4ai-setup`；或先用 `USE_MOCK_CRAWLER=true` 验证链路 |
| 日志显示 "LLM 初始化失败" 后进入模拟模式 | `.env` 未加载或 `AI_API_KEY` / `AI_BASE_URL` 缺失/错误；run.py 会显式 `load_dotenv()`，确认 `.env` 在 Skill 根目录 |
| 浏览器启动超时（60s） | 机器无 GPU 属正常（已软渲染）；确认 Chromium 已安装、`CRAWLER_HEADLESS=true` |
| 子任务反复 failed | 看 `crawl_subtasks.error_message`；常见为页面超时/JSON 解析失败/LLM 返回空，已内置 2 次重试（间隔 3s） |
| 数据库锁或找不到表 | 确认 `--db-path` 指向可写目录；`run.py` 启动时会自动建目录、幂等建表 |
| 进程不退出 | 调度器连续约 30s 无任务自动停止；另有 `--overall-timeout` 兜底强停（默认 600s） |
| 最后一行不是 JSON | 正常/异常路径都会输出 JSON；若进程被 kill -9 则无输出，按 `skill_execution_failed` 处理 |
