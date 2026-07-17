# config.py - Orchestrator 全局配置
"""路径与环境配置。所有路径相对 v4 根目录解析，可用环境变量覆盖。"""
import os
from pathlib import Path

ORCH_ROOT = Path(__file__).resolve().parent
V4_ROOT = ORCH_ROOT.parent

# 既有资产（M1/M2），只引用不复制
ADAPTER_DIR = V4_ROOT / "runtime-adapter"
SKILL_DIR = V4_ROOT / "crawl4more-skill"
SCHEMA_SQL = SKILL_DIR / "db" / "schema.sql"

# SQLite 状态事实源：Skill 与 Orchestrator 读写同一个库
DB_PATH = os.environ.get("CRAWLER_DB_PATH", str(V4_ROOT / "data" / "crawler.db"))

# OpenClaw Agent（M2 已配置）
AGENT_ID = os.environ.get("OPENCLAW_AGENT_ID", "researcher-v4")

# system/status 的 preflight 缓存秒数（避免每次打 CLI）
PREFLIGHT_CACHE_TTL = int(os.environ.get("ORCH_PREFLIGHT_TTL", "60"))

# parse 接口调 Agent 的超时（秒）
PARSE_TIMEOUT = int(os.environ.get("ORCH_PARSE_TIMEOUT", "120"))

VERSION = "v4-m3"
