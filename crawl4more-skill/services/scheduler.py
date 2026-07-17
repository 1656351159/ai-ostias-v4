# services/scheduler.py
import asyncio
import logging
from datetime import datetime
from typing import Optional

from models.job import CrawlJob
from services.task_manager import TaskManager
from crawler.worker import CrawlerWorker

logger = logging.getLogger(__name__)


class TimeSliceScheduler:
    """时间片轮换调度器"""

    def __init__(self, db_path: str = None, auto_stop: bool = True):
        self.task_manager = TaskManager(db_path)
        self.worker_id = f"scheduler_{datetime.now().strftime('%H%M%S')}"
        self.running = False
        self.current_job: Optional[CrawlJob] = None
        self.check_interval = 10
        # ✅ 信号量限制并发
        self.semaphore = asyncio.Semaphore(1)
        self.max_concurrent_workers = 1
        self.active_workers = set()

        # ✅ 自动停止配置
        self.auto_stop = auto_stop
        self.idle_count = 0
        self.max_idle_checks = 3  # 连续 3 次（15秒）没有任务则停止

    async def start(self):
        """启动调度器"""
        self.running = True
        logger.info(f"[{self.worker_id}] 调度器启动 (自动停止: {self.auto_stop})")

        while self.running:
            try:
                # 检查是否有可运行的作业
                has_runnable_job = False

                # 检查当前作业
                if self.current_job:
                    job = self.task_manager.get_job(self.current_job.id)
                    if job and job.status in ('running', 'pending'):
                        has_runnable_job = True
                    else:
                        self.current_job = None

                # 如果没有当前作业，尝试获取新作业
                if not self.current_job:
                    job = self.task_manager.get_next_runnable_job(self.worker_id)
                    if job:
                        self.current_job = job
                        has_runnable_job = True
                        logger.info(f"[{self.worker_id}] 🚀 开始执行作业: {job.job_uuid[:8]}... ({job.start_url[:50]})")
                        asyncio.create_task(self._run_job_with_semaphore(job))

                # ✅ 检查是否所有任务都已完成
                if not self.current_job and not has_runnable_job:
                    pending_jobs = self.task_manager.get_all_jobs_status()
                    active_jobs = [j for j in pending_jobs if j['status'] in ('pending', 'running', 'paused')]

                    if not active_jobs:
                        self.idle_count += 1
                        logger.info(
                            f"[{self.worker_id}] 没有可运行的作业 (空闲检查 {self.idle_count}/{self.max_idle_checks})")

                        if self.auto_stop and self.idle_count >= self.max_idle_checks:
                            logger.info(f"[{self.worker_id}] ✅ 所有任务已完成，自动停止调度器")
                            await self.stop()
                            break
                    else:
                        self.idle_count = 0

                # 检查时间片
                if self.current_job:
                    if self.task_manager.is_time_slice_expired(self.current_job.id):
                        logger.info(f"[{self.worker_id}] ⏰ 作业 {self.current_job.job_uuid[:8]}... 时间片到期，暂停")
                        self.task_manager.update_job_status(self.current_job.id, 'paused')
                        self.current_job = None
                        self.idle_count = 0
                        await asyncio.sleep(1)

                await asyncio.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"[{self.worker_id}] 调度器错误: {e}")
                await asyncio.sleep(self.check_interval)

    async def _run_job_with_semaphore(self, job: CrawlJob):
        """使用信号量控制并发"""
        async with self.semaphore:
            self.active_workers.add(job.id)
            logger.debug(f"[{self.worker_id}] ✅ 获取信号量，开始执行: {job.job_uuid[:8]}...")
            try:
                await self._run_job(job)
            finally:
                self.active_workers.discard(job.id)

    async def _run_job(self, job: CrawlJob):
        """运行单个作业"""
        try:
            worker = CrawlerWorker(self.task_manager, job)
            await worker.run()
        except Exception as e:
            logger.error(f"[{self.worker_id}] ❌ 作业 {job.job_uuid[:8]}... 运行失败: {e}")
            self.task_manager.update_job_status(job.id, 'failed', str(e))
        finally:
            if self.current_job and self.current_job.id == job.id:
                final_job = self.task_manager.get_job(job.id)
                if final_job and final_job.status not in ('completed', 'failed', 'cancelled'):
                    pending = self.task_manager.get_pending_subtask_count(job.id)
                    if pending == 0:
                        self.task_manager.update_job_status(job.id, 'completed')
                        logger.info(f"[{self.worker_id}] 作业 {job.job_uuid[:8]}... 所有子任务完成")
                    else:
                        self.task_manager.update_job_status(job.id, 'paused')
                        logger.info(f"[{self.worker_id}] 作业 {job.job_uuid[:8]}... 暂停，剩余 {pending} 个子任务")
                self.current_job = None

    async def stop(self):
        """停止调度器"""
        self.running = False
        logger.info(f"[{self.worker_id}] 调度器停止")