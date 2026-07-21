import axios from 'axios'
import { Download, FileCheck2, Loader2, RefreshCw, Trash2, Upload, X } from 'lucide-react'
import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'

const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || '' })
const ACTIVE_ERRATA_JOB_STORAGE_KEY = 'zyb.active_errata_job_id'

type ErrataItem = {
  item_id: string; task_id?: string; item_index: number; source_ref: string; question_text: string
  original_answer: string; correction_opinion: string; existing_content: string
  evidence: string[]; status: string; result_type?: string; final_text_markup: string
  material_paths: string[]; material_text: string; material_version: number; has_material_packet: boolean
  warnings: string[]; replace_existing: boolean
  mineru_text: string; review_status: string; review_feedback: string
  solution_text: string; standard_answer_verdict: string; question_verdict: string
  original_answer_verdict: string; correction_opinion_verdict: string
  errata_opinion: string; question_errata: string
  human_confirmed?: boolean
}
type ErrataJobSummary = { job_id: string; original_filename: string; state: string; item_count: number }

const taskStatusLabel: Record<string, string> = {
  queued: '排队中', solving: '解题中', reviewing: '复核或裁决中', formatting: '排版或组合中',
  completed: '已完成', failed: '失败', paused: '已暂停', manual: '待处理', insufficient_evidence: '证据不足',
}

export default function ErrataWorkbench({ focusItemId }: { focusItemId?: string | null }) {
  const [jobId, setJobId] = useState(() => localStorage.getItem(ACTIVE_ERRATA_JOB_STORAGE_KEY) || '')
  const [file, setFile] = useState<File | null>(null)
  const [customAnchors, setCustomAnchors] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [items, setItems] = useState<ErrataItem[]>([])
  const [mineruStatus, setMineruStatus] = useState('not_requested')
  const [selectedId, setSelectedId] = useState(() => localStorage.getItem('zyb.active_errata_item_id') || '')
  const [busy, setBusy] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [message, setMessage] = useState('')
  const [jobError, setJobError] = useState('')
  const [recentJobs, setRecentJobs] = useState<ErrataJobSummary[]>([])
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'failed'>('saved')
  const [dirtyItemId, setDirtyItemId] = useState('')
  const [previewEvidenceUrl, setPreviewEvidenceUrl] = useState('')
  const evidenceInputRef = useRef<HTMLInputElement | null>(null)
  const selected = useMemo(() => items.find((item) => item.item_id === selectedId) || items[0], [items, selectedId])
  const visibleItems = useMemo(() => items.filter((item) => (
    (statusFilter === 'all' || item.status === statusFilter)
    && `${item.source_ref} ${item.question_text}`.toLowerCase().includes(search.toLowerCase())
  )), [items, search, statusFilter])

  const refresh = async (id = jobId) => {
    if (!id) return
    const [{ data }, { data: job }] = await Promise.all([api.get(`/api/errata/jobs/${id}/items`), api.get(`/api/errata/jobs/${id}`)])
    setItems(data.items)
    setMineruStatus(job.mineru_status || 'not_requested')
    setJobError(job.error_msg || '')
    if (!selectedId && data.items[0]) setSelectedId(data.items[0].item_id)
  }

  const loadRecentJobs = async () => {
    const { data } = await api.get('/api/errata/jobs')
    setRecentJobs(data.items || [])
  }

  useEffect(() => { void loadRecentJobs().catch(() => undefined) }, [])

  useEffect(() => {
    if (!jobId) return
    localStorage.setItem(ACTIVE_ERRATA_JOB_STORAGE_KEY, jobId)
    void refresh(jobId).catch((error) => {
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        localStorage.removeItem(ACTIVE_ERRATA_JOB_STORAGE_KEY)
        setJobId('')
      }
      setMessage('无法恢复该勘误任务')
    })
  }, [jobId])

  useEffect(() => {
    if (focusItemId) setSelectedId(focusItemId)
  }, [focusItemId])

  useEffect(() => {
    if (selectedId) localStorage.setItem('zyb.active_errata_item_id', selectedId)
  }, [selectedId])

  useEffect(() => {
    if (!jobId || !items.some((item) => ['queued', 'solving', 'reviewing', 'formatting'].includes(item.status)) && mineruStatus !== 'parsing') return
    const timer = window.setInterval(() => void refresh(), 1800)
    return () => window.clearInterval(timer)
  }, [jobId, items, mineruStatus])

  useEffect(() => {
    if (!previewEvidenceUrl) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreviewEvidenceUrl('')
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [previewEvidenceUrl])

  const upload = async () => {
    if (!file) return
    setBusy(true); setMessage('正在拆分题块并提取页面证据…')
    try {
      const form = new FormData(); form.append('file', file); form.append('custom_anchors', customAnchors)
      const { data } = await api.post('/api/errata/jobs', form)
      setJobId(data.job_id); setSelectedId(''); await refresh(data.job_id)
      await loadRecentJobs()
      setMessage(`已识别 ${data.item_count} 个勘误题块`)
    } catch (error) { setMessage(axios.isAxiosError(error) ? error.response?.data?.detail || error.message : '上传失败') }
    finally { setBusy(false) }
  }

  const generateAll = async () => {
    setBusy(true); setMessage('已开始批量生成，可逐题查看结果')
    try { await api.post(`/api/errata/jobs/${jobId}/generate`, {}); await refresh() }
    finally { setBusy(false) }
  }

  const rebuildMaterials = async () => {
    if (!jobId || !window.confirm('将从原 Word 重建所有题块材料包，并使现有结果失效后重新审查。继续吗？')) return
    try {
      await api.post(`/api/errata/jobs/${jobId}/rebuild-materials`)
      await refresh()
      setMessage('已重建原始材料包，请重新运行勘误工作流')
    } catch (error) { setMessage(axios.isAxiosError(error) ? error.response?.data?.detail || '重建材料包失败' : '重建材料包失败') }
  }

  const patchItem = (patch: Partial<ErrataItem>) => {
    if (!selected) return
    setItems((current) => current.map((item) => item.item_id === selected.item_id ? { ...item, ...patch } : item))
    setDirtyItemId(selected.item_id)
  }

  const saveDraft = async (item = selected) => {
    if (!item) return false
    setSaveState('saving')
    try {
      const payload = { source_ref: item.source_ref, question_text: item.question_text, original_answer: item.original_answer, correction_opinion: item.correction_opinion, existing_content: item.existing_content, solution_text: item.solution_text, replace_existing: item.replace_existing }
      const { data } = await api.patch(`/api/errata/items/${item.item_id}`, payload)
      setItems((current) => current.map((currentItem) => currentItem.item_id === data.item_id ? data : currentItem))
      setDirtyItemId((current) => current === item.item_id ? '' : current)
      setSaveState('saved')
      return true
    } catch (error) {
      setSaveState('failed')
      setMessage(axios.isAxiosError(error) ? error.response?.data?.detail || error.message : '自动保存失败')
      return false
    }
  }

  useEffect(() => {
    if (!selected || dirtyItemId !== selected.item_id) return
    const timer = window.setTimeout(() => { void saveDraft(selected) }, 1000)
    return () => window.clearTimeout(timer)
  }, [dirtyItemId, selected])

  const save = async (confirm = false) => {
    if (!selected) return
    try {
      if (!(await saveDraft(selected))) return
      if (!confirm) { setMessage('已保存编辑'); return }
      const { data } = await api.patch(`/api/errata/items/${selected.item_id}`, { status: 'confirmed' })
      setItems((current) => current.map((item) => item.item_id === data.item_id ? data : item))
      setMessage('已标记为人工检查；Task 完成状态仍由工作流决定')
    } catch (error) { setMessage(axios.isAxiosError(error) ? error.response?.data?.detail || error.message : '保存失败') }
  }

  const regenerate = async () => {
    if (!selected?.task_id) return
    await api.post(`/api/tasks/${selected.task_id}/run`, { start_node: 'solver', target_nodes: ['solver', 'reviewer', 'formatter', 'errata_adjudication', 'word_composition'] })
    patchItem({ status: 'queued' }); setMessage('正在重新运行当前勘误工作流')
  }

  const review = async () => {
    if (!selected?.task_id) return
    await api.post(`/api/tasks/${selected.task_id}/run`, { start_node: 'errata_adjudication', target_nodes: ['errata_adjudication', 'word_composition'] })
    patchItem({ status: 'queued', review_status: 'reviewing', review_feedback: '' }); setMessage('正在从勘误裁决节点继续')
  }

  const exportDocx = async () => {
    const pendingItems = items.filter((item) => item.status !== 'completed' || !item.human_confirmed)
    if (pendingItems.length > 0) { setMessage(`仍有 ${pendingItems.length} 题未完成或未人工确认，不能导出`); return }
    setExporting(true)
    setMessage('正在生成 Word，请稍候…')
    try {
      const response = await api.post(`/api/errata/jobs/${jobId}/export`, {}, { responseType: 'blob' })
      const url = URL.createObjectURL(response.data); const anchor = document.createElement('a')
      anchor.href = url; anchor.download = '勘误_已处理.docx'; anchor.click(); URL.revokeObjectURL(url)
      setMessage('Word 已生成，下载已开始')
    } catch (error) {
      let detail = '导出失败'
      if (axios.isAxiosError(error)) {
        const data = error.response?.data
        if (data instanceof Blob) {
          try { detail = JSON.parse(await data.text()).detail || detail } catch { /* 响应不是 JSON */ }
        } else detail = data?.detail || error.message || detail
      }
      setMessage(detail)
    } finally { setExporting(false) }
  }

  const addManualItem = async () => {
    const { data } = await api.post(`/api/errata/jobs/${jobId}/items`, { question_text: '请填写题干' })
    setItems((current) => [...current, data]); setSelectedId(data.item_id); setMessage('已新增手动题目，请编辑后保存')
  }

  const addEvidence = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !selected) return
    const form = new FormData(); form.append('file', file)
    try {
      const { data } = await api.post(`/api/errata/items/${selected.item_id}/evidence`, form)
      setItems((current) => current.map((item) => item.item_id === data.item_id ? data : item))
      setMessage('图片证据已追加，重新生成时会一并发送给模型')
    } catch (error) { setMessage(axios.isAxiosError(error) ? error.response?.data?.detail || '添加图片失败' : '添加图片失败') }
    finally { event.target.value = '' }
  }

  const selectItem = async (itemId: string) => {
    if (dirtyItemId && selected) await saveDraft(selected)
    setSelectedId(itemId)
  }

  const operateTask = async (action: 'start' | 'pause' | 'terminate') => {
    if (!selected?.task_id) return
    if (action === 'start') await api.post(`/api/tasks/${selected.task_id}/run`, { start_node: 'solver', target_nodes: ['solver', 'reviewer', 'formatter', 'errata_adjudication', 'word_composition'] })
    else await api.post(`/api/tasks/${selected.task_id}/operation`, { action })
    setMessage(action === 'start' ? '已启动勘误任务' : `已${action === 'pause' ? '暂停' : '终止'}勘误任务`)
  }

  const deleteSelectedItem = async () => {
    if (!selected) return
    const confirmed = window.confirm(`永久删除“${selected.source_ref || `题块 ${selected.item_index}`}”吗？该题的主任务、日志和历史产物会一并删除，无法恢复。`)
    if (!confirmed) return
    try {
      await api.delete(`/api/errata/items/${selected.item_id}`)
      const remaining = items.filter((item) => item.item_id !== selected.item_id)
      setItems(remaining)
      setSelectedId(remaining[0]?.item_id || '')
      setDirtyItemId('')
      setMessage('已永久删除该勘误题')
      void loadRecentJobs()
    } catch (error) {
      setMessage(axios.isAxiosError(error) ? error.response?.data?.detail || '删除勘误题失败' : '删除勘误题失败')
    }
  }

  const showJobList = () => {
    localStorage.removeItem(ACTIVE_ERRATA_JOB_STORAGE_KEY)
    localStorage.removeItem('zyb.active_errata_item_id')
    setJobId(''); setItems([]); setSelectedId(''); setJobError('')
    void loadRecentJobs()
  }

  if (!jobId) return (
    <main className="min-h-[70vh] grid place-items-center px-6">
      <section className="w-full max-w-2xl border-t-4 border-indigo-600 pt-10">
        <p className="text-sm font-semibold tracking-widest text-indigo-600">ERRATA WORKBENCH</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight text-slate-950">勘误工作台</h1>
        <p className="mt-3 text-slate-500">上传 Word 副本，按锚点拆题；各题进入统一 Task 工作流，完成后可直接合并新文件。</p>
        <div onDragOver={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={() => setIsDragging(false)} onDrop={(event) => { event.preventDefault(); setIsDragging(false); setFile(event.dataTransfer.files[0] || null) }} className={`mt-10 border-y py-6 transition ${isDragging ? 'border-indigo-500 bg-indigo-50' : 'border-slate-200'}`}>
          <div className="flex items-center gap-3"><input type="file" accept=".docx" onChange={(event) => setFile(event.target.files?.[0] || null)} className="min-w-0 flex-1 text-sm" />
          <button onClick={upload} disabled={!file || busy} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-40">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} 导入并拆分
          </button></div>
          <p className="mt-3 text-xs text-slate-500">也可将 .docx 文件直接拖到这里。默认会识别“勘误处理建议/应该为：”“应该为：”“改为：”及其半角冒号写法。</p>
          <label className="mt-4 block text-xs font-semibold uppercase tracking-wider text-slate-400">追加自定义锚点（每行或逗号分隔）</label>
          <input value={customAnchors} onChange={(event) => setCustomAnchors(event.target.value)} placeholder="例如：处理意见：, 订正为：" className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500" />
        </div>
        {recentJobs.length > 0 && <section className="mt-8 border-t border-slate-200 pt-5"><h2 className="text-sm font-semibold text-slate-800">继续处理</h2><div className="mt-3 divide-y divide-slate-200">{recentJobs.map((job) => <button key={job.job_id} onClick={() => { setSelectedId(''); setJobId(job.job_id) }} className="flex w-full items-center justify-between py-3 text-left hover:text-indigo-700"><span className="min-w-0 truncate text-sm">{job.original_filename}</span><span className="ml-4 shrink-0 text-xs text-slate-500">{job.item_count} 题 · {job.state}</span></button>)}</div></section>}
        {message && <p className="mt-4 text-sm text-rose-600">{message}</p>}
      </section>
    </main>
  )

  return (
    <main className="grid min-h-[calc(100vh-64px)] grid-cols-[300px_minmax(0,1fr)] bg-white">
      <aside className="border-r border-slate-200 bg-slate-50/70">
        <div className="border-b border-slate-200 p-5">
          <div className="flex items-center justify-between"><h1 className="font-semibold text-slate-950">勘误题目</h1><span className="text-xs text-slate-500">{items.filter((i) => i.status === 'completed').length}/{items.length} 已完成</span></div>
          <button onClick={generateAll} disabled={!items.length || busy} className="mt-4 w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-40">批量生成并审查</button>
          <button onClick={addManualItem} className="mt-3 w-full rounded-lg border border-indigo-300 px-3 py-2 text-sm font-medium text-indigo-700">新增手动题目</button>
          <button onClick={() => void rebuildMaterials()} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700">重建原始材料包</button>
          <p className="mt-3 text-xs text-slate-500">MinerU：{mineruStatus}</p>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索题号或题干" className="mt-4 w-full rounded border border-slate-300 px-2 py-1.5 text-xs" />
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="mt-2 w-full rounded border border-slate-300 px-2 py-1.5 text-xs"><option value="all">全部状态</option><option value="manual">待处理</option><option value="queued">排队中</option><option value="solving">生成中</option><option value="reviewing">审查中</option><option value="formatting">排版中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="paused">已暂停</option></select>
          <button onClick={showJobList} className="mt-4 text-xs font-medium text-indigo-700 hover:text-indigo-900">切换任务或重新导入</button>
        </div>
        <div className="max-h-[calc(100vh-180px)] overflow-y-auto">
          {visibleItems.map((item) => <button key={item.item_id} onClick={() => void selectItem(item.item_id)} className={`w-full border-b border-slate-200 px-5 py-4 text-left transition ${selected?.item_id === item.item_id ? 'bg-white shadow-[inset_3px_0_0_#4f46e5]' : 'hover:bg-white'}`}>
            <div className="flex items-center justify-between"><span className="text-sm font-semibold">题块 {item.item_index} {item.material_paths.length > 0 ? '· 原始材料' : ''}</span><span className={`text-xs ${item.status === 'completed' ? 'text-emerald-600' : item.status === 'failed' ? 'text-rose-600' : 'text-slate-400'}`}>{taskStatusLabel[item.status] || item.status}{item.human_confirmed ? ' · 已检查' : ''}</span></div>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{item.question_text || '未提取到题干文字'}</p>
          </button>)}
        </div>
      </aside>
      {!selected && <section className="grid place-items-center px-8 py-7"><div className="max-w-md border-l-2 border-amber-400 pl-5"><h2 className="text-lg font-semibold text-slate-900">当前任务没有可处理题目</h2><p className="mt-2 text-sm leading-6 text-slate-600">{jobError || '请返回项目列表，选择其他任务或重新导入文件。'}</p><button onClick={showJobList} className="mt-5 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white">返回项目列表</button></div></section>}
      {selected && <section className="min-w-0 px-8 py-7">
        <header className="flex items-start justify-between gap-6 border-b border-slate-200 pb-5">
          <div><p className="text-xs font-medium tracking-wider text-indigo-600">{selected.source_ref || `题块 ${selected.item_index}`}</p><h2 className="mt-1 text-2xl font-semibold">复核并填写锚点内容</h2><p className="mt-2 text-xs text-slate-500">主任务：{selected.task_id || '创建中'} · 勘误：{taskStatusLabel[selected.status] || selected.status} · 审查：{selected.review_status} · {saveState === 'saving' ? '保存中…' : saveState === 'failed' ? '保存失败，可重试' : '已保存'}</p></div>
          <div className="min-w-0"><div className="flex flex-wrap justify-end gap-2"><button onClick={showJobList} className="rounded-lg border border-slate-300 px-3 py-2 text-sm">切换任务</button><button onClick={() => operateTask('start')} className="rounded-lg border border-emerald-300 px-3 py-2 text-sm text-emerald-700">开始</button><button onClick={() => operateTask('pause')} className="rounded-lg border border-amber-300 px-3 py-2 text-sm text-amber-700">暂停</button><button onClick={() => operateTask('terminate')} className="rounded-lg border border-rose-300 px-3 py-2 text-sm text-rose-700">终止</button><button onClick={regenerate} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm"><RefreshCw className="h-4 w-4" />重新生成</button><button onClick={exportDocx} disabled={exporting} className="inline-flex min-w-28 items-center justify-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-50">{exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}{exporting ? '正在导出' : '导出 Word'}</button><button onClick={() => void deleteSelectedItem()} className="inline-flex items-center gap-2 rounded-lg border border-rose-300 px-3 py-2 text-sm text-rose-700"><Trash2 className="h-4 w-4" />删除本题</button></div>{message && <p role="status" aria-live="polite" className="mt-2 text-right text-xs text-slate-600">{message}</p>}</div>
        </header>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(360px,0.8fr)] gap-8 pt-6">
          <div className="space-y-7">
            <section><div className="mb-3 flex items-center justify-between"><h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">发送给勘误模型的原始材料</h3><input ref={evidenceInputRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={addEvidence} className="hidden" /><button onClick={() => evidenceInputRef.current?.click()} className="text-xs font-medium text-indigo-700">追加人工图片</button></div><pre className="max-h-72 overflow-auto whitespace-pre-wrap border border-slate-200 bg-slate-50 p-4 text-sm leading-7 text-slate-700">{selected.material_text || '（该题没有可提取文本，以下仅展示原始页面材料。）'}</pre>{selected.material_paths.length > 0 ? <div className="mt-4 space-y-4">{selected.material_paths.map((path, index) => { const url = `/api/errata/jobs/${jobId}/evidence/${encodeURI(path)}`; return <figure key={path} className="border border-slate-200"><figcaption className="border-b border-slate-200 px-3 py-2 text-xs text-slate-500">原始材料第 {index + 1} 页</figcaption><button onClick={() => setPreviewEvidenceUrl(url)} title="放大查看原始材料" className="block w-full"><img src={url} className="w-full" /></button></figure> })}</div> : <p className="mt-3 text-sm text-amber-700">尚未生成原始材料包，请使用“重建原始材料包”。</p>}</section>
          </div>
          <div className="sticky top-6 h-fit border-l border-slate-200 pl-8">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">完整解题结果</label>
            <textarea value={selected.solution_text} onChange={(event) => patchItem({ solution_text: event.target.value, final_text_markup: '', result_type: undefined })} rows={14} className="mt-2 w-full rounded-lg border border-slate-300 p-3 font-mono text-sm leading-6 outline-none focus:border-indigo-500" placeholder="完成解题与排版后显示完整正解。" />
            <label className="mt-5 block text-xs font-semibold uppercase tracking-wider text-slate-400">勘误裁决</label>
            <p className="mt-2 text-sm leading-6 text-slate-700">标准答案：{selected.standard_answer_verdict || '待裁决'}；题干：{selected.question_verdict || '待裁决'}；原答案：{selected.original_answer_verdict || '待裁决'}；勘误意见：{selected.correction_opinion_verdict || '待裁决'}</p>
            {selected.question_errata && <p className="mt-2 text-sm leading-6 text-amber-800">题干勘误：{selected.question_errata}</p>}
            <label className="mt-3 block text-xs font-semibold uppercase tracking-wider text-slate-400">勘误意见</label>
            <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-700">{selected.errata_opinion || '待裁决'}</pre>
            <p className="mt-4 text-xs text-slate-500">处理类型：{selected.result_type || '待服务端派生'}</p>
            <label className="mt-5 block text-xs font-semibold uppercase tracking-wider text-slate-400">最终写入文本</label>
            <pre className="mt-2 max-h-96 min-h-40 overflow-auto whitespace-pre-wrap border border-slate-200 bg-slate-50 p-3 font-mono text-sm leading-6 text-slate-700">{selected.final_text_markup || '人工编辑 Formatter 后需重新裁决，裁决通过后生成预览。'}</pre>
            {selected.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-amber-700">⚠ {warning}</p>)}
            <div className={`mt-3 text-xs ${selected.review_status === 'passed' ? 'text-emerald-700' : 'text-amber-700'}`}>审查：{selected.review_status}{selected.review_feedback ? ` — ${selected.review_feedback}` : ''}</div>
            <div className="mt-5 flex gap-2"><button onClick={() => save(false)} className="flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium">立即保存</button><button onClick={review} disabled={!selected.task_id} className="flex-1 rounded-lg border border-indigo-300 px-4 py-2.5 text-sm font-medium text-indigo-700 disabled:opacity-40">从审查节点继续</button><button onClick={() => save(true)} disabled={saveState !== 'saved' || selected.status !== 'completed'} className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40"><FileCheck2 className="h-4 w-4" />标记已检查</button></div>
          </div>
        </div>
      </section>}
      {previewEvidenceUrl && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6" onClick={() => setPreviewEvidenceUrl('')} role="dialog" aria-modal="true" aria-label="图片预览"><button onClick={() => setPreviewEvidenceUrl('')} className="absolute right-5 top-5 rounded border border-white/40 p-2 text-white hover:bg-white/15" title="关闭图片预览"><X className="h-6 w-6" /></button><img src={previewEvidenceUrl} alt="勘误证据原图" className="max-h-full max-w-full object-contain" onClick={(event) => event.stopPropagation()} /></div>}
    </main>
  )
}
