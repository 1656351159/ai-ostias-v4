import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 通用轮询 Hook
 * @param fetcher 每次轮询执行的异步函数
 * @param intervalMs 轮询间隔（毫秒）
 * @param enabled 是否启用轮询（false 时停止）
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled: boolean,
) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const tick = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
      setLastUpdated(new Date())
    } catch (e) {
      // 轮询失败不炸页面，仅记录错误
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    void tick()
    const id = window.setInterval(() => void tick(), intervalMs)
    return () => window.clearInterval(id)
  }, [enabled, intervalMs, tick])

  return { data, error, loading, lastUpdated, refresh: tick }
}
