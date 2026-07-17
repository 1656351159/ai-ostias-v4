import { Radar } from 'lucide-react'
import { Toaster } from '@/components/ui/sonner'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import TaskSection from '@/sections/TaskSection'
import ResultsSection from '@/sections/ResultsSection'
import SystemSection from '@/sections/SystemSection'

export default function App() {
  return (
    <div className="min-h-screen bg-slate-100/60">
      {/* 顶栏 */}
      <header className="sticky top-0 z-10 border-b bg-slate-900 text-slate-50 shadow-sm">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3">
          <Radar className="h-6 w-6 text-sky-400" />
          <div>
            <h1 className="text-base font-semibold leading-tight">
              AI-OSTIAS 科技情报采集系统
            </h1>
            <p className="text-xs text-slate-400">
              AI-Orchestrated Science &amp; Technology Intelligence Acquisition System
            </p>
          </div>
        </div>
      </header>

      {/* 主内容 */}
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Tabs defaultValue="task">
          <TabsList className="mb-6">
            <TabsTrigger value="task">对话任务</TabsTrigger>
            <TabsTrigger value="results">结果展示</TabsTrigger>
            <TabsTrigger value="system">系统状态</TabsTrigger>
          </TabsList>
          <TabsContent value="task">
            <TaskSection />
          </TabsContent>
          <TabsContent value="results">
            <ResultsSection />
          </TabsContent>
          <TabsContent value="system">
            <SystemSection />
          </TabsContent>
        </Tabs>
      </main>

      <Toaster richColors position="top-center" />
    </div>
  )
}
