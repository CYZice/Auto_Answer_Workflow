import { useState, ClipboardEvent } from 'react'
import { QueryClient, QueryClientProvider, useQuery, useMutation } from '@tanstack/react-query'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { X, Image as ImageIcon, Play, Plus, Maximize2, Settings } from 'lucide-react'
import 'katex/dist/katex.min.css'

const queryClient = new QueryClient()

const api = axios.create({
  baseURL: 'http://localhost:8000',
})

// --- Types ---
interface PendingTask {
  id: string;
  imageUrl: string;
}

interface ModelConfig {
  model_name: string;
  api_key: string;
  base_url: string;
  max_tokens: number;
}

// --- Components ---

function TaskDashboard() {
  // 待处理队列（本地维护）
  const [pendingQueue, setPendingQueue] = useState<PendingTask[]>([])
  // 当前正在预览的图片
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  // 当前活跃（正在查看）的后端任务ID
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  // 控制设置弹窗
  const [showSettings, setShowSettings] = useState(false)
  // 错误提示
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  // 模型配置状态，初始尝试从 localStorage 读取
  const [solverConfig, setSolverConfig] = useState<ModelConfig>(() => {
    const saved = localStorage.getItem('solver_config')
    return saved ? JSON.parse(saved) : { model_name: 'gpt-4o', api_key: '', base_url: 'https://api.openai.com/v1', max_tokens: 4096 }
  })
  const [reviewerConfig, setReviewerConfig] = useState<ModelConfig>(() => {
    const saved = localStorage.getItem('reviewer_config')
    return saved ? JSON.parse(saved) : { model_name: 'gpt-4o-mini', api_key: '', base_url: 'https://api.openai.com/v1', max_tokens: 2048 }
  })
  const [formatterConfig, setFormatterConfig] = useState<ModelConfig>(() => {
    const saved = localStorage.getItem('formatter_config')
    return saved ? JSON.parse(saved) : { model_name: 'gpt-4o-mini', api_key: '', base_url: 'https://api.openai.com/v1', max_tokens: 1024 }
  })

  // 保存设置到 localStorage
  const saveSettings = () => {
    localStorage.setItem('solver_config', JSON.stringify(solverConfig))
    localStorage.setItem('reviewer_config', JSON.stringify(reviewerConfig))
    localStorage.setItem('formatter_config', JSON.stringify(formatterConfig))
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
      solver_config: solverConfig.api_key ? solverConfig : undefined,
      reviewer_config: reviewerConfig.api_key ? reviewerConfig : undefined,
      formatter_config: formatterConfig.api_key ? formatterConfig : undefined
    }).then(res => res.data),
  })

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
        // 将最后一个任务设为当前活跃视图
        setActiveTaskId(result.task_id);
      } catch (error: any) {
        console.error("❌ 提交任务失败:", error);
        const errorMsg = error.response?.data?.detail || error.message || "未知错误";
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
        <button 
          onClick={() => setShowSettings(true)}
          className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-full transition-colors"
          title="模型配置"
        >
          <Settings size={24} />
        </button>
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

      {/* 任务详情区域（如果是实际项目，这里应该有个列表供切换） */}
      {activeTaskId && (
        <div>
          <h2 className="text-lg font-semibold mb-4 text-gray-700">最近提交的任务详情</h2>
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

function TaskDetail({ taskId, onPreview }: { taskId: string, onPreview: (url: string) => void }) {
  const [draftInput, setDraftInput] = useState('')
  
  const { data: task, isLoading } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.get(`/api/tasks/${taskId}`).then(res => res.data),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      if (state === 'completed' || state === 'failed' || state === 'manual') return false;
      return 2000;
    },
  })

  const manualMutation = useMutation({
    mutationFn: ({ action, draft }: { action: 'resume' | 'fail', draft?: string }) => 
      api.post(`/api/tasks/${taskId}/manual`, { action, draft_solution: draft }).then(res => res.data),
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
        ) : (
          <div className="h-full flex flex-col justify-center items-center text-gray-500 space-y-4 min-h-[300px] bg-gray-50 rounded-lg border border-dashed">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600"></div>
            <p className="font-medium">Agent is working on this task...</p>
            <p className="text-sm px-4 py-1.5 bg-white border rounded-full shadow-sm">
              Current Node: <span className="font-mono text-blue-600 font-bold ml-1">{task.state}</span>
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-gray-100/50 py-8 font-sans text-gray-800">
        <TaskDashboard />
      </div>
    </QueryClientProvider>
  )
}

export default App

