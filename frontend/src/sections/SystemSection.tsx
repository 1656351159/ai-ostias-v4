import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Database,
  Loader2,
  RefreshCw,
  Server,
  XCircle,
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
import { api, type SystemStatus } from '@/lib/api'

function StatusDot({ ok }: { ok: boolean }) {
  return ok ? (
    <span className="inline-flex items-center gap-1.5 text-emerald-600">
      <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
      正常
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-red-600">
      <span className="h-2.5 w-2.5 rounded-full bg-red-500" />
      异常
    </span>
  )
}

/** 从 db 字段提取可读摘要 */
function dbSummary(db: unknown): { ok: boolean; text: string } {
  if (db == null) return { ok: false, text: '未知' }
  if (typeof db === 'boolean') return { ok: db, text: db ? '已连接' : '未连接' }
  if (typeof db === 'string') return { ok: /ok|connected|true/i.test(db), text: db }
  if (typeof db === 'object') {
    const d = db as Record<string, unknown>
    const ok = Boolean(d.ok ?? d.connected ?? d.status === 'ok')
    const text =
      (typeof d.message === 'string' && d.message) ||
      (typeof d.driver === 'string' && d.driver) ||
      (typeof d.database === 'string' && d.database) ||
      JSON.stringify(db)
    return { ok, text }
  }
  return { ok: false, text: String(db) }
}

/** 从 openclaw 字段提取 preflight 摘要 */
function openclawSummary(oc: SystemStatus['openclaw'] | undefined): {
  ok: boolean
  version?: string
  agent?: string
  gateway?: string
} {
  if (!oc) return { ok: false }
  const o = oc as Record<string, unknown>
  const pick = (...keys: string[]): string | undefined => {
    for (const k of keys) {
      const v = o[k]
      if (typeof v === 'string' && v) return v
      if (v && typeof v === 'object') {
        const vv = (v as Record<string, unknown>).version ?? (v as Record<string, unknown>).state
        if (typeof vv === 'string' && vv) return vv
      }
    }
    return undefined
  }
  return {
    ok: Boolean(oc.ok),
    version: pick('version'),
    agent: pick('agent', 'agent_id', 'agent_status'),
    gateway: pick('gateway', 'gateway_url', 'gateway_status'),
  }
}

export default function SystemSection() {
  const [data, setData] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.systemStatus()
      setData(r)
      setCheckedAt(new Date())
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      toast.error(`系统状态检测失败：${msg}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const db = data ? dbSummary(data.db) : null
  const oc = data ? openclawSummary(data.openclaw) : null

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">系统状态</h2>
          {checkedAt && (
            <p className="text-sm text-muted-foreground">
              最近检测：{checkedAt.toLocaleString('zh-CN', { hour12: false })}
            </p>
          )}
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-1 h-4 w-4" />
          )}
          重新检测
        </Button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            重试
          </Button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {/* 数据库 */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="h-4 w-4 text-muted-foreground" />
              数据库连接
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {db ? (
              <>
                <StatusDot ok={db.ok} />
                <p className="break-all text-sm text-muted-foreground">{db.text}</p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">等待检测…</p>
            )}
          </CardContent>
        </Card>

        {/* OpenClaw */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4 text-muted-foreground" />
              OpenClaw
            </CardTitle>
            <CardDescription>preflight 摘要</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {oc ? (
              <>
                <StatusDot ok={oc.ok} />
                <div className="flex flex-col gap-1 text-sm text-muted-foreground">
                  {oc.version && (
                    <span className="flex items-center gap-1">
                      {oc.ok ? (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 text-red-500" />
                      )}
                      版本：{oc.version}
                    </span>
                  )}
                  {oc.agent && <span>Agent：{oc.agent}</span>}
                  {oc.gateway && <span>Gateway：{oc.gateway}</span>}
                  {!oc.version && !oc.agent && !oc.gateway && (
                    <span className="break-all font-mono text-xs">
                      {JSON.stringify(data?.openclaw)}
                    </span>
                  )}
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">等待检测…</p>
            )}
          </CardContent>
        </Card>

        {/* 后端版本 */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Server className="h-4 w-4 text-muted-foreground" />
              后端版本
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data ? (
              <Badge variant="secondary" className="font-mono text-sm">
                {data.version}
              </Badge>
            ) : (
              <p className="text-sm text-muted-foreground">等待检测…</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
