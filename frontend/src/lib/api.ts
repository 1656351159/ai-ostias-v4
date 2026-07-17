/**
 * AI-OSTIAS V4 前端 API 封装
 * 统一使用相对路径 /api，由 vite dev proxy 转发到 http://localhost:8000
 */

// ---------- 类型定义 ----------

export type JobStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type SubtaskStatus = 'pending' | 'running' | 'completed' | 'failed'

export type ControlAction = 'pause' | 'resume' | 'cancel' | 'update'

export interface ParseRequest {
  text: string
}

export interface ParseResponse {
  start_url: string | null
  max_pages: number | null
  keywords: string[] | string
  priority: number | null
  source: 'agent' | 'fallback'
}

export interface CreateJobRequest {
  start_url: string
  extra_urls: string[]
  max_pages: number
  priority: number
  slice_timeout: number
  mock: boolean
}

export interface CreateJobResponse {
  job_id: string
  status: string
}

export interface Job {
  job_id?: string
  job_uuid?: string
  status: JobStatus
  start_url?: string
  extra_urls?: string[]
  max_pages?: number
  processed_pages?: number
  total_pages?: number
  priority?: number
  mock?: boolean
  created_at?: string
  updated_at?: string
  adapter_state?: string | null
  error?: string | null
  error_message?: string | null
  [key: string]: unknown
}

export interface JobListResponse {
  total: number
  items: Job[]
}

export interface Subtask {
  subtask_id?: string
  url: string
  task_type?: string
  type?: string
  status: SubtaskStatus
  retry_count?: number
  retries?: number
  [key: string]: unknown
}

export interface JobLog {
  ts?: string
  time?: string
  created_at?: string
  action?: string
  message?: string
  level?: string
  [key: string]: unknown
}

export interface AdapterInfo {
  state: string
  [key: string]: unknown
}

export interface JobStatusResponse {
  job: Job | null
  subtasks: Subtask[]
  logs: JobLog[]
  adapter: AdapterInfo | null
  note?: string
}

export interface ControlRequest {
  action: ControlAction
  params?: {
    max_pages?: number
    priority?: number
  }
}

export interface ResultItem {
  url: string
  title: string
  pub_date: string | null
  site_name: string
  task_type: string
  created_at: string
  [key: string]: unknown
}

export interface ResultsQuery {
  site?: string
  url_kw?: string
  task_type?: string
  created_from?: string
  created_to?: string
  sort?: 'created_at' | 'url'
  order?: 'asc' | 'desc'
  page?: number
  size?: number
}

export interface ResultsResponse {
  total: number
  items: ResultItem[]
}

export interface SystemStatus {
  db: unknown
  openclaw: {
    ok: boolean
    [key: string]: unknown
  }
  version: string
  [key: string]: unknown
}

export class ApiError extends Error {
  status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// ---------- 基础请求 ----------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (e) {
    throw new ApiError(
      `网络请求失败：${e instanceof Error ? e.message : String(e)}（请确认后端已启动）`,
    )
  }
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      detail = body?.detail ? `：${typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)}` : ''
    } catch {
      // 忽略非 JSON 错误体
    }
    throw new ApiError(`请求失败 HTTP ${res.status}${detail}`, res.status)
  }
  return (await res.json()) as T
}

// ---------- 接口 ----------

export const api = {
  /** 对话式解析任务参数 */
  parseTask: (text: string) =>
    request<ParseResponse>('/api/tasks/parse', {
      method: 'POST',
      body: JSON.stringify({ text } satisfies ParseRequest),
    }),

  /** 创建采集任务 */
  createJob: (payload: CreateJobRequest) =>
    request<CreateJobResponse>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  /** Job 列表（倒序） */
  listJobs: () => request<JobListResponse>('/api/jobs'),

  /** 单个 Job 状态（含子任务、日志、adapter） */
  jobStatus: (jobId: string) =>
    request<JobStatusResponse>(`/api/jobs/${encodeURIComponent(jobId)}/status`),

  /** 任务控制：暂停/恢复/取消/更新参数 */
  controlJob: (jobId: string, action: ControlAction, params?: ControlRequest['params']) =>
    request<unknown>(`/api/jobs/${encodeURIComponent(jobId)}/control`, {
      method: 'POST',
      body: JSON.stringify({ action, params } satisfies ControlRequest),
    }),

  /** 结果查询 */
  listResults: (q: ResultsQuery) => {
    const sp = new URLSearchParams()
    if (q.site) sp.set('site', q.site)
    if (q.url_kw) sp.set('url_kw', q.url_kw)
    if (q.task_type) sp.set('task_type', q.task_type)
    if (q.created_from) sp.set('created_from', q.created_from)
    if (q.created_to) sp.set('created_to', q.created_to)
    if (q.sort) sp.set('sort', q.sort)
    if (q.order) sp.set('order', q.order)
    if (q.page != null) sp.set('page', String(q.page))
    if (q.size != null) sp.set('size', String(q.size))
    const qs = sp.toString()
    return request<ResultsResponse>(`/api/results${qs ? `?${qs}` : ''}`)
  },

  /** 结果导出 URL（带筛选条件） */
  exportUrl: (format: 'csv' | 'json', q: ResultsQuery) => {
    const sp = new URLSearchParams()
    sp.set('format', format)
    if (q.site) sp.set('site', q.site)
    if (q.url_kw) sp.set('url_kw', q.url_kw)
    if (q.task_type) sp.set('task_type', q.task_type)
    if (q.created_from) sp.set('created_from', q.created_from)
    if (q.created_to) sp.set('created_to', q.created_to)
    return `/api/results/export?${sp.toString()}`
  },

  /** 系统状态 */
  systemStatus: () => request<SystemStatus>('/api/system/status'),
}

// ---------- 展示辅助 ----------

export const JOB_STATUS_META: Record<JobStatus, { label: string; className: string }> = {
  pending: { label: '排队中', className: 'bg-slate-100 text-slate-600 border-slate-300' },
  running: { label: '运行中', className: 'bg-blue-100 text-blue-700 border-blue-300' },
  paused: { label: '已暂停', className: 'bg-amber-100 text-amber-700 border-amber-300' },
  completed: { label: '已完成', className: 'bg-emerald-100 text-emerald-700 border-emerald-300' },
  failed: { label: '失败', className: 'bg-red-100 text-red-700 border-red-300' },
  cancelled: { label: '已取消', className: 'bg-slate-100 text-slate-500 border-slate-300' },
}

export const SUBTASK_STATUS_META: Record<SubtaskStatus, { label: string; className: string }> = {
  pending: { label: '等待', className: 'bg-slate-100 text-slate-600 border-slate-300' },
  running: { label: '运行中', className: 'bg-blue-100 text-blue-700 border-blue-300' },
  completed: { label: '完成', className: 'bg-emerald-100 text-emerald-700 border-emerald-300' },
  failed: { label: '失败', className: 'bg-red-100 text-red-700 border-red-300' },
}

export function isTerminalStatus(s: JobStatus): boolean {
  return s === 'completed' || s === 'failed' || s === 'cancelled'
}

export function fmtTime(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  return d.toLocaleString('zh-CN', { hour12: false })
}
