# AI-OSTIAS V4 Runtime Adapter（M2）

业务层与 OpenClaw 之间的唯一接口：`RuntimeAdapter.submit(task) -> RuntimeResult`（契约沿用 V3）。
本目录是 V3 `ref/openclaw-runtime-demo/` 三件套的 V4 演进版，改造依据为
《AI-OSTIAS-V4 技术手册》第 5 章与第 12 章（评审决策）。

## 目录

```
runtime-adapter/
├── models.py                  # 任务/结果契约：constraints.skill、Skill 输出契约、skill_execution_failed
├── runtime_adapter.py         # 适配层：preflight / invoke_text / submit、工具硬门控、脱敏、Session 审计
├── runtime_demo.py            # 验证器：preflight + skill_wiring + session + error + security
├── fixtures/sample_crawl_task.json
├── tests/test_runtime_adapter.py   # 47 项单元测试
├── evidence/                  # 脱敏证据报告（preflight-check.json / m2-final-evidence.json）
└── README.md
```

Agent 工作区在 `../agent-workspace/`（约定文件全套保留自 V3，`crawl4more-skill/` 为其内部署副本）。

## Transport 决策（评审决策 4 的落地记录）

**结论：采用"经 Gateway 的 CLI RPC 模式"（`openclaw gateway call agent --expect-final`），
不自建裸 WebSocket 长连接客户端。** 这是手册允许的降级路径，证据如下：

1. **官方 WS 协议已文档化但无公开客户端库**。docs.openclaw.ai/gateway/protocol 描述了
   protocol v4 握手（connect.challenge → connect → hello-ok）与 req/res/event 帧；但
   docs.openclaw.ai/gateway/external-apps 明确写道："A future client library package is
   in progress internally, but it is **not a public install surface yet**."。
   握手还涉及 device identity（publicKey/signature）与 pairing 流程，自实现面大且脆。
2. **依赖约束**。V3 起 Adapter 仅依赖 Python 标准库；本机 managed Python 无
   `websockets`/`websocket-client`/`aiohttp`（已实测 import 均失败）。引入第三方依赖
   或用 stdlib 手写 RFC6455 + pairing 都超出 M2 范围且难以审计。
3. **官方为脚本场景提供的 Ready 表面就是 CLI**。external-apps 文档把 `openclaw agent`
   列为 "Ready — one-shot script integration"；`gateway call <method>` 是同一 CLI 的
   WS/RPC 客户端能力。我们实测 `openclaw gateway call agent` 本身就是一条 Gateway
   WebSocket/RPC 调用（不带 `--local`），满足"Gateway 模式"决策。
4. **实测证据**（2026-07-17，OpenClaw 2026.7.1）：
   - `gateway call agent --params '{"message","agentId","sessionId","idempotencyKey"}'
     --expect-final --json` 端到端成功，返回
     `{runId,status:"ok",summary,result:{payloads,meta:{agentMeta:{sessionId,sessionFile}}}}`；
   - 缺 `message` / 缺 `idempotencyKey` 时返回 `{"ok":false,"error":{code:"INVALID_REQUEST"}}`
     ——参数契约与错误封套均已探测并归一化；
   - `gateway status --json --require-rpc` 预检 `rpc.ok=true`（pid 81698，端口 18789）。
5. **额外收益**：`idempotencyKey` 为必填，天然对齐 V3 阶段 C 的幂等键要求
   （`submit:` 前缀 + task_id）。

**已知取舍**：每次 turn 新建一条 WS 连接（非长连接复用）；消息体经 `--params` 出现在
子进程参数表中（本机 `ps` 可见，不涉 shell 解释，`shell=False` 保证无注入）。生产化时
待官方客户端库发布后切换到真正的长连接 transport（代码中 `RuntimeAdapter(transport=...)`
已预留通道抽象，`agent-cli` 备用通道保留 V3 的 `--message-file` 行为）。

## Agent 配置（researcher-v4，脱敏）

```bash
openclaw agents add researcher-v4 \
  --workspace <v4>/agent-workspace \
  --model custom-api-kimi-com/kimi-for-coding-highspeed --non-interactive

openclaw config set 'agents.list[2].tools.allow' '["read","exec"]'
openclaw config set 'agents.list[2].tools.deny'  '["process","write","edit","apply_patch",
  "browser","message","cron","gateway","web_search","web_fetch","sessions_list",
  "sessions_history","sessions_send","sessions_spawn","sessions_yield","subagents",
  "image_generate","music_generate","video_generate"]'
openclaw config set 'agents.list[2].tools.fs.workspaceOnly' 'true'
openclaw config set 'agents.list[2].tools.elevated.enabled' 'false'
openclaw config set 'agents.list[2].tools.exec.mode' '"allowlist"'
openclaw approvals allowlist add --agent researcher-v4 "/usr/bin/python3"
```

最终策略（`openclaw exec-policy show` 实测）：

- `tools.allow = ["read","exec"]`，精确白名单（无通配/group 条目）
- exec：`mode=allowlist, security=allowlist, ask=off, askFallback=deny`，
  per-agent allowlist 仅 `/usr/bin/python3`
- `fs.workspaceOnly=true`，`elevated.enabled=false`，无渠道绑定
- 实测非白名单命令被硬拒绝：`exec denied: allowlist miss`（Session 审计可见）

注意：2026.7.1 的 exec allowlist 粒度是**二进制路径 glob**，不支持完整命令行匹配。
因此"收敛到仅允许 Skill 入口命令"= 仅放行 python3 解释器 + workspace 限定 +
Prompt 指引 + Adapter 审计核对（Session 中 exec 命令行必须含 `run.py`，见
`skill_wiring_test` 证据 a）。后续可按手册决策 1 评估升级为 OpenClaw 原生 Tool。

## Skill 放置（评审决策 2 的实证结论）

**workspace 内为部署副本，权威源是 `v4/crawl4more-skill/`。**

实证：符号链接 `agent-workspace/crawl4more-skill -> ../crawl4more-skill` 被 OpenClaw
拒绝（read 工具报错 `Symlink escapes sandbox root`，Session 审计留存）。
改为 `cp -R` 副本后 read/exec 全部通过。Skill 变更需重新同步副本；
`preflight` 的 `skill_presence` 检查会校验 run.py/SKILL.md 就位且 realpath 不逃逸。

## 运行

```bash
cd v4/runtime-adapter
python3 -m unittest discover -s tests -v          # 单元测试（47 项）
python3 runtime_demo.py --check-only --agent researcher-v4   # preflight（9 项）
python3 runtime_demo.py --agent researcher-v4 --timeout 900  # 完整套件
```

环境变量：`OPENCLAW_GATEWAY_URL` / `OPENCLAW_GATEWAY_TOKEN`（可选，CLI 本地配置已可认证）、
`OPENCLAW_TRANSPORT`（rpc|agent-cli，默认 rpc）、`OPENCLAW_TIMEOUT`、`CRAWL_SKILL_PYTHON`。

## 安全哲学（与 V3 一致）

- 工具硬策略在 Runtime/Agent 侧强制（集合相等校验 + exec allowlist），Prompt 只是 advisory。
- 不支持任务级动态声明新工具/新 Skill：超出硬策略的工具请求在模型调用前
  `tool_policy_unenforced` 拒绝；未注册 Skill 在契约校验 `invalid_task` 拒绝。
- 网络访问必须 `allow_network: true` 显式声明；爬取 Skill 强制要求。
- 凭据只经子进程环境变量传递；所有对外输出递归脱敏；Gateway URL 入库前去认证信息。

## BLOCKED / 待用户配置清单

当前**无 BLOCKED 项**：模型 provider（custom-api-kimi-com/kimi-for-coding-highspeed）
已由用户在 OpenClaw 中配置完成，全部真实集成测试通过。

若迁移到他机复现，需要：

1. `openclaw agents add` 创建 Agent 并配置模型 provider 凭据（本适配层只看键不看值）。
2. 真实爬取模式：`pip install -r crawl4more-skill/requirements.txt && crawl4ai-setup`，
   并在 Skill 的 `.env` 填 `AI_API_KEY`/`AI_BASE_URL`（M2 只用 mock 模式验证链路）。
3. Gateway Token 如需显式注入：在本地 `.env` 配 `OPENCLAW_GATEWAY_URL`/`OPENCLAW_GATEWAY_TOKEN`。
