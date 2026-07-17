-- db/schema.sql
-- 爬虫任务管理数据库表结构

-- ============================================
-- 作业表（一次完整的爬取请求）
-- ============================================
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uuid TEXT UNIQUE NOT NULL,
    start_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/paused/completed/failed/cancelled
    max_pages INTEGER DEFAULT 15,
    concurrency INTEGER DEFAULT 3,
    priority INTEGER DEFAULT 0,              -- 数字越大优先级越高
    processed_pages INTEGER DEFAULT 0,
    total_pages INTEGER DEFAULT 0,
    slice_timeout INTEGER DEFAULT 30,        -- 时间片长度（秒）
    current_slice_start TIMESTAMP,           -- 当前时间片开始时间
    locked_by TEXT,                          -- Worker 标识
    locked_until TIMESTAMP,                  -- 锁过期时间
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- ============================================
-- 子任务表（每个 URL 的处理单元）
-- ============================================
CREATE TABLE IF NOT EXISTS crawl_subtasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    task_type TEXT NOT NULL,                 -- 'seed' / 'article' / 'nav'
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    extracted_data TEXT,                     -- JSON 格式的提取结果
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

-- ============================================
-- 任务日志表（审计追踪）
-- ============================================
CREATE TABLE IF NOT EXISTS crawl_task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    subtask_id INTEGER,
    action TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT,
    worker TEXT,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES crawl_jobs(id) ON DELETE CASCADE
);

-- ============================================
-- 索引（提升查询性能）
-- ============================================
CREATE INDEX IF NOT EXISTS idx_jobs_status ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON crawl_jobs(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_locked ON crawl_jobs(locked_until);
CREATE INDEX IF NOT EXISTS idx_subtasks_job_status ON crawl_subtasks(job_id, status);
CREATE INDEX IF NOT EXISTS idx_subtasks_url ON crawl_subtasks(url);
CREATE INDEX IF NOT EXISTS idx_logs_job ON crawl_task_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_logs_created ON crawl_task_logs(created_at);