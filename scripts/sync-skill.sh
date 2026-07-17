#!/usr/bin/env bash
# sync-skill.sh — 把权威 crawl4more Skill 同步到 OpenClaw Agent workspace 部署副本。
#
# 背景：agent-workspace/crawl4more-skill 是 v4/crawl4more-skill 的部署副本
# （M2 实证符号链接会被 OpenClaw 拒绝，见 runtime-adapter/README.md「Skill 放置」）。
# 权威 Skill 有任何改动后必须运行本脚本使两边一致：
#
#   v4/scripts/sync-skill.sh
#
# 排除：.venv / .env / __pycache__ / *.db / *.pyc（副本侧已有内容受保护，不被删除）。
set -euo pipefail

V4_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$V4_ROOT/crawl4more-skill/"
DST="$V4_ROOT/agent-workspace/crawl4more-skill/"

if [ ! -d "$SRC" ]; then
  echo "[sync-skill] 权威 Skill 目录不存在: $SRC" >&2
  exit 1
fi
mkdir -p "$DST"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.venv' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '*.db' \
    --exclude '*.pyc' \
    "$SRC" "$DST"
else
  echo "[sync-skill] 未找到 rsync，退化为 cp -R 同步" >&2
  # 清空目标中除 .venv/.env 外的内容，再整体拷贝并清理缓存/库文件
  find "$DST" -mindepth 1 -maxdepth 1 ! -name '.venv' ! -name '.env' -exec rm -rf {} +
  (
    cd "$SRC"
    find . -mindepth 1 -maxdepth 1 ! -name '.venv' ! -name '.env' ! -name '*.db' \
      -exec cp -R {} "$DST" \;
  )
  find "$DST" -type d -name '__pycache__' -prune -exec rm -rf {} +
  find "$DST" -type f -name '*.pyc' -delete
  find "$DST" -type f -name '*.db' -delete
fi

echo "[sync-skill] 已同步: $SRC -> $DST"
