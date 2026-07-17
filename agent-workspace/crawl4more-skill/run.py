#!/usr/bin/env python3
# run.py - crawl4more Skill 统一入口
"""
crawl4more：单机时间片轮换多任务爬虫调度器。

两种模式：
  1. 默认模式：创建 Job 并运行调度器，直到作业完成 / 自停 / 整体超时。
  2. --enqueue-only：只创建 Job 和种子/追加子任务后立即退出（由其他进程调度执行）。

输出契约：进程向 stdout 打印的【最后一行】必须是单行 JSON 摘要：
  {"job_id","status","processed_pages","extracted_count","failed_subtasks","error"}
中途日志一律打到 stderr（或前面的 stdout 行），供上层 Adapter 解析最后一行。
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Skill 根目录加入 sys.path（run.py 可能被上层以任意 cwd 调用）
SKILL_ROOT = Path(__file__).resolve().parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

# ========== 显式加载 .env（改造项 1：原代码从不加载 .env） ==========
try:
    from dotenv import load_dotenv
    load_dotenv(SKILL_ROOT / ".env")
except ImportError:
    print("[run.py] 警告: 未安装 python-dotenv，跳过 .env 加载", file=sys.stderr)

from db.init_db import init_database
from services.task_manager import TaskManager, get_default_db_path
from services.scheduler import TimeSliceScheduler

logger = logging.getLogger("run")


def setup_logging():
    """日志全部走 stderr，stdout 留给最终 JSON 摘要"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def resolve_db_path(cli_db_path: str = None) -> str:
    """数据库路径优先级：--db-path > CRAWLER_DB_PATH 环境变量 > <skill>/../data/crawler.db"""
    if cli_db_path:
        return cli_db_path
    return get_default_db_path()


def build_summary(tm: TaskManager, job, error: str = None) -> dict:
    """从数据库统计归一化摘要"""
    extracted_count = 0
    failed_subtasks = 0
    if job is not None:
        with tm._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT COUNT(*) AS cnt FROM crawl_subtasks "
                "WHERE job_id = ? AND status = 'completed' AND extracted_data IS NOT NULL",
                (job.id,),
            ).fetchone()
            extracted_count = row["cnt"] if row else 0
            row = cursor.execute(
                "SELECT COUNT(*) AS cnt FROM crawl_subtasks "
                "WHERE job_id = ? AND status = 'failed'",
                (job.id,),
            ).fetchone()
            failed_subtasks = row["cnt"] if row else 0

    return {
        "job_id": job.job_uuid if job is not None else None,
        "status": job.status if job is not None else "failed",
        "processed_pages": job.processed_pages if job is not None else 0,
        "extracted_count": extracted_count,
        "failed_subtasks": failed_subtasks,
        "error": error if error is not None else (job.error_message if job is not None else "job not found"),
    }


def print_summary(summary: dict):
    """最后一行 stdout 必须是单行 JSON"""
    print(json.dumps(summary, ensure_ascii=False))


async def monitor_progress(tm: TaskManager, scheduler: TimeSliceScheduler,
                           job_db_id: int, check_interval: int = 10,
                           overall_timeout: int = 600) -> str:
    """进度监控协程：每 10s 打印一次进度（stderr）；整体超时则强停调度器。

    返回 None 表示正常结束（所有任务完成），返回错误字符串表示超时强停。
    """
    loop = asyncio.get_event_loop()
    start_time = loop.time()

    while True:
        await asyncio.sleep(check_interval)

        jobs = tm.get_all_jobs_status()
        active = [j for j in jobs if j["status"] in ("pending", "running", "paused")]
        completed = [j for j in jobs if j["status"] == "completed"]
        failed = [j for j in jobs if j["status"] == "failed"]

        target = next((j for j in jobs if j["id"] == job_db_id), None)
        target_desc = (
            f"目标 Job 状态={target['status']} 已处理={target['processed_pages']}"
            if target else "目标 Job 不存在"
        )
        print(
            f"[monitor] 进度: 总任务={len(jobs)} 完成={len(completed)} "
            f"失败={len(failed)} 进行中={len(active)} | {target_desc}",
            file=sys.stderr,
        )

        if not active:
            print("[monitor] 所有任务已结束", file=sys.stderr)
            return None

        elapsed = loop.time() - start_time
        if elapsed > overall_timeout:
            print(f"[monitor] 整体超时 ({overall_timeout}s)，强制停止调度器", file=sys.stderr)
            await scheduler.stop()
            return f"overall timeout ({overall_timeout}s) reached, scheduler force-stopped"


async def run_with_scheduler(tm: TaskManager, db_path: str, job_db_id: int,
                             overall_timeout: int) -> str:
    """创建 Job 后运行调度器直到完成 / 自停 / 超时。返回错误信息或 None。"""
    scheduler = TimeSliceScheduler(db_path, auto_stop=True)
    try:
        results = await asyncio.gather(
            scheduler.start(),
            monitor_progress(tm, scheduler, job_db_id,
                             check_interval=10, overall_timeout=overall_timeout),
        )
        return results[1]  # monitor 返回的错误信息（None 表示正常）
    except KeyboardInterrupt:
        print("\n[run.py] 收到停止信号，正在关闭调度器...", file=sys.stderr)
        await scheduler.stop()
        return "interrupted by user (KeyboardInterrupt)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="crawl4more Skill：时间片轮换多任务爬虫调度器",
    )
    parser.add_argument("--start-url", required=True, help="起始 URL（种子子任务）")
    parser.add_argument("--extra-urls", nargs="*", default=[],
                        help="追加到队列的额外 URL（可多个）")
    parser.add_argument("--max-pages", type=int, default=15, help="最大处理页数（默认 15）")
    parser.add_argument("--priority", type=int, default=0, help="优先级，数字越大越优先（默认 0）")
    parser.add_argument("--slice-timeout", type=int, default=30, help="时间片长度秒数（默认 30）")
    parser.add_argument("--db-path", default=None,
                        help="SQLite 数据库路径（默认：CRAWLER_DB_PATH 或 <skill>/../data/crawler.db）")
    parser.add_argument("--job-id", default=None,
                        help="指定 job_uuid（不传则自动生成 uuid）")
    parser.add_argument("--enqueue-only", action="store_true",
                        help="只创建 Job 和子任务后退出，不运行调度器")
    parser.add_argument("--overall-timeout", type=int, default=600,
                        help="整体超时秒数，超时强停调度器（默认 600）")
    args = parser.parse_args()

    setup_logging()

    db_path = resolve_db_path(args.db_path)
    logger.info("数据库路径: %s", db_path)

    summary = None
    exit_code = 0
    try:
        # 1. 建库（幂等）
        init_database(db_path)

        # 2. 创建 Job + 种子子任务（复用 TaskManager.create_job）
        tm = TaskManager(db_path)
        job = tm.create_job(
            start_url=args.start_url,
            max_pages=args.max_pages,
            concurrency=1,
            priority=args.priority,
            slice_timeout=args.slice_timeout,
            job_uuid=args.job_id,
        )
        logger.info("Job 创建成功: uuid=%s id=%s", job.job_uuid, job.id)

        # 3. 追加 URL 入队
        if args.extra_urls:
            added = tm.add_subtasks(job.id, args.extra_urls, "nav")
            logger.info("追加入队 %s 个 URL", added)

        if args.enqueue_only:
            # 仅入队模式：不启动调度器，直接输出摘要
            job = tm.get_job(job.id)
            summary = build_summary(tm, job)
            print_summary(summary)
            return 0

        # 4. 默认模式：运行调度器直到完成 / 自停 / 超时
        error = asyncio.run(run_with_scheduler(tm, db_path, job.id, args.overall_timeout))

        # 5. 汇总输出
        job = tm.get_job(job.id)
        summary = build_summary(tm, job, error=error)

    except Exception as e:
        logger.exception("run.py 执行失败")
        summary = {
            "job_id": args.job_id,
            "status": "failed",
            "processed_pages": 0,
            "extracted_count": 0,
            "failed_subtasks": 0,
            "error": str(e),
        }
        exit_code = 1

    print_summary(summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
