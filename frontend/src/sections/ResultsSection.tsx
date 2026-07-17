import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  AlertCircle,
  ArrowDownWideNarrow,
  ArrowUpNarrowWide,
  Download,
  ExternalLink,
  Loader2,
  Search,
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
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  api,
  fmtTime,
  type ResultItem,
  type ResultsQuery,
} from '@/lib/api'

const PAGE_SIZE = 20

type SortKey = 'created_at' | 'url'

export default function ResultsSection() {
  // ---- 筛选条件（输入态） ----
  const [site, setSite] = useState('')
  const [urlKw, setUrlKw] = useState('')
  const [taskType, setTaskType] = useState('all')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')

  // ---- 查询态 ----
  const [query, setQuery] = useState<ResultsQuery>({})
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<SortKey>('created_at')
  const [sortAsc, setSortAsc] = useState(false)

  // ---- 数据 ----
  const [items, setItems] = useState<ResultItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ---- 勾选 ----
  const [checked, setChecked] = useState<Set<string>>(new Set())

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.listResults({
        ...query,
        sort,
        order: sortAsc ? 'asc' : 'desc',
        page,
        size: PAGE_SIZE,
      })
      setItems(r.items ?? [])
      setTotal(r.total ?? 0)
      setChecked(new Set())
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      toast.error(`结果加载失败：${msg}`)
    } finally {
      setLoading(false)
    }
  }, [query, page, sort, sortAsc])

  useEffect(() => {
    void load()
  }, [load])

  const applyFilter = () => {
    setPage(1)
    setQuery({
      site: site.trim() || undefined,
      url_kw: urlKw.trim() || undefined,
      task_type: taskType === 'all' ? undefined : taskType,
      created_from: start || undefined,
      created_to: end || undefined,
    })
  }

  const resetFilter = () => {
    setSite('')
    setUrlKw('')
    setTaskType('all')
    setStart('')
    setEnd('')
    setPage(1)
    setQuery({})
  }

  const toggleSort = (key: SortKey) => {
    if (sort === key) {
      setSortAsc((v) => !v)
    } else {
      setSort(key)
      setSortAsc(false)
    }
  }

  const toggleRow = (url: string, v: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (v) next.add(url)
      else next.delete(url)
      return next
    })
  }

  const allChecked = items.length > 0 && items.every((it) => checked.has(it.url))
  const toggleAll = (v: boolean) => {
    setChecked(v ? new Set(items.map((it) => it.url)) : new Set())
  }

  const doExport = (format: 'csv' | 'json') => {
    const url = api.exportUrl(format, query)
    window.open(url, '_blank')
    toast.success(`正在导出 ${format.toUpperCase()}（含当前筛选条件）`)
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* 左侧筛选栏 */}
      <Card className="w-full shrink-0 lg:w-64">
        <CardHeader>
          <CardTitle className="text-base">筛选条件</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>站点</Label>
            <Input
              value={site}
              onChange={(e) => setSite(e.target.value)}
              placeholder="如 example.com"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label>URL 关键词</Label>
            <Input
              value={urlKw}
              onChange={(e) => setUrlKw(e.target.value)}
              placeholder="如 ai"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label>类型</Label>
            <Select value={taskType} onValueChange={setTaskType}>
              <SelectTrigger>
                <SelectValue placeholder="全部类型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                <SelectItem value="article">文章页</SelectItem>
                <SelectItem value="nav">导航页</SelectItem>
                <SelectItem value="seed">种子页</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label>创建时间起</Label>
            <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label>创建时间止</Label>
            <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <Button className="flex-1" onClick={applyFilter}>
              <Search className="mr-1 h-4 w-4" /> 查询
            </Button>
            <Button variant="outline" onClick={resetFilter}>
              重置
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 右侧结果区 */}
      <Card className="min-w-0 flex-1">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">采集结果</CardTitle>
              <CardDescription>
                共 {total} 条{checked.size > 0 ? `，已勾选 ${checked.size} 条` : ''}
                {loading && <Loader2 className="ml-2 inline h-3 w-3 animate-spin" />}
              </CardDescription>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => doExport('csv')}>
                <Download className="mr-1 h-4 w-4" /> 导出 CSV
              </Button>
              <Button variant="outline" size="sm" onClick={() => doExport('json')}>
                <Download className="mr-1 h-4 w-4" /> 导出 JSON
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {error && (
            <div className="flex items-center gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span className="flex-1">{error}</span>
              <Button variant="outline" size="sm" onClick={() => void load()}>
                重试
              </Button>
            </div>
          )}

          <div className="overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={allChecked}
                      onCheckedChange={(v) => toggleAll(v === true)}
                      aria-label="全选"
                    />
                  </TableHead>
                  <TableHead>标题</TableHead>
                  <TableHead className="w-28">发布日期</TableHead>
                  <TableHead className="w-32">来源站点</TableHead>
                  <TableHead className="w-44">
                    <button
                      className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() => toggleSort('url')}
                    >
                      URL
                      {sort === 'url' &&
                        (sortAsc ? (
                          <ArrowUpNarrowWide className="h-3 w-3" />
                        ) : (
                          <ArrowDownWideNarrow className="h-3 w-3" />
                        ))}
                    </button>
                  </TableHead>
                  <TableHead className="w-20">类型</TableHead>
                  <TableHead className="w-40">
                    <button
                      className="inline-flex items-center gap-1 hover:text-foreground"
                      onClick={() => toggleSort('created_at')}
                    >
                      创建时间
                      {sort === 'created_at' &&
                        (sortAsc ? (
                          <ArrowUpNarrowWide className="h-3 w-3" />
                        ) : (
                          <ArrowDownWideNarrow className="h-3 w-3" />
                        ))}
                    </button>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center text-muted-foreground">
                      {loading ? '加载中…' : '暂无数据'}
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((it) => (
                    <TableRow key={it.url}>
                      <TableCell>
                        <Checkbox
                          checked={checked.has(it.url)}
                          onCheckedChange={(v) => toggleRow(it.url, v === true)}
                          aria-label={`选择 ${it.title}`}
                        />
                      </TableCell>
                      <TableCell className="max-w-52">
                        <span className="line-clamp-2" title={it.title}>
                          {it.title || '（无标题）'}
                        </span>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {it.pub_date ?? '—'}
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">{it.site_name || '—'}</Badge>
                      </TableCell>
                      <TableCell className="max-w-44">
                        <a
                          href={it.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex max-w-full items-center gap-1 truncate font-mono text-xs text-blue-600 hover:underline"
                          title={it.url}
                        >
                          <span className="truncate">{it.url}</span>
                          <ExternalLink className="h-3 w-3 shrink-0" />
                        </a>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{it.task_type || '—'}</Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {fmtTime(it.created_at)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          {/* 分页 */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              第 {page} / {totalPages} 页
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
              >
                下一页
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
