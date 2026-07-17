import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  AlertCircle,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Send,
  Square,
  Trash2,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import {
  api,
  fmtTime,
  isTerminalStatus,
  JOB_STATUS_META,
  SUBTASK_STATUS_META,
  type JobLog,
  type JobStatus,
  type Subtask,
} from '@/lib/api'
import { usePoll } from '@/hooks/usePoll'

/** 参数确认卡片的状态 */
interface ConfirmParams {
  startUrl: string
  extraUrls: string[]
  maxPages: number
  priority: number
  keywordsText: string
  mock: boolean
  source?: 'agent' | 'fallback'
}

function logTime(l: JobLog): string {
  return l.ts ?? l.time ?? l.created_at ?? ''
}

function subtaskType(s: Subtask): string {
  return s.task_type ?? s.type ?? '—'
}

function subtaskRetry(s: Subtask): number {
  return s.retry_count ?? s.retries ?? 0
}

const TYPE_LABEL: Record<string, string> = {
  seed: '种子页',
  article: '文章页',
  nav: '导航页',
}

export default function TaskSection() {
  // ---- URL 列表 ----
  const [urls, setUrls] = useState<string[]>([''])
  // ---- 任务描述 ----
  const [text, setText] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)

  // ---- 参数确认 ----
  const [confirm, setConfirm] = useState<ConfirmParams | null>(null)
  const [creating, setCreating] = useState(false)

  // ---- 当前任务 ----
  const [jobId, setJobId] = useState<string | null>(null)
  const [terminalReached, setTerminalReached] = useState(false)

  // ---- 控制条 ----
  const [newMaxPages, setNewMaxPages] = useState('')
  const [controlling, setControlling] = useState(false)

  // ---- 轮询 ----
  const pollEnabled = jobId != null && !terminalReached
  const { data, error: pollError, loading, refresh } = usePoll(
    () => api.jobStatus(jobId!),
    5000,
    pollEnabled,
  )
  const job = data?.job ?? null
  const adapter = data?.adapter ?? null
  const note = data?.note
  const subtasks = data?.subtasks ?? []
  const logs = useMemo(
    () =>
      [...(data?.logs ?? [])].sort((a, b) =>
        logTime(a).localeCompare(logTime(b)),
      ),
    [data?.logs],
  )

  // 终态检测：到达终态后停止轮询
  // （DB 记录未创建时 job 为 null，此时以 adapter.state 作为兜底判断）
  useEffect(() => {
    if (job && isTerminalStatus(job.status)) {
      setTerminalReached(true)
    } else if (
      !job &&
      adapter &&
      ['completed', 'failed', 'cancelled'].includes(adapter.state)
    ) {
      setTerminalReached(true)
    }
  }, [job, adapter])

  // 日志自动滚到底
  const logEndRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs.length])

  // ---- URL 行操作 ----
  const setUrl = (i: number, v: string) =>
    setUrls((prev) => prev.map((u, idx) => (idx === i ? v : u)))
  const addUrl = () => setUrls((prev) => [...prev, ''])
  const removeUrl = (i: number) =>
    setUrls((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev))

  // ---- 解析参数 ----
  const handleParse = async () => {
    if (!text.trim()) {
      toast.error('请先输入任务描述')
      return
    }
    setParsing(true)
    setParseError(null)
    try {
      const r = await api.parseTask(text.trim())
      const typedUrls = urls.map((u) => u.trim()).filter(Boolean)
      const keywordsText = Array.isArray(r.keywords)
        ? r.keywords.join('，')
        : (r.keywords ?? '')
      setConfirm({
        startUrl: typedUrls[0] ?? r.start_url ?? '',
        extraUrls: typedUrls.length > 1 ? typedUrls.slice(1) : [],
        maxPages: r.max_pages ?? 10,
        priority: r.priority ?? 5,
        keywordsText,
        mock: true,
        source: r.source,
      })
      toast.success('参数解析完成，请确认后开始任务')
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setParseError(msg)
      toast.error(`参数解析失败：${msg}`)
    } finally {
      setParsing(false)
    }
  }

  // ---- 确认开始 ----
  const handleCreate = async () => {
    if (!confirm) return
    if (!confirm.startUrl.trim()) {
      toast.error('起始 URL 不能为空')
      return
    }
    setCreating(true)
    try {
      const r = await api.createJob({
        start_url: confirm.startUrl.trim(),
        extra_urls: confirm.extraUrls.map((u) => u.trim()).filter(Boolean),
        max_pages: confirm.maxPages,
        priority: confirm.priority,
        slice_timeout: 30,
        mock: confirm.mock,
      })
      setJobId(r.job_id)
      setTerminalReached(false)
      setNewMaxPages(String(confirm.maxPages))
      toast.success(`任务已提交（${r.job_id.slice(0, 8)}…）`)
    } catch (e) {
      toast.error(`任务提交失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setCreating(false)
    }
  }

  // ---- 任务控制 ----
  const handleControl = async (action: 'pause' | 'resume' | 'cancel') => {
    if (!jobId) return
    setControlling(true)
    try {
      await api.controlJob(jobId, action)
      const label = action === 'pause' ? '已暂停' : action === 'resume' ? '已恢复' : '已取消'
      toast.success(`任务${label}`)
      await refresh()
    } catch (e) {
      toast.error(`操作失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setControlling(false)
    }
  }

  const handleUpdateMaxPages = async () => {
    if (!jobId) return
    const n = Number(newMaxPages)
    if (!Number.isInteger(n) || n <= 0) {
      toast.error('请输入正整数的最大页数')
      return
    }
    setControlling(true)
    try {
      await api.controlJob(jobId, 'update', { max_pages: n })
      toast.success(`最大页数已更新为 ${n}`)
      await refresh()
    } catch (e) {
      toast.error(`更新失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setControlling(false)
    }
  }

  const status: JobStatus | null = job?.status ?? null
  const meta = status ? JOB_STATUS_META[status] : null
  const processed = job?.processed_pages ?? 0
  const maxPages = job?.max_pages ?? confirm?.maxPages ?? 0
  const progress = maxPages > 0 ? Math.min(100, Math.round((processed / maxPages) * 100)) : 0

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      {/* 任务输入区 */}
      <Card>
        <CardHeader>
          <CardTitle>对话式创建采集任务</CardTitle>
          <CardDescription>
            填写目标 URL 并用自然语言描述采集需求，系统将自动解析任务参数
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>任务 URL 列表（第一行为起始 URL）</Label>
            {urls.map((u, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  value={u}
                  onChange={(e) => setUrl(i, e.target.value)}
                  placeholder={
                    i === 0 ? '起始 URL，例如 https://example.com/news' : '附加 URL（可选）'
                  }
                />
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => removeUrl(i)}
                  disabled={urls.length <= 1}
                  title="删除此行"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button variant="outline" size="sm" className="self-start" onClick={addUrl}>
              <Plus className="mr-1 h-4 w-4" /> 添加 URL
            </Button>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="task-text">任务描述</Label>
            <Textarea
              id="task-text"
              className="min-h-32"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="例如：采集该站点最近的人工智能与芯片相关新闻，最多 20 页，优先级高"
            />
          </div>

          {parseError && (
            <div className="flex items-center gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span className="flex-1">{parseError}</span>
              <Button variant="outline" size="sm" onClick={handleParse} disabled={parsing}>
                重试
              </Button>
            </div>
          )}

          <Button onClick={handleParse} disabled={parsing} className="self-end">
            {parsing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            解析参数
          </Button>
        </CardContent>
      </Card>

      {/* 参数确认卡片 */}
      {confirm && (
        <Card className="border-blue-200">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              参数确认
              {confirm.source && (
                <Badge variant="outline">
                  {confirm.source === 'agent' ? 'Agent 解析' : '兜底解析'}
                </Badge>
              )}
            </CardTitle>
            <CardDescription>请核对以下参数，确认无误后启动采集任务</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div className="flex flex-col gap-2 md:col-span-2">
                <Label>起始 URL</Label>
                <Input
                  value={confirm.startUrl}
                  onChange={(e) => setConfirm({ ...confirm, startUrl: e.target.value })}
                />
              </div>
              <div className="flex flex-col gap-2 md:col-span-2">
                <Label>附加 URL（每行一个，可留空）</Label>
                <Textarea
                  className="min-h-20"
                  value={confirm.extraUrls.join('\n')}
                  onChange={(e) =>
                    setConfirm({
                      ...confirm,
                      extraUrls: e.target.value.split('\n').filter((s) => s.trim()),
                    })
                  }
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label>最大页数</Label>
                <Input
                  type="number"
                  min={1}
                  value={confirm.maxPages}
                  onChange={(e) =>
                    setConfirm({ ...confirm, maxPages: Number(e.target.value) || 1 })
                  }
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label>优先级（数值越大越优先）</Label>
                <Input
                  type="number"
                  value={confirm.priority}
                  onChange={(e) =>
                    setConfirm({ ...confirm, priority: Number(e.target.value) || 0 })
                  }
                />
              </div>
              <div className="flex flex-col gap-2 md:col-span-2">
                <Label>关键词（用逗号分隔）</Label>
                <Input
                  value={confirm.keywordsText}
                  onChange={(e) => setConfirm({ ...confirm, keywordsText: e.target.value })}
                />
              </div>
              <div className="flex items-center gap-3 md:col-span-2">
                <Switch
                  id="mock-switch"
                  checked={confirm.mock}
                  onCheckedChange={(v) => setConfirm({ ...confirm, mock: v })}
                />
                <Label htmlFor="mock-switch" className="cursor-pointer">
                  mock 模式
                  <span className="ml-2 text-xs text-muted-foreground">（演示模式，不触发真实抓取）</span>
                </Label>
              </div>
            </div>
            <Button onClick={handleCreate} disabled={creating} className="self-end">
              {creating ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Send className="mr-2 h-4 w-4" />
              )}
              确认开始
            </Button>
          </CardContent>
        </Card>
      )}

      {/* 队列监控面板 */}
      {jobId && (
        <Card>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-3">
              队列监控
              {meta ? (
                <Badge variant="outline" className={meta.className}>
                  {meta.label}
                </Badge>
              ) : (
                <Badge variant="outline" className="bg-slate-100 text-slate-600 border-slate-300">
                  启动中
                </Badge>
              )}
              <span className="font-mono text-xs text-muted-foreground">
                Job {jobId.slice(0, 8)}…
              </span>
              {adapter && (
                <Badge variant="secondary">Agent: {adapter.state}</Badge>
              )}
            </CardTitle>
            <CardDescription>
              每 5 秒自动刷新任务进度
              {loading && <Loader2 className="ml-2 inline h-3 w-3 animate-spin" />}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            {note && !job && (
              <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700">
                {note}
              </div>
            )}
            {pollError && (
              <div className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-700">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <span className="flex-1">状态刷新失败：{pollError}（将继续自动重试）</span>
                <Button variant="outline" size="sm" onClick={() => void refresh()}>
                  重试
                </Button>
              </div>
            )}

            {/* 进度条 */}
            <div className="flex flex-col gap-2">
              <div className="flex justify-between text-sm">
                <span>采集进度</span>
                <span className="tabular-nums text-muted-foreground">
                  {processed} / {maxPages} 页（{progress}%）
                </span>
              </div>
              <Progress value={progress} />
            </div>

            {/* 子任务表格 */}
            <div className="flex flex-col gap-2">
              <Label>子任务（{subtasks.length}）</Label>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>URL</TableHead>
                      <TableHead className="w-24">类型</TableHead>
                      <TableHead className="w-24">状态</TableHead>
                      <TableHead className="w-20 text-right">重试</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {subtasks.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={4} className="text-center text-muted-foreground">
                          暂无子任务
                        </TableCell>
                      </TableRow>
                    ) : (
                      subtasks.map((s, i) => {
                        const sm = SUBTASK_STATUS_META[s.status] ?? SUBTASK_STATUS_META.pending
                        const t = subtaskType(s)
                        return (
                          <TableRow key={s.subtask_id ?? i}>
                            <TableCell className="max-w-0 truncate font-mono text-xs" title={s.url}>
                              {s.url}
                            </TableCell>
                            <TableCell>
                              <Badge variant="secondary">{TYPE_LABEL[t] ?? t}</Badge>
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline" className={sm.className}>
                                {sm.label}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {subtaskRetry(s)}
                            </TableCell>
                          </TableRow>
                        )
                      })
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>

            {/* 流式日志 */}
            <div className="flex flex-col gap-2">
              <Label>执行日志（最近 {logs.length} 条）</Label>
              <ScrollArea className="h-48 rounded-md border bg-slate-50 p-3">
                {logs.length === 0 ? (
                  <p className="text-sm text-muted-foreground">暂无日志</p>
                ) : (
                  <div className="flex flex-col gap-1 font-mono text-xs">
                    {logs.map((l, i) => (
                      <div key={i} className="flex gap-2">
                        <span className="shrink-0 text-muted-foreground">
                          {fmtTime(logTime(l))}
                        </span>
                        {l.action && (
                          <span className="shrink-0 font-semibold text-blue-700">
                            [{l.action}]
                          </span>
                        )}
                        <span className="break-all">{l.message ?? JSON.stringify(l)}</span>
                      </div>
                    ))}
                    <div ref={logEndRef} />
                  </div>
                )}
              </ScrollArea>
            </div>

            <Button
              variant="outline"
              size="sm"
              className="self-start"
              onClick={() => void refresh()}
              disabled={loading}
            >
              <RefreshCw className={`mr-1 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </Button>

            {terminalReached && (
              <div className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-700">
                任务已结束{meta ? `（${meta.label}）` : ''}，请到「结果展示」标签页查看采集结果。
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* 任务控制条 */}
      {jobId && (
        <Card>
          <CardHeader>
            <CardTitle>任务控制</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              onClick={() => handleControl('pause')}
              disabled={controlling || status !== 'running'}
            >
              <Pause className="mr-1 h-4 w-4" /> 暂停
            </Button>
            <Button
              variant="outline"
              onClick={() => handleControl('resume')}
              disabled={controlling || status !== 'paused'}
            >
              <Play className="mr-1 h-4 w-4" /> 恢复
            </Button>
            <Button
              variant="destructive"
              onClick={() => handleControl('cancel')}
              disabled={controlling || status == null || terminalReached}
            >
              <Square className="mr-1 h-4 w-4" /> 取消
            </Button>
            <div className="ml-auto flex items-center gap-2">
              <Label htmlFor="new-max-pages" className="whitespace-nowrap">
                最大页数
              </Label>
              <Input
                id="new-max-pages"
                type="number"
                min={1}
                className="w-24"
                value={newMaxPages}
                onChange={(e) => setNewMaxPages(e.target.value)}
                disabled={terminalReached}
              />
              <Button
                variant="secondary"
                onClick={handleUpdateMaxPages}
                disabled={controlling || terminalReached}
              >
                应用
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
