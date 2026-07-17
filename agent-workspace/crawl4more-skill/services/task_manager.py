# services/task_manager.py
import sqlite3
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from pathlib import Path

from models.job import CrawlJob
from models.subtask import CrawlSubtask

logger = logging.getLogger(__name__)


def get_default_db_path() -> str:
    """解析默认数据库路径。

    优先级：环境变量 CRAWLER_DB_PATH > <skill目录>/../data/crawler.db。
    运行产物不写进 Skill 目录内部。
    """
    env_path = os.environ.get("CRAWLER_DB_PATH")
    if env_path:
        return env_path
    return str(Path(__file__).resolve().parent.parent.parent / "data" / "crawler.db")


# 默认数据库路径
DEFAULT_DB_PATH = get_default_db_path()


class TaskManager:
    """
    任务管理器 - 负责所有数据库操作
    包括 Job CRUD、SubTask CRUD、锁管理、日志记录
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DEFAULT_DB_PATH)
        # 确保数据库所在目录存在（运行产物目录可能尚未创建）
        Path(self.db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"TaskManager 初始化: {self.db_path}")

    @contextmanager
    def _get_connection(self):
        """获取数据库连接（上下文管理器）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ==================== Job 操作 ====================

    def create_job(self, start_url: str, max_pages: int = 15,
                   concurrency: int = 3, priority: int = 0,
                   slice_timeout: int = 30, job_uuid: str = None) -> CrawlJob:
        """创建新作业（job_uuid 可选，不传则自动生成）"""
        job_uuid = job_uuid or str(uuid.uuid4())
        now = datetime.now().isoformat()

        logger.info(f"[TaskManager] 创建作业: {start_url}")

        with self._get_connection() as conn:
            cursor = conn.cursor()

            try:
                # 1. 插入 Job
                cursor.execute("""
                    INSERT INTO crawl_jobs (
                        job_uuid, start_url, status, max_pages, concurrency,
                        priority, slice_timeout, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (job_uuid, start_url, 'pending', max_pages, concurrency,
                      priority, slice_timeout, now, now))

                job_id = cursor.lastrowid
                logger.info(f"[TaskManager] Job 创建成功: id={job_id}, uuid={job_uuid[:8]}...")

                # 2. ✅ 创建种子子任务
                subtask_id = self._create_subtask(conn, job_id, start_url, 'seed')
                logger.info(f"[TaskManager] 种子子任务创建成功: id={subtask_id}, url={start_url[:50]}")

                # 3. 记录日志
                self._add_log(conn, job_id, None, 'job_created', None, 'pending',
                              f'创建作业: {start_url}')

                conn.commit()
                logger.info(f"[TaskManager] 事务提交成功")

            except Exception as e:
                logger.error(f"[TaskManager] 创建作业失败: {e}")
                conn.rollback()
                raise

        return self.get_job(job_id)

    def get_job(self, job_id: int) -> Optional[CrawlJob]:
        """获取单个作业"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                return CrawlJob.from_row(dict(row))
        return None

    def get_job_by_uuid(self, job_uuid: str) -> Optional[CrawlJob]:
        """通过 UUID 获取作业"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT * FROM crawl_jobs WHERE job_uuid = ?", (job_uuid,)).fetchone()
            if row:
                return CrawlJob.from_row(dict(row))
        return None

    def get_next_runnable_job(self, worker_id: str) -> Optional[CrawlJob]:
        """
        获取下一个可运行的作业（时间片轮换）
        - 优先获取 pending 或 paused 的作业
        - 按优先级降序、创建时间升序排序
        - 使用行锁防止并发冲突
        """
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 先清理过期的锁
            cursor.execute("""
                UPDATE crawl_jobs 
                SET locked_by = NULL, locked_until = NULL
                WHERE locked_until IS NOT NULL AND locked_until < ?
            """, (now,))

            # 查找可运行的作业
            row = cursor.execute("""
                SELECT * FROM crawl_jobs 
                WHERE status IN ('pending', 'paused') 
                AND (locked_by IS NULL OR locked_until IS NULL OR locked_until < ?)
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            """, (now,)).fetchone()

            if row:
                job = CrawlJob.from_row(dict(row))
                # 锁定作业
                lock_until = (datetime.now() + timedelta(seconds=60)).isoformat()
                cursor.execute("""
                    UPDATE crawl_jobs 
                    SET locked_by = ?, locked_until = ?, status = 'running',
                        current_slice_start = ?, started_at = COALESCE(started_at, ?),
                        updated_at = ?
                    WHERE id = ?
                """, (worker_id, lock_until, now, now, now, job.id))

                # 记录日志
                self._add_log(conn, job.id, None, 'job_acquired', job.status, 'running',
                              f'被 {worker_id} 获取')

                conn.commit()
                return self.get_job(job.id)

        return None

    def update_job_status(self, job_id: int, status: str,
                          error_message: str = None) -> bool:
        """更新作业状态"""
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 获取旧状态用于日志
            old = cursor.execute("SELECT status FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
            old_status = old['status'] if old else None

            updates = {
                'status': status,
                'updated_at': now
            }
            if status == 'running' and (not old or old_status != 'running'):
                updates['started_at'] = now
            if status in ('completed', 'failed', 'cancelled'):
                updates['completed_at'] = now
                updates['locked_by'] = None
                updates['locked_until'] = None
            if status == 'paused':
                updates['current_slice_start'] = None
            if error_message:
                updates['error_message'] = error_message

            set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [job_id]

            cursor.execute(f"UPDATE crawl_jobs SET {set_clause} WHERE id = ?", values)
            conn.commit()

            self._add_log(conn, job_id, None, 'status_change', old_status, status,
                          error_message or f'状态变更: {old_status} -> {status}')

        return True

    def increment_processed_pages(self, job_id: int) -> int:
        """增加已处理页面计数"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE crawl_jobs 
                SET processed_pages = processed_pages + 1,
                    total_pages = total_pages + 1,
                    updated_at = ?
                WHERE id = ?
                RETURNING processed_pages
            """, (datetime.now().isoformat(), job_id))
            row = cursor.fetchone()
            conn.commit()
            return row['processed_pages'] if row else 0

    def is_time_slice_expired(self, job_id: int) -> bool:
        """检查作业的时间片是否已过期"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("""
                SELECT current_slice_start, slice_timeout 
                FROM crawl_jobs 
                WHERE id = ?
            """, (job_id,)).fetchone()

            if not row or not row['current_slice_start']:
                return False

            start = datetime.fromisoformat(row['current_slice_start'])
            timeout = row['slice_timeout']
            elapsed = (datetime.now() - start).total_seconds()

            return elapsed >= timeout

    def get_all_jobs_status(self) -> List[Dict]:
        """获取所有作业状态（用于 Agent 决策）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT 
                    j.id, j.job_uuid, j.start_url, j.status, 
                    j.processed_pages, j.total_pages, j.priority,
                    j.created_at, j.started_at, j.completed_at,
                    (SELECT COUNT(*) FROM crawl_subtasks s 
                     WHERE s.job_id = j.id AND s.status = 'pending') as pending_count
                FROM crawl_jobs j
                ORDER BY j.priority DESC, j.created_at ASC
            """).fetchall()
            return [dict(row) for row in rows]

    # ==================== SubTask 操作 ====================

    def _create_subtask(self, conn, job_id: int, url: str,
                        task_type: str = 'seed') -> int:
        """创建子任务（内部方法）"""
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        try:
            # ✅ 检查是否已存在相同的 URL（去重）
            existing = cursor.execute(
                "SELECT id FROM crawl_subtasks WHERE job_id = ? AND url = ?",
                (job_id, url)
            ).fetchone()

            if existing:
                logger.info(f"[TaskManager] URL 已存在，跳过: {url[:50]}")
                return existing['id']

            # ✅ 插入新的子任务
            cursor.execute("""
                INSERT INTO crawl_subtasks (job_id, url, task_type, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
            """, (job_id, url, task_type, now, now))

            subtask_id = cursor.lastrowid
            logger.info(f"[TaskManager] _create_subtask 成功: id={subtask_id}, job_id={job_id}, url={url[:50]}")
            return subtask_id

        except Exception as e:
            logger.error(f"[TaskManager] _create_subtask 失败: {e}")
            raise

    def add_subtasks(self, job_id: int, urls: List[str],
                     task_type: str = 'nav') -> int:
        """批量添加子任务"""
        count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            # 获取已存在的 URL
            existing = set()
            rows = cursor.execute(
                "SELECT url FROM crawl_subtasks WHERE job_id = ?", (job_id,)
            ).fetchall()
            existing = {row['url'] for row in rows}

            for url in urls:
                if url and url not in existing:
                    cursor.execute("""
                        INSERT INTO crawl_subtasks (job_id, url, task_type, status, created_at, updated_at)
                        VALUES (?, ?, ?, 'pending', ?, ?)
                    """, (job_id, url, task_type, now, now))
                    count += 1
                    existing.add(url)  # 防止同一批次重复

            conn.commit()
            logger.info(f"[TaskManager] 添加了 {count} 个子任务到 Job {job_id}")

        return count

    def get_next_pending_subtask(self, job_id: int) -> Optional[CrawlSubtask]:
        """获取作业的下一个待处理子任务"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("""
                SELECT * FROM crawl_subtasks 
                WHERE job_id = ? AND status = 'pending'
                ORDER BY 
                    CASE task_type 
                        WHEN 'seed' THEN 0
                        WHEN 'article' THEN 1
                        WHEN 'nav' THEN 2
                        ELSE 3
                    END,
                    created_at ASC
                LIMIT 1
            """, (job_id,)).fetchone()

            if row:
                logger.debug(f"[TaskManager] 找到待处理子任务: id={row['id']}, url={row['url'][:50]}")
                return CrawlSubtask.from_row(dict(row))

            logger.debug(f"[TaskManager] 没有待处理的子任务: job_id={job_id}")
            return None

    def get_pending_subtask_count(self, job_id: int) -> int:
        """获取待处理的子任务数量"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT COUNT(*) as cnt FROM crawl_subtasks WHERE job_id = ? AND status = 'pending'",
                (job_id,)
            ).fetchone()
            return row['cnt'] if row else 0

    def update_subtask_status(self, subtask_id: int, status: str,
                              extracted_data: Dict = None,
                              error_message: str = None) -> bool:
        """更新子任务状态"""
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()

            old = cursor.execute(
                "SELECT status, job_id FROM crawl_subtasks WHERE id = ?",
                (subtask_id,)
            ).fetchone()
            old_status = old['status'] if old else None
            job_id = old['job_id'] if old else None

            updates = {
                'status': status,
                'updated_at': now
            }
            if status == 'running':
                updates['started_at'] = now
            if status in ('completed', 'failed'):
                updates['completed_at'] = now
            if extracted_data:
                updates['extracted_data'] = json.dumps(extracted_data, ensure_ascii=False)
            if error_message:
                updates['error_message'] = error_message
            if status == 'failed':
                updates['retry_count'] = cursor.execute(
                    "SELECT retry_count FROM crawl_subtasks WHERE id = ?", (subtask_id,)
                ).fetchone()['retry_count'] + 1

            set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [subtask_id]

            cursor.execute(f"UPDATE crawl_subtasks SET {set_clause} WHERE id = ?", values)
            conn.commit()

            if job_id:
                self._add_log(conn, job_id, subtask_id, 'subtask_status_change',
                              old_status, status, f'子任务状态变更')

        return True

    # ==================== 日志操作 ====================

    def _add_log(self, conn, job_id: int = None, subtask_id: int = None,
                 action: str = '', old_status: str = '', new_status: str = '',
                 message: str = '', worker: str = ''):
        """添加日志（内部方法）"""
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO crawl_task_logs (job_id, subtask_id, action, old_status, new_status, worker, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, subtask_id, action, old_status, new_status, worker, message, now))

    def get_job_logs(self, job_id: int, limit: int = 100) -> List[Dict]:
        """获取作业日志"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT * FROM crawl_task_logs 
                WHERE job_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (job_id, limit)).fetchall()
            return [dict(row) for row in rows]