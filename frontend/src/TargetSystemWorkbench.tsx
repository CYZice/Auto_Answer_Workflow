import axios from 'axios'
import { ChevronLeft, ChevronRight, Download, ExternalLink, Eye, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '' })
type TargetTask = {
  id: number; remote_task_id: string; title: string; status: string; workflow_task_id?: string | null
  workflow_state?: string | null; exam_point: string; delivery_order?: number | null; error_message?: string | null
  school_name?: string; subject_name?: string; question_text?: string; image_urls?: string[]
  rendered_answer_url?: string | null; browser_screenshot_url?: string | null
}

type FilterOption = { id: number; name: string }
type SyncStatus = { state: string; synced: number; imported: number; schools_done: number; schools_total: number; error: string }
type WorkflowTask = { task_id: string; answer_preview?: string | null; final_result?: string | null }

const statusLabel: Record<string, string> = {
  discovered: '待选择', selected: '已选择', claimed: '已抢题', solving: '解题中', review_pending: '已解答，待人工填入',
  abandoned: '已撤回', remote_closed: '远端已关闭',
}

const apiErrorMessage = (error: unknown, fallback: string) => {
  if (axios.isAxiosError(error)) {
    return (error.response?.data as { detail?: string } | undefined)?.detail || error.message || fallback
  }
  return error instanceof Error ? error.message : fallback
}

export default function TargetSystemWorkbench() {
  const [pageSize, setPageSize] = useState(() => window.matchMedia('(max-width: 767px)').matches ? 10 : 20)
  const [items, setItems] = useState<TargetTask[]>([])
  const [filters, setFilters] = useState<{ schools: FilterOption[]; subjects: FilterOption[] }>({ schools: [], subjects: [] })
  const [selected, setSelected] = useState<string[]>([])
  const [details, setDetails] = useState<Record<number, TargetTask>>({})
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [loadingDetail, setLoadingDetail] = useState<number | null>(null)
  const [syncInfo, setSyncInfo] = useState<SyncStatus | null>(null)
  const [schoolId, setSchoolId] = useState('')
  const [subjectId, setSubjectId] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [allTotal, setAllTotal] = useState(0)
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({})
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [previewTask, setPreviewTask] = useState<WorkflowTask | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const refresh = async () => {
    const params = { page, page_size: pageSize, ...(schoolId ? { school_id: Number(schoolId) } : {}), ...(subjectId ? { subject_id: Number(subjectId) } : {}), ...(statusFilter ? { status: statusFilter } : {}) }
    const [tasks, status] = await Promise.all([
      api.get('/api/target-system/tasks', { params }),
      api.get('/api/target-system/sync/status'),
    ])
    setItems(tasks.data.items || [])
    setTotal(tasks.data.total || 0)
    setAllTotal(tasks.data.all_total || 0)
    setStatusCounts(tasks.data.status_counts || {})
    setSyncInfo(status.data)
  }

  useEffect(() => {
    const params = schoolId ? { school_id: Number(schoolId) } : undefined
    void api.get('/api/target-system/filters', { params }).then(({ data }) => setFilters({ schools: data.schools || [], subjects: data.subjects || [] })).catch(() => undefined)
  }, [schoolId])
  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)')
    const updatePageSize = (event: MediaQueryListEvent) => {
      setPageSize(event.matches ? 10 : 20)
      setPage(1)
    }
    media.addEventListener('change', updatePageSize)
    return () => media.removeEventListener('change', updatePageSize)
  }, [])
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer) }, [page, pageSize, schoolId, subjectId, statusFilter])

  const perform = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setMessage('')
    try { await action(); await refresh() } catch (error: unknown) { setMessage(apiErrorMessage(error, '操作失败')) } finally { setBusy('') }
  }

  const toggle = (remoteId: string) => setSelected((current) => current.includes(remoteId) ? current.filter((id) => id !== remoteId) : [...current, remoteId])
  const returnToAll = (item: TargetTask) => {
    if (!window.confirm('这会停止本地解题/审核/填入流程，并让题目回到“全部”和“待选择”列表。不会向目标系统发送取消抢题或放弃请求。继续吗？')) return
    void perform(`return-${item.id}`, () => api.post(`/api/target-system/tasks/${item.id}/return-to-all`))
  }
  const toggleDetail = async (item: TargetTask) => {
    if (expandedId === item.id) { setExpandedId(null); return }
    setExpandedId(item.id)
    if (details[item.id]) return
    setLoadingDetail(item.id)
    try { const { data } = await api.get(`/api/target-system/tasks/${item.id}/detail`); setDetails((current) => ({ ...current, [item.id]: data })) } finally { setLoadingDetail(null) }
  }
  const openAnswerPreview = async (taskId: string) => {
    setPreviewTask(null)
    setPreviewError('')
    setPreviewLoading(true)
    try {
      const { data } = await api.get<WorkflowTask>(`/api/admin/tasks/${encodeURIComponent(taskId)}`)
      setPreviewTask(data)
    } catch (error: unknown) {
      setPreviewError(apiErrorMessage(error, '无法加载 AI 结果'))
    } finally {
      setPreviewLoading(false)
    }
  }
  const openAiResearch = () => {
    // 移动浏览器只会允许点击事件同步触发的新标签页，不能等接口返回后再打开。
    const opened = window.open('about:blank', '_blank')
    if (!opened) {
      setMessage('浏览器阻止了新标签页，请允许弹窗后重试。')
      return
    }
    opened.document.title = '正在打开学解…'
    setBusy('open-ai-research')
    setMessage('')
    void api.post('/api/target-system/browser/open-ai-research').then(({ data }) => {
      const accessUrl = typeof data.access_url === 'string' ? data.access_url : ''
      if (!accessUrl) throw new Error('未获取到学解页面地址。')
      opened.location.replace(new URL(accessUrl, window.location.origin).toString())
      setMessage(data.message || '已在新标签页打开学解 AI Research。')
      void refresh()
    }).catch((error: unknown) => {
      opened.close()
      setMessage(apiErrorMessage(error, '无法打开学解 AI Research。'))
    }).finally(() => setBusy(''))
  }
  const shownStart = total ? (page - 1) * pageSize + 1 : 0
  const shownEnd = Math.min(page * pageSize, total)
  const selectedSchoolName = useMemo(() => filters.schools.find((item) => String(item.id) === schoolId)?.name, [filters.schools, schoolId])
  return <main className="mx-auto max-w-7xl space-y-4 px-3 py-4 sm:space-y-5 sm:px-6 sm:py-5 lg:px-8">
    <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white pb-5">
      <div className="mr-auto"><p className="text-xs font-semibold tracking-wider text-indigo-600">TARGET SYSTEM</p><h1 className="mt-1 text-2xl font-semibold">目标系统</h1><p className="mt-1 text-xs text-gray-500">按学校和科目同步、抢题并检查交付状态。</p></div>
      <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap">
        <select value={schoolId} onChange={(event) => { setSchoolId(event.target.value); setSubjectId(''); setPage(1) }} className="min-w-0 rounded border px-2 py-2 text-sm"><option value="">选择学校</option>{filters.schools.map((school) => <option key={school.id} value={school.id}>{school.name}</option>)}</select>
        <select value={subjectId} onChange={(event) => { setSubjectId(event.target.value); setPage(1) }} className="min-w-0 rounded border px-2 py-2 text-sm"><option value="">全部科目</option>{filters.subjects.map((subject) => <option key={subject.id} value={subject.id}>{subject.name}</option>)}</select>
        <button onClick={() => perform('sync', async () => { await api.post('/api/target-system/sync', { school_id: Number(schoolId), ...(subjectId ? { subject_id: Number(subjectId) } : {}) }); setMessage(`已开始同步${selectedSchoolName || '当前学校'}的题目`) })} disabled={Boolean(busy) || !schoolId} className="inline-flex items-center justify-center gap-2 rounded border px-3 py-2 text-sm hover:bg-gray-50 disabled:opacity-50"><RefreshCw className="h-4 w-4" />同步待接题</button>
        <button onClick={openAiResearch} disabled={Boolean(busy)} className="inline-flex items-center justify-center gap-2 rounded border border-indigo-200 px-3 py-2 text-sm text-indigo-700 hover:bg-indigo-50 disabled:opacity-50" title="在新 Chrome 页面中自动登录学解 AI Research"><ExternalLink className="h-4 w-4" />打开学解录入</button>
        <button onClick={() => { if (window.confirm(`确认抢题并启动 ${selected.length} 道题吗？`)) void perform('claim', async () => { await api.post('/api/target-system/tasks/select', { remote_task_ids: selected }); await api.post('/api/target-system/tasks/claim', { remote_task_ids: selected }); setSelected([]); setMessage('已确认抢题并开始解题') }) }} disabled={Boolean(busy) || selected.length === 0} className="inline-flex items-center justify-center gap-2 rounded bg-indigo-600 px-3 py-2 text-sm text-white disabled:opacity-50"><Download className="h-4 w-4" />确认抢题 ({selected.length})</button>
      </div>
    </header>

    {message && <div className="border rounded-lg bg-amber-50 text-amber-800 px-4 py-3 text-sm">{message}</div>}
    {syncInfo?.state === 'running' && <div className="border rounded-lg bg-blue-50 text-blue-800 px-4 py-3 text-sm">正在通过 API 分页读取：已扫描 {syncInfo.schools_done}/{syncInfo.schools_total || '…'} 所学校，发现 {syncInfo.synced} 题。</div>}
    {syncInfo?.state === 'failed' && <div className="border rounded-lg bg-red-50 text-red-700 px-4 py-3 text-sm">同步失败：{syncInfo.error || '请稍后重试'}</div>}
    <div className="flex overflow-x-auto border-y bg-white text-sm">{[['', '全部'], ['discovered', '待选择'], ['solving', '解题中'], ['review_pending', '已解答']].map(([value, label]) => <button key={value} onClick={() => { setStatusFilter(value); setPage(1) }} className={`shrink-0 border-r px-4 py-3 ${statusFilter === value ? 'bg-indigo-600 text-white' : 'hover:bg-slate-50'}`}>{label} {value ? statusCounts[value] || 0 : allTotal}</button>)}</div>

    <div>
      <div className="overflow-hidden rounded-lg border bg-white">
        <div className="flex items-center justify-between px-4 py-3 border-b"><span className="text-xs text-gray-500">显示 {shownStart}–{shownEnd} / {total} 题</span><span className="text-xs text-gray-400">图片和完整题干按需加载</span></div>
        <div className="hidden grid-cols-[40px_100px_1fr_110px_160px] gap-3 border-b px-4 py-3 text-xs font-medium text-gray-500 md:grid"><span>选</span><span>远端 ID</span><span>题目</span><span>工作流</span><span>状态与操作</span></div>
        {items.length === 0 && <div className="p-8 text-sm text-gray-500">当前筛选条件下尚无已同步题目。</div>}
        {items.map((item) => <div key={item.id} className="border-b last:border-0">
          <div className="grid grid-cols-[32px_minmax(0,1fr)] gap-x-3 gap-y-2 px-4 py-3 text-sm md:grid-cols-[40px_100px_1fr_110px_160px] md:items-start md:gap-3">
            <input type="checkbox" disabled={!['discovered', 'selected'].includes(item.status)} checked={selected.includes(item.remote_task_id)} onChange={() => toggle(item.remote_task_id)} className="row-span-4 mt-1 md:row-auto md:row-span-1 md:mt-0" />
            <span className="col-start-2 font-mono text-xs text-gray-500 md:col-auto">{item.remote_task_id}</span>
            <div className="col-start-2 min-w-0 md:col-auto"><p className="font-medium truncate">{item.title}</p><p className="text-xs text-gray-400">{item.school_name || '未指定学校'}{item.subject_name ? ` · ${item.subject_name}` : ''}</p><button onClick={() => void toggleDetail(item)} className="mt-1 inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline"><Eye className="w-3 h-3" />{expandedId === item.id ? '收起题目' : '查看题目'}</button>{item.error_message && <p className="text-xs text-red-600 truncate mt-1">{item.error_message}</p>}</div>
            <div className="col-start-2 md:col-auto">{item.workflow_task_id ? <button onClick={() => void openAnswerPreview(item.workflow_task_id!)} className="text-xs text-indigo-600 hover:underline">查看 AI 结果</button> : <span className="text-xs text-gray-400">未创建工作流</span>}</div>
            <div className="col-start-2 flex items-center justify-between gap-3 md:col-auto md:block md:space-y-1"><span className="block text-xs">{statusLabel[item.status] || item.status}</span>{!['discovered', 'selected', 'delivered'].includes(item.status) && <button onClick={() => returnToAll(item)} disabled={Boolean(busy)} className="text-left text-xs text-rose-600 hover:underline disabled:opacity-50">撤回到全部</button>}</div>
          </div>
          {expandedId === item.id && <div className="mx-4 mb-4 rounded border bg-slate-50 px-3 py-3 text-xs md:ml-[152px]">
            {loadingDetail === item.id && <span className="inline-flex items-center gap-1 text-gray-500"><LoaderCircle className="w-3 h-3 animate-spin" />正在加载题干和题图…</span>}
            {details[item.id] && <><p className="whitespace-pre-wrap leading-5 text-gray-700">{details[item.id].question_text || '接口未返回文字题干。'}</p>{details[item.id].image_urls?.length ? <div className="mt-3 flex flex-wrap gap-2">{details[item.id].image_urls!.map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer"><img src={url} loading="lazy" alt={`题图${index + 1}`} className="h-28 w-40 object-contain bg-white border rounded" /></a>)}</div> : null}{(details[item.id].rendered_answer_url || details[item.id].browser_screenshot_url) && <div className="mt-3 flex flex-wrap gap-3">{details[item.id].rendered_answer_url && <a href={details[item.id].rendered_answer_url || undefined} target="_blank" rel="noreferrer"><img src={details[item.id].rendered_answer_url || undefined} alt="KaTeX 渲染答案" className="h-28 w-40 object-contain bg-white border rounded" /></a>}{details[item.id].browser_screenshot_url && <a href={details[item.id].browser_screenshot_url || undefined} target="_blank" rel="noreferrer"><img src={details[item.id].browser_screenshot_url || undefined} alt="浏览器填写后网页" className="h-28 w-40 object-contain bg-white border rounded" /></a>}</div>}</>}
          </div>}
        </div>)}
        <div className="flex items-center justify-end gap-3 px-4 py-3 text-sm"><button onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page === 1} className="p-1 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button><span className="text-xs text-gray-500">第 {page}/{totalPages} 页</span><button onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={page >= totalPages} className="p-1 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button></div>
      </div>

    </div>
    {(previewLoading || previewTask || previewError) && <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/80 sm:items-center sm:p-6" onClick={() => { setPreviewTask(null); setPreviewError(''); setPreviewLoading(false) }}>
      <article className="relative max-h-[92svh] w-full max-w-5xl overflow-y-auto rounded-t-lg bg-white p-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] shadow-2xl sm:max-h-[90vh] sm:rounded-none sm:p-8" onClick={(event) => event.stopPropagation()}>
        <button type="button" onClick={() => { setPreviewTask(null); setPreviewError(''); setPreviewLoading(false) }} className="absolute right-3 top-3 p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-900" aria-label="关闭 AI 结果预览" title="关闭"><X className="h-5 w-5" /></button>
        {previewLoading && <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-gray-500"><LoaderCircle className="h-5 w-5 animate-spin" />正在加载 AI 结果…</div>}
        {previewError && <div className="min-h-48 py-10 text-sm text-red-600">{previewError}</div>}
        {previewTask && <div className="prose prose-slate max-w-none break-words"><ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>{previewTask.answer_preview || previewTask.final_result || ''}</ReactMarkdown></div>}
      </article>
    </div>}
  </main>
}
