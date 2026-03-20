import { useState, ClipboardEvent, useEffect, useMemo } from 'react'
import { QueryClient, QueryClientProvider, useQuery, useMutation, useQueries } from '@tanstack/react-query'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { X, Image as ImageIcon, Play, Plus, Maximize2, Settings, Database, Save, Trash2, Download } from 'lucide-react'
import 'katex/dist/katex.min.css'

const queryClient = new QueryClient()

const api = axios.create({
  baseURL: 'http://localhost:8000',
})

const RUNNING_TASK_STATES = ['queued', 'solving', 'reviewing', 'formatting']
const EXCEPTION_TASK_STATES = ['failed', 'manual', 'cancelled']
const SUBMITTED_TASKS_STORAGE_KEY = 'submitted_tasks'
const ACTIVE_TASK_ID_STORAGE_KEY = 'active_task_id'
const SOLVER_CONFIG_STORAGE_KEY = 'solver_config'
const REVIEWER_CONFIG_STORAGE_KEY = 'reviewer_config'
const FORMATTER_CONFIG_STORAGE_KEY = 'formatter_config'

const getErrorMessage = (error: unknown, fallback: string) => {
  if (axios.isAxiosError(error)) {
    return (error.response?.data as { detail?: string } | undefined)?.detail || error.message || fallback
  }
  if (error instanceof Error) return error.message
  return fallback
}

// --- Types ---
interface PendingTask {
  id: string;
  imageUrl: string;
}

interface SubmittedTask {
  taskId: string;
}

interface ModelConfig {
  model_name: string;
  api_key: string;
  base_url: string;
  max_tokens: number;
}

interface AdminTask {
  task_id: string;
  thread_id: string;
  image_url: string;
  state: string;
  retry_count: number;
  history?: string | null;
  final_result?: string | null;
  token_usage?: string | null;
  error_code?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  manual_operator?: string | null;
}

interface AdminTaskListResponse {
  total: number;
  page: number;
  page_size: number;
  items: AdminTask[];
}

interface AdminLogItem {
  id: number;
  task_id: string;
  node_name: string;
  request_payload?: string | null;
  response_payload?: string | null;
  cost_tokens: number;
  created_at?: string | null;
}

interface AdminLogListResponse {
  total: number;
  items: AdminLogItem[];
}

interface RetryModelConfigs {
  solver_config: ModelConfig;
  reviewer_config: ModelConfig;
  formatter_config: ModelConfig;
}

const readStoredJson = <T,>(key: string, fallback: T): T => {
  const raw = localStorage.getItem(key)
  if (!raw) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

const getLatestRetryConfigs = (): RetryModelConfigs => ({
  solver_config: readStoredJson<ModelConfig>(SOLVER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 4096 }),
  reviewer_config: readStoredJson<ModelConfig>(REVIEWER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 2048 }),
  formatter_config: readStoredJson<ModelConfig>(FORMATTER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 1024 })
})

const persistTaskForDashboard = (taskId: string) => {
  const submittedTasks = readStoredJson<SubmittedTask[]>(SUBMITTED_TASKS_STORAGE_KEY, [])
  const normalized = Array.isArray(submittedTasks)
    ? submittedTasks.filter((item): item is SubmittedTask => (
      typeof item === 'object'
      && item !== null
      && typeof item.taskId === 'string'
      && item.taskId.trim().length > 0
    ))
    : []
  if (!normalized.some((item) => item.taskId === taskId)) {
    normalized.push({ taskId })
  }
  localStorage.setItem(SUBMITTED_TASKS_STORAGE_KEY, JSON.stringify(normalized))
  localStorage.setItem(ACTIVE_TASK_ID_STORAGE_KEY, taskId)
}

// --- Components ---

function TaskDashboard({ onOpenAdmin }: { onOpenAdmin: () => void }) {
  const [pendingQueue, setPendingQueue] = useState<PendingTask[]>([])
  const [submittedTasks, setSubmittedTasks] = useState<SubmittedTask[]>(() => {
    const saved = readStoredJson<SubmittedTask[]>(SUBMITTED_TASKS_STORAGE_KEY, [])
    if (!Array.isArray(saved)) return []
    return saved.filter((item): item is SubmittedTask => (
      typeof item === 'object'
      && item !== null
      && typeof item.taskId === 'string'
      && item.taskId.trim().length > 0
    ))
  })
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  const [activeTaskId, setActiveTaskId] = useState<string | null>(() => {
    const saved = localStorage.getItem(ACTIVE_TASK_ID_STORAGE_KEY)
    if (!saved) return null
    const taskId = saved.trim()
    return taskId.length > 0 ? taskId : null
  })
  const [showSettings, setShowSettings] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [taskFilter, setTaskFilter] = useState<'all' | 'running' | 'completed' | 'exception'>('all')
  const [solverConfig, setSolverConfig] = useState<ModelConfig>(() => {
    const saved = localStorage.getItem(SOLVER_CONFIG_STORAGE_KEY)
    return saved ? JSON.parse(saved) : { model_name: '', api_key: '', base_url: '', max_tokens: 4096 }
  })
  const [reviewerConfig, setReviewerConfig] = useState<ModelConfig>(() => {
    const saved = localStorage.getItem(REVIEWER_CONFIG_STORAGE_KEY)
    return saved ? JSON.parse(saved) : { model_name: '', api_key: '', base_url: '', max_tokens: 2048 }
  })
  const [formatterConfig, setFormatterConfig] = useState<ModelConfig>(() => {
    const saved = localStorage.getItem(FORMATTER_CONFIG_STORAGE_KEY)
    return saved ? JSON.parse(saved) : { model_name: '', api_key: '', base_url: '', max_tokens: 1024 }
  })

  // 保存设置到 localStorage
  const saveSettings = () => {
    localStorage.setItem(SOLVER_CONFIG_STORAGE_KEY, JSON.stringify(solverConfig))
    localStorage.setItem(REVIEWER_CONFIG_STORAGE_KEY, JSON.stringify(reviewerConfig))
    localStorage.setItem(FORMATTER_CONFIG_STORAGE_KEY, JSON.stringify(formatterConfig))
    setShowSettings(false)
  }

  // 处理剪贴板粘贴图片
  const handlePaste = (e: ClipboardEvent<HTMLDivElement>) => {
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image') !== -1) {
        const file = items[i].getAsFile();
        if (!file) continue;
        
        // 将文件转为 Base64 URL（为了纯本地演示，不依赖图床）
        // 生产环境应先传给后端的 /upload 接口换取真实 URL
        const reader = new FileReader();
        reader.onload = (event) => {
          if (event.target?.result) {
            setPendingQueue(prev => [...prev, {
              id: Math.random().toString(36).substring(7),
              imageUrl: event.target!.result as string
            }]);
          }
        };
        reader.readAsDataURL(file);
      }
    }
  };

  // 创建任务的 Mutation
  const createMutation = useMutation({
    mutationFn: (url: string) => api.post('/api/tasks', { 
      image_url: url,
      solver_config: solverConfig,
      reviewer_config: reviewerConfig,
      formatter_config: formatterConfig
    }).then(res => res.data),
  })
  const submittedTaskStatusQueries = useQueries({
    queries: submittedTasks.map((task) => ({
      queryKey: ['task-status', task.taskId],
      queryFn: () => api.get<AdminTask>(`/api/tasks/${task.taskId}`).then((res) => res.data),
      refetchInterval: (query: { state: { data?: AdminTask } }) => {
        const state = query.state.data?.state;
        if (state === 'completed' || state === 'failed' || state === 'manual' || state === 'cancelled') return false;
        return 2000;
      },
    })),
  })
  const submittedTaskItems = useMemo(
    () =>
      submittedTasks.map((task, index) => {
        const taskData = submittedTaskStatusQueries[index]?.data as AdminTask | undefined
        return {
          taskId: task.taskId,
          state: taskData?.state || 'queued'
        }
      }),
    [submittedTasks, submittedTaskStatusQueries]
  )
  const isRunningState = (state: string) => RUNNING_TASK_STATES.includes(state)
  const runningCount = submittedTaskItems.filter((task) => isRunningState(task.state)).length
  const completedCount = submittedTaskItems.filter((task) => task.state === 'completed').length
  const exceptionCount = submittedTaskItems.filter((task) => EXCEPTION_TASK_STATES.includes(task.state)).length
  const filteredTaskItems = submittedTaskItems.filter((task) => {
    if (taskFilter === 'running') return isRunningState(task.state)
    if (taskFilter === 'completed') return task.state === 'completed'
    if (taskFilter === 'exception') return EXCEPTION_TASK_STATES.includes(task.state)
    return true
  })
  const runningItems = filteredTaskItems.filter((task) => isRunningState(task.state))
  const otherItems = filteredTaskItems.filter((task) => !isRunningState(task.state))

  useEffect(() => {
    localStorage.setItem(SUBMITTED_TASKS_STORAGE_KEY, JSON.stringify(submittedTasks))
  }, [submittedTasks])

  useEffect(() => {
    if (activeTaskId) {
      localStorage.setItem(ACTIVE_TASK_ID_STORAGE_KEY, activeTaskId)
      return
    }
    localStorage.removeItem(ACTIVE_TASK_ID_STORAGE_KEY)
  }, [activeTaskId])

  useEffect(() => {
    if (submittedTasks.length === 0) {
      if (activeTaskId !== null) {
        setActiveTaskId(null)
      }
      return
    }
    if (activeTaskId && submittedTasks.some((task) => task.taskId === activeTaskId)) {
      return
    }
    setActiveTaskId(submittedTasks[submittedTasks.length - 1].taskId)
  }, [submittedTasks, activeTaskId])

  // 处理“开始处理”逻辑
  const handleStartProcessing = async () => {
    if (pendingQueue.length === 0) return;
    setErrorMessage(null);
    
    // 复制一份当前队列，然后清空 UI 队列
    const tasksToProcess = [...pendingQueue];
    setPendingQueue([]);
    
    console.log("🚀 开始提交任务队列，共", tasksToProcess.length, "个任务");

    // 逐个提交给后端
    for (const task of tasksToProcess) {
      try {
        console.log(`正在提交任务 (ID: ${task.id})...`);
        const result = await createMutation.mutateAsync(task.imageUrl);
        console.log(`✅ 任务提交成功，后端返回 Task ID: ${result.task_id}`);
        setSubmittedTasks((prev) => (
          prev.some((item) => item.taskId === result.task_id)
            ? prev
            : [...prev, { taskId: result.task_id }]
        ));
        // 将最后一个任务设为当前活跃视图
        setActiveTaskId(result.task_id);
      } catch (error: unknown) {
        console.error("❌ 提交任务失败:", error);
        const errorMsg = getErrorMessage(error, "未知错误");
        setErrorMessage(`提交失败: ${errorMsg}`);
        // 如果提交失败，把没提交的放回队列（可选策略）
        // 这里为了体验，只提示错误
      }
    }
  };

  const removePendingTask = (id: string) => {
    setPendingQueue(prev => prev.filter(t => t.id !== id));
  };

  const parseMaxTokens = (val: string) => {
    const parsed = parseInt(val, 10);
    return isNaN(parsed) ? 0 : parsed;
  };

  return (
    <div className="max-w-7xl mx-auto p-8 space-y-8" onPaste={handlePaste}>
      {/* 错误提示框 */}
      {errorMessage && (
        <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded shadow-sm flex justify-between items-start">
          <div className="text-red-700">
            <p className="font-bold">发生错误</p>
            <p className="text-sm">{errorMessage}</p>
          </div>
          <button onClick={() => setErrorMessage(null)} className="text-red-500 hover:text-red-700"><X size={18}/></button>
        </div>
      )}

      {/* 顶部标题与说明 */}
      <header className="border-b pb-4 flex justify-between items-start">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Zyb-Agent 生产流水线</h1>
          <p className="text-sm text-gray-500 mt-2">提示: 直接在这个页面 <kbd className="bg-gray-100 px-1 rounded border">Ctrl+V</kbd> 粘贴图片即可添加到队列。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onOpenAdmin}
            className="p-2 text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 rounded-full transition-colors"
            title="后台管理"
          >
            <Database size={24} />
          </button>
          <button 
            onClick={() => setShowSettings(true)}
            className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-full transition-colors"
            title="模型配置"
          >
            <Settings size={24} />
          </button>
        </div>
      </header>

      {/* 待处理队列区域 */}
      <div className="bg-white p-6 rounded-xl shadow-sm border space-y-4">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <ImageIcon size={20} className="text-blue-500" />
            待处理队列 ({pendingQueue.length})
          </h2>
          <button 
            onClick={handleStartProcessing}
            disabled={pendingQueue.length === 0 || createMutation.isPending}
            className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg font-medium disabled:opacity-50 hover:bg-blue-700 transition-colors"
          >
            <Play size={18} />
            {createMutation.isPending ? '正在提交...' : '开始处理'}
          </button>
        </div>

        {/* 队列缩略图展示 */}
        {pendingQueue.length > 0 ? (
          <div className="flex gap-4 overflow-x-auto pb-4">
            {pendingQueue.map(task => (
              <div key={task.id} className="relative group w-32 h-32 flex-shrink-0 border rounded-lg overflow-hidden bg-gray-50">
                <img src={task.imageUrl} alt="pending" className="w-full h-full object-cover" />
                {/* 悬浮操作层 */}
                <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                  <button onClick={() => setPreviewImage(task.imageUrl)} className="p-1.5 bg-white rounded-full text-gray-700 hover:text-blue-600" title="预览">
                    <Maximize2 size={16} />
                  </button>
                  <button onClick={() => removePendingTask(task.id)} className="p-1.5 bg-white rounded-full text-gray-700 hover:text-red-600" title="删除">
                    <X size={16} />
                  </button>
                </div>
              </div>
            ))}
            {/* 模拟粘贴提示框 */}
            <div className="w-32 h-32 flex-shrink-0 border-2 border-dashed border-gray-300 rounded-lg flex flex-col items-center justify-center text-gray-400 bg-gray-50/50">
              <Plus size={24} className="mb-2" />
              <span className="text-xs">继续粘贴图片</span>
            </div>
          </div>
        ) : (
          <div className="h-32 border-2 border-dashed border-gray-300 rounded-lg flex items-center justify-center text-gray-400 bg-gray-50">
            在此处 Ctrl+V 粘贴截图，或点击任意位置粘贴
          </div>
        )}
      </div>

      {submittedTaskItems.length > 0 && (
        <div className="bg-white p-4 rounded-xl shadow-sm border space-y-3">
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setTaskFilter('all')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                taskFilter === 'all'
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              全部 ({submittedTaskItems.length})
            </button>
            <button
              onClick={() => setTaskFilter('running')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                taskFilter === 'running'
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              运行中 ({runningCount})
            </button>
            <button
              onClick={() => setTaskFilter('completed')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                taskFilter === 'completed'
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              已完成 ({completedCount})
            </button>
            <button
              onClick={() => setTaskFilter('exception')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${
                taskFilter === 'exception'
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              异常/人工 ({exceptionCount})
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {runningItems.map((task) => (
              <TaskSwitcherButton
                key={task.taskId}
                taskId={task.taskId}
                state={task.state}
                isActive={activeTaskId === task.taskId}
                onSelect={() => setActiveTaskId(task.taskId)}
              />
            ))}
            {otherItems.map((task) => (
              <TaskSwitcherButton
                key={task.taskId}
                taskId={task.taskId}
                state={task.state}
                isActive={activeTaskId === task.taskId}
                onSelect={() => setActiveTaskId(task.taskId)}
              />
            ))}
            {filteredTaskItems.length === 0 && (
              <div className="text-sm text-gray-500 px-2 py-1">当前筛选下没有任务</div>
            )}
          </div>
        </div>
      )}

      {activeTaskId && (
        <div>
          <h2 className="text-lg font-semibold mb-4 text-gray-700">任务详情</h2>
          <TaskDetail taskId={activeTaskId} onPreview={setPreviewImage} />
        </div>
      )}

      {/* 全屏图片预览 Modal */}
      {previewImage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setPreviewImage(null)}>
          <button className="absolute top-4 right-4 text-white hover:text-gray-300">
            <X size={32} />
          </button>
          <img 
            src={previewImage} 
            alt="Preview" 
            className="max-w-full max-h-full object-contain rounded bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()} // 阻止点击图片时关闭
          />
        </div>
      )}

      {/* 设置弹窗 Modal */}
      {showSettings && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setShowSettings(false)}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b flex justify-between items-center bg-gray-50">
              <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2"><Settings size={20}/> 节点模型配置</h2>
              <button onClick={() => setShowSettings(false)} className="text-gray-500 hover:text-gray-800"><X size={24} /></button>
            </div>
            
            <div className="p-6 space-y-8 max-h-[70vh] overflow-y-auto">
              {/* Solver 配置 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-blue-600 border-b pb-2">Solver (解题) 节点</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                    <input type="text" value={solverConfig.model_name} onChange={e => setSolverConfig({...solverConfig, model_name: e.target.value})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                    <input type="number" value={solverConfig.max_tokens || ''} onChange={e => setSolverConfig({...solverConfig, max_tokens: parseMaxTokens(e.target.value)})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                    <input type="text" value={solverConfig.base_url} onChange={e => setSolverConfig({...solverConfig, base_url: e.target.value})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input type="password" value={solverConfig.api_key} onChange={e => setSolverConfig({...solverConfig, api_key: e.target.value})} placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                </div>
              </div>

              {/* Reviewer 配置 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-purple-600 border-b pb-2">Reviewer (审查) 节点</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                    <input type="text" value={reviewerConfig.model_name} onChange={e => setReviewerConfig({...reviewerConfig, model_name: e.target.value})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                    <input type="number" value={reviewerConfig.max_tokens || ''} onChange={e => setReviewerConfig({...reviewerConfig, max_tokens: parseMaxTokens(e.target.value)})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                    <input type="text" value={reviewerConfig.base_url} onChange={e => setReviewerConfig({...reviewerConfig, base_url: e.target.value})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input type="password" value={reviewerConfig.api_key} onChange={e => setReviewerConfig({...reviewerConfig, api_key: e.target.value})} placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                </div>
              </div>

              {/* Formatter 配置 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-green-600 border-b pb-2">Formatter (排版) 节点</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                    <input type="text" value={formatterConfig.model_name} onChange={e => setFormatterConfig({...formatterConfig, model_name: e.target.value})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                    <input type="number" value={formatterConfig.max_tokens || ''} onChange={e => setFormatterConfig({...formatterConfig, max_tokens: parseMaxTokens(e.target.value)})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                    <input type="text" value={formatterConfig.base_url} onChange={e => setFormatterConfig({...formatterConfig, base_url: e.target.value})} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input type="password" value={formatterConfig.api_key} onChange={e => setFormatterConfig({...formatterConfig, api_key: e.target.value})} placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                </div>
              </div>
            </div>

            <div className="p-4 border-t bg-gray-50 flex justify-end gap-3">
              <button onClick={() => setShowSettings(false)} className="px-4 py-2 text-gray-600 hover:bg-gray-200 rounded-lg transition-colors font-medium">取消</button>
              <button onClick={saveSettings} className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium shadow-sm">保存配置</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function TaskSwitcherButton({
  taskId,
  state,
  isActive,
  onSelect
}: {
  taskId: string
  state: string
  isActive: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`px-3 py-1.5 rounded-lg border text-sm font-mono transition-colors ${
        isActive
          ? 'border-blue-500 bg-blue-50 text-blue-700'
          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
      }`}
    >
      <span>{taskId}</span>
      <TaskStatusBadge state={state} />
    </button>
  )
}

function TaskStatusBadge({ state }: { state: string }) {
  const styleMap: Record<string, string> = {
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    manual: 'bg-yellow-100 text-yellow-700',
    cancelled: 'bg-gray-200 text-gray-700',
    solving: 'bg-blue-100 text-blue-700',
    reviewing: 'bg-blue-100 text-blue-700',
    formatting: 'bg-blue-100 text-blue-700',
    queued: 'bg-gray-100 text-gray-600'
  }

  return (
    <span className={`ml-2 px-2 py-0.5 rounded text-[10px] uppercase ${styleMap[state] || 'bg-gray-100 text-gray-600'}`}>
      {state}
    </span>
  )
}

function TaskDetail({ taskId, onPreview }: { taskId: string, onPreview: (url: string) => void }) {
  const [draftInput, setDraftInput] = useState('')
  const [streamedContent, setStreamedContent] = useState('')
  const [currentNode, setCurrentNode] = useState('')
  
  const { data: task, isLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.get(`/api/tasks/${taskId}`).then(res => res.data),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      if (state === 'completed' || state === 'failed' || state === 'manual' || state === 'cancelled') return false;
      return 2000;
    },
  })

  // 当切换任务时，清空流式输出的旧数据
  useEffect(() => {
    setStreamedContent('');
    setCurrentNode('');
  }, [taskId]);

  // Use React's useEffect to handle SSE connection
  const isTaskEnded = task?.state === 'completed' || task?.state === 'failed' || task?.state === 'manual' || task?.state === 'cancelled';
  
  useEffect(() => {
    if (isTaskEnded) return;

    const sse = new EventSource(`http://localhost:8000/api/tasks/${taskId}/stream`);
    
    sse.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.event === 'on_chat_model_stream') {
          setStreamedContent(prev => prev + (data.chunk || ''));
          if (data.node) setCurrentNode(data.node);
        } else if (data.event === 'end') {
          sse.close(); // 后端主动通知结束，断开连接避免重试
        } else if (data.error) {
          console.error("SSE Error:", data.error);
        }
      } catch (err) {
        console.error("Failed to parse SSE data", err);
      }
    };

    sse.onerror = (e) => {
      console.error("SSE connection error", e);
      sse.close();
    };

    return () => {
      sse.close();
      // 不在此处清空内容，避免重渲染时发生闪烁
    };
  }, [taskId, isTaskEnded]);

  const manualMutation = useMutation({
    mutationFn: ({ action, draft }: { action: 'resume' | 'fail', draft?: string }) => {
      const payload: Record<string, unknown> = { action, draft_solution: draft }
      if (action === 'resume') {
        Object.assign(payload, getLatestRetryConfigs())
      }
      return api.post(`/api/tasks/${taskId}/manual`, payload).then(res => res.data)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
    }
  })

  const cancelMutation = useMutation({
    mutationFn: () => api.post(`/api/tasks/${taskId}/cancel`).then(res => res.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['task', taskId] })
    }
  })

  if (isLoading) return <div className="text-gray-500 p-8 text-center bg-white rounded-xl border shadow-sm">Loading task data...</div>
  if (!task) return null

  const history = task.history ? JSON.parse(task.history) : {}
  const tokens = task.token_usage ? JSON.parse(task.token_usage) : {}

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 bg-white p-6 rounded-xl shadow-sm border">
      {/* Left: Original Image & Meta */}
      <div className="space-y-4 border-r pr-8">
        <div className="flex items-center justify-between">
          <h3 className="text-xl font-semibold">Task: <span className="text-sm font-mono text-gray-500">{task.task_id}</span></h3>
          <span className={`px-3 py-1 rounded-full text-xs font-bold uppercase ${
            task.state === 'completed' ? 'bg-green-100 text-green-700' :
            task.state === 'failed' ? 'bg-red-100 text-red-700' :
            task.state === 'manual' ? 'bg-yellow-100 text-yellow-700' :
            task.state === 'cancelled' ? 'bg-gray-200 text-gray-700' :
            'bg-blue-100 text-blue-700'
          }`}>
            {task.state}
          </span>
        </div>
        
        <div className="grid grid-cols-2 gap-4 text-sm text-gray-600 bg-gray-50 p-4 rounded">
          <div><strong>Retry Count:</strong> {task.retry_count} / 1</div>
          <div><strong>Total Tokens:</strong> {tokens.total_tokens || 0}</div>
        </div>

        <div className="relative border rounded bg-gray-50 p-2 h-64 flex items-center justify-center group overflow-hidden cursor-pointer" onClick={() => onPreview(task.image_url)}>
          <img src={task.image_url} alt="Task target" className="max-h-full object-contain" />
          <div className="absolute inset-0 bg-black/10 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
            <Maximize2 className="text-gray-700 bg-white/80 p-2 rounded-full w-10 h-10 shadow-sm" />
          </div>
        </div>

        {task.error_code && (
          <div className="bg-red-50 text-red-700 p-4 rounded text-sm font-mono border border-red-100">
            <strong>Error:</strong> {task.error_code}
          </div>
        )}
      </div>

      {/* Right: Agent Outputs & Interventions */}
      <div className="space-y-6">
        {task.state === 'manual' || task.state === 'failed' ? (
          <div className="space-y-4">
            <h3 className="font-semibold text-red-600 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-red-600 inline-block animate-pulse"></span>
              Manual Intervention Required
            </h3>
            <div className="text-sm text-gray-700 bg-red-50 p-3 rounded border border-red-100">
              <span className="font-bold">Reviewer Feedback:</span> {history.review_feedback || 'System Error'}
            </div>
            <textarea 
              className="w-full h-[280px] p-4 border rounded font-mono text-sm bg-gray-50 focus:bg-white focus:ring-2 focus:ring-blue-500 outline-none resize-none"
              defaultValue={history.draft_solution || ''}
              onChange={(e) => setDraftInput(e.target.value)}
              placeholder="Edit the draft solution here..."
            />
            <div className="flex gap-4 pt-2">
              <button 
                onClick={() => manualMutation.mutate({ action: 'resume', draft: draftInput || history.draft_solution })}
                className="bg-green-600 text-white px-6 py-2.5 rounded font-medium hover:bg-green-700 transition-colors shadow-sm"
              >
                Approve & Resume
              </button>
              <button 
                onClick={() => manualMutation.mutate({ action: 'fail' })}
                className="bg-white border border-red-200 text-red-600 px-6 py-2.5 rounded font-medium hover:bg-red-50 transition-colors shadow-sm"
              >
                Mark as Failed
              </button>
            </div>
          </div>
        ) : task.state === 'completed' ? (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="font-semibold text-green-600 flex items-center gap-2 shrink-0">
              <span className="w-2 h-2 rounded-full bg-green-600 inline-block"></span>
              Final Output
            </h3>
            <div className="prose prose-sm max-w-none border p-6 rounded-lg bg-gray-50 overflow-y-auto flex-grow max-h-[400px]">
              <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
                {task.final_result || ''}
              </ReactMarkdown>
            </div>
          </div>
        ) : task.state === 'cancelled' ? (
          <div className="space-y-4 h-full flex flex-col">
            <h3 className="font-semibold text-gray-700 flex items-center gap-2 shrink-0">
              <span className="w-2 h-2 rounded-full bg-gray-500 inline-block"></span>
              Workflow Cancelled
            </h3>
            <div className="text-sm text-gray-700 bg-gray-50 p-4 rounded border border-gray-200">
              <p className="font-medium">该任务已停止并以取消状态结束。</p>
              <p className="mt-2 font-mono text-xs">{task.error_code || 'Manually cancelled.'}</p>
            </div>
            <div className="flex-grow overflow-y-auto bg-white p-4 rounded border text-sm font-mono whitespace-pre-wrap shadow-inner text-gray-800 max-h-[400px]">
              {streamedContent || "No stream content was produced before cancellation."}
            </div>
          </div>
        ) : (
          <div className="h-full flex flex-col space-y-4 bg-gray-50 rounded-lg border border-dashed p-6 max-h-[500px]">
            <div className="flex items-center justify-between shrink-0">
              <div className="flex items-center gap-3">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600"></div>
                <p className="font-medium text-gray-700">Agent is working...</p>
                <span className="text-xs px-3 py-1 bg-white border rounded-full shadow-sm text-blue-600 font-mono">
                  {currentNode || task.state}
                </span>
              </div>
              <button 
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
                className="text-xs px-3 py-1.5 bg-red-50 text-red-600 border border-red-200 rounded-lg hover:bg-red-100 transition-colors flex items-center gap-1 disabled:opacity-50"
              >
                <X size={14} />
                {cancelMutation.isPending ? 'Stopping...' : 'Stop Workflow'}
              </button>
            </div>
            <div className="flex-grow overflow-y-auto bg-white p-4 rounded border text-sm font-mono whitespace-pre-wrap shadow-inner text-gray-800">
              {streamedContent || "Waiting for stream..."}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function AdminPanel({ onBack }: { onBack: () => void }) {
  const [searchTaskId, setSearchTaskId] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [selectedExportIds, setSelectedExportIds] = useState<string[]>([])
  const [isExporting, setIsExporting] = useState(false)
  const [editState, setEditState] = useState('')
  const [editHistory, setEditHistory] = useState('')
  const [editFinalResult, setEditFinalResult] = useState('')
  const [editErrorCode, setEditErrorCode] = useState('')
  const [editManualOperator, setEditManualOperator] = useState('')
  const [operationMessage, setOperationMessage] = useState<string | null>(null)

  const { data: listData, isLoading: listLoading } = useQuery({
    queryKey: ['admin-tasks', searchTaskId, stateFilter],
    queryFn: () =>
      api
        .get<AdminTaskListResponse>('/api/admin/tasks', {
          params: {
            task_id: searchTaskId || undefined,
            state: stateFilter || undefined,
            page: 1,
            page_size: 100
          }
        })
        .then((res) => res.data)
  })

  const { data: selectedTask, isLoading: detailLoading } = useQuery({
    queryKey: ['admin-task-detail', selectedTaskId],
    queryFn: () => api.get<AdminTask>(`/api/admin/tasks/${selectedTaskId}`).then((res) => res.data),
    enabled: !!selectedTaskId
  })

  const { data: logData } = useQuery({
    queryKey: ['admin-logs', selectedTaskId],
    queryFn: () => api.get<AdminLogListResponse>('/api/admin/logs', { params: { task_id: selectedTaskId } }).then((res) => res.data),
    enabled: !!selectedTaskId
  })

  useEffect(() => {
    if (!selectedTask) return
    setEditState(selectedTask.state || '')
    setEditHistory(selectedTask.history || '')
    setEditFinalResult(selectedTask.final_result || '')
    setEditErrorCode(selectedTask.error_code || '')
    setEditManualOperator(selectedTask.manual_operator || '')
  }, [selectedTask])

  useEffect(() => {
    if (!selectedTaskId && listData && listData.items.length > 0) {
      setSelectedTaskId(listData.items[0].task_id)
    }
  }, [selectedTaskId, listData])

  useEffect(() => {
    if (!listData) return
    const idsInList = new Set(listData.items.map((item) => item.task_id))
    setSelectedExportIds((prev) => prev.filter((id) => idsInList.has(id)))
  }, [listData])

  const updateMutation = useMutation({
    mutationFn: () =>
      api.patch(`/api/admin/tasks/${selectedTaskId}`, {
        state: editState || undefined,
        history: editHistory,
        final_result: editFinalResult,
        error_code: editErrorCode || null,
        manual_operator: editManualOperator || null
      }),
    onSuccess: () => {
      setOperationMessage('保存成功')
      queryClient.invalidateQueries({ queryKey: ['admin-task-detail', selectedTaskId] })
      queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
    },
    onError: (error: unknown) => {
      const msg = getErrorMessage(error, '保存失败')
      setOperationMessage(msg)
    }
  })

  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => api.delete(`/api/admin/tasks/${taskId}`),
    onSuccess: (_, taskId) => {
      setOperationMessage(`已删除 ${taskId}`)
      queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
      queryClient.invalidateQueries({ queryKey: ['admin-task-detail'] })
      queryClient.invalidateQueries({ queryKey: ['admin-logs'] })
      setSelectedTaskId(null)
    },
    onError: (error: unknown) => {
      const msg = getErrorMessage(error, '删除失败')
      setOperationMessage(msg)
    }
  })

  const retryMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTaskId || !selectedTask) throw new Error('请先选择任务')
      if (!['manual', 'failed'].includes(selectedTask.state)) throw new Error('仅 manual/failed 任务可重试')
      let draftSolution: string | undefined
      const historyText = (editHistory || '').trim()
      if (historyText) {
        let parsedHistory: unknown
        try {
          parsedHistory = JSON.parse(historyText)
        } catch {
          throw new Error('history 不是合法 JSON，请先修正后再重试')
        }
        if (parsedHistory && typeof parsedHistory === 'object' && 'draft_solution' in parsedHistory) {
          const rawDraft = (parsedHistory as { draft_solution?: unknown }).draft_solution
          if (typeof rawDraft === 'string' && rawDraft.trim().length > 0) {
            draftSolution = rawDraft
          }
        }
      }
      await api.post(`/api/tasks/${selectedTaskId}/manual`, {
        action: 'resume',
        draft_solution: draftSolution,
        ...getLatestRetryConfigs()
      })
    },
    onSuccess: () => {
      if (selectedTaskId) {
        persistTaskForDashboard(selectedTaskId)
      }
      setOperationMessage(`已触发重试：${selectedTaskId}`)
      queryClient.invalidateQueries({ queryKey: ['admin-task-detail', selectedTaskId] })
      queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
      queryClient.invalidateQueries({ queryKey: ['task', selectedTaskId] })
    },
    onError: (error: unknown) => {
      setOperationMessage(getErrorMessage(error, '重试失败'))
    }
  })

  const handleDelete = (taskId: string) => {
    const confirmed = window.confirm(`确认删除任务 ${taskId} 吗？`)
    if (!confirmed) return
    deleteMutation.mutate(taskId)
  }
  const canRetrySelectedTask = !!selectedTask && ['manual', 'failed'].includes(selectedTask.state)

  const toggleExportSelection = (taskId: string) => {
    setSelectedExportIds((prev) => (
      prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
    ))
  }

  const toggleSelectAllInList = () => {
    const ids = (listData?.items || []).map((item) => item.task_id)
    if (ids.length === 0) return
    const allSelected = ids.every((id) => selectedExportIds.includes(id))
    setSelectedExportIds(allSelected ? [] : ids)
  }

  const exportFinalResults = async () => {
    if (selectedExportIds.length === 0) {
      setOperationMessage('请先勾选任务')
      return
    }
    setIsExporting(true)
    try {
      const details = await Promise.all(
        selectedExportIds.map((taskId) =>
          api.get<AdminTask>(`/api/admin/tasks/${taskId}`).then((res) => res.data)
        )
      )
      const itemsWithFinalResult = details.filter((task) => (task.final_result || '').trim().length > 0)
      if (itemsWithFinalResult.length === 0) {
        setOperationMessage('所选任务没有可导出的最终排版结果')
        return
      }
      const fileContent = itemsWithFinalResult
        .map((task) => `# ${task.task_id}\n\n${(task.final_result || '').trim()}`)
        .join('\n\n---\n\n')
      const blob = new Blob([fileContent], { type: 'text/markdown;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
      link.href = url
      link.download = `final_results_${timestamp}.md`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      setOperationMessage(`导出成功，共 ${itemsWithFinalResult.length} 条最终排版结果`)
    } catch (error: unknown) {
      setOperationMessage(getErrorMessage(error, '导出失败'))
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <div className="max-w-7xl mx-auto p-8 space-y-6">
      <header className="border-b pb-4 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">后台管理</h1>
          <p className="text-sm text-gray-500 mt-2">按 task_id 管理任务记录</p>
        </div>
        <button onClick={onBack} className="px-4 py-2 bg-white border rounded-lg hover:bg-gray-50 transition-colors">
          返回处理台
        </button>
      </header>

      {operationMessage && (
        <div className="bg-blue-50 border border-blue-200 text-blue-700 px-4 py-2 rounded">
          {operationMessage}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white border rounded-xl p-4 space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700">Task ID 搜索</label>
            <input
              value={searchTaskId}
              onChange={(e) => setSearchTaskId(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="输入 task_id 关键字"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700">状态筛选</label>
            <select
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <option value="">全部</option>
              <option value="queued">queued</option>
              <option value="solving">solving</option>
              <option value="reviewing">reviewing</option>
              <option value="formatting">formatting</option>
              <option value="manual">manual</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
              <option value="cancelled">cancelled</option>
            </select>
          </div>

          <div className="pt-2 border-t">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-semibold text-gray-800">任务列表 ({listData?.total || 0})</h3>
              <div className="flex items-center gap-2">
                <button
                  onClick={toggleSelectAllInList}
                  className="text-xs px-2.5 py-1 border rounded hover:bg-gray-50"
                >
                  全选当前列表
                </button>
                <button
                  onClick={exportFinalResults}
                  disabled={isExporting || selectedExportIds.length === 0}
                  className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 bg-indigo-600 text-white rounded disabled:opacity-50 hover:bg-indigo-700"
                >
                  <Download size={12} />
                  {isExporting ? '导出中...' : `导出最终结果(${selectedExportIds.length})`}
                </button>
              </div>
            </div>
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {listLoading && <div className="text-sm text-gray-500">加载中...</div>}
              {!listLoading && (listData?.items || []).map((task) => (
                <div
                  key={task.task_id}
                  className={`w-full text-left p-3 rounded-lg border transition-colors ${
                    selectedTaskId === task.task_id ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={selectedExportIds.includes(task.task_id)}
                      onChange={() => toggleExportSelection(task.task_id)}
                      className="mt-0.5"
                    />
                    <button onClick={() => setSelectedTaskId(task.task_id)} className="flex-1 text-left">
                      <div className="text-xs font-mono text-gray-700 truncate">{task.task_id}</div>
                      <div className="text-xs text-gray-500 mt-1">{task.state} · retry {task.retry_count}</div>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="lg:col-span-2 bg-white border rounded-xl p-6 space-y-4">
          {!selectedTaskId && <div className="text-sm text-gray-500">请从左侧选择任务</div>}
          {detailLoading && <div className="text-sm text-gray-500">正在加载详情...</div>}
          {selectedTask && (
            <>
              <div className="flex justify-between items-center">
                <h2 className="text-lg font-semibold text-gray-800 font-mono">{selectedTask.task_id}</h2>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => retryMutation.mutate()}
                    disabled={!canRetrySelectedTask || retryMutation.isPending}
                    className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-green-700 border border-green-200 rounded hover:bg-green-50 disabled:opacity-50"
                  >
                    {retryMutation.isPending ? '重试中...' : '断点重试'}
                  </button>
                  <button
                    onClick={() => handleDelete(selectedTask.task_id)}
                    className="inline-flex items-center gap-2 px-3 py-1.5 text-sm text-red-600 border border-red-200 rounded hover:bg-red-50"
                  >
                    <Trash2 size={14} />
                    删除任务
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 text-sm bg-gray-50 border rounded p-4">
                <div><strong>thread_id:</strong> {selectedTask.thread_id}</div>
                <div><strong>retry:</strong> {selectedTask.retry_count}</div>
                <div><strong>created_at:</strong> {selectedTask.created_at || '-'}</div>
                <div><strong>updated_at:</strong> {selectedTask.updated_at || '-'}</div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">状态</label>
                <select value={editState} onChange={(e) => setEditState(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm">
                  <option value="queued">queued</option>
                  <option value="solving">solving</option>
                  <option value="reviewing">reviewing</option>
                  <option value="formatting">formatting</option>
                  <option value="manual">manual</option>
                  <option value="completed">completed</option>
                  <option value="failed">failed</option>
                  <option value="cancelled">cancelled</option>
                </select>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">manual_operator</label>
                <input value={editManualOperator} onChange={(e) => setEditManualOperator(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">error_code</label>
                <input value={editErrorCode} onChange={(e) => setEditErrorCode(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">history</label>
                <textarea value={editHistory} onChange={(e) => setEditHistory(e.target.value)} className="w-full min-h-36 border rounded-lg px-3 py-2 text-xs font-mono" />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">final_result</label>
                <textarea value={editFinalResult} onChange={(e) => setEditFinalResult(e.target.value)} className="w-full min-h-36 border rounded-lg px-3 py-2 text-xs font-mono" />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">token_usage（只读）</label>
                <pre className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono bg-gray-50 overflow-auto whitespace-pre-wrap">{selectedTask.token_usage || ''}</pre>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">image_url</label>
                <img src={selectedTask.image_url} alt="task" className="max-h-60 border rounded bg-gray-50 object-contain" />
              </div>

              <div className="flex justify-end">
                <button
                  onClick={() => updateMutation.mutate()}
                  disabled={updateMutation.isPending}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  <Save size={16} />
                  {updateMutation.isPending ? '保存中...' : '保存修改'}
                </button>
              </div>

              <div className="pt-2 border-t">
                <h3 className="font-semibold text-gray-800 mb-2">Agent Logs ({logData?.total || 0})</h3>
                <div className="space-y-3 max-h-80 overflow-y-auto">
                  {(logData?.items || []).map((log) => (
                    <div key={log.id} className="border rounded-lg p-3 bg-gray-50 text-xs space-y-2">
                      <div className="font-semibold text-gray-700">{log.node_name} · tokens {log.cost_tokens} · {log.created_at || '-'}</div>
                      <pre className="bg-white border rounded p-2 overflow-auto whitespace-pre-wrap">{log.request_payload || ''}</pre>
                      <pre className="bg-white border rounded p-2 overflow-auto whitespace-pre-wrap">{log.response_payload || ''}</pre>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function App() {
  const [currentView, setCurrentView] = useState<'dashboard' | 'admin'>('dashboard')

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-gray-100/50 py-8 font-sans text-gray-800">
        {currentView === 'dashboard' ? (
          <TaskDashboard onOpenAdmin={() => setCurrentView('admin')} />
        ) : (
          <AdminPanel onBack={() => setCurrentView('dashboard')} />
        )}
      </div>
    </QueryClientProvider>
  )
}

export default App
