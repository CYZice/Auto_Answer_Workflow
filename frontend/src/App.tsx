import { useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery, useMutation } from '@tanstack/react-query'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

const queryClient = new QueryClient()

const api = axios.create({
  baseURL: 'http://localhost:8000',
})

// --- Components ---

function TaskDashboard() {
  const [imageUrl, setImageUrl] = useState('https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Pythagorean_theorem_abc.svg/800px-Pythagorean_theorem_abc.svg.png')
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: (url: string) => api.post('/api/tasks', { image_url: url }).then(res => res.data),
    onSuccess: (data) => {
      setActiveTaskId(data.task_id)
    }
  })

  return (
    <div className="max-w-6xl mx-auto p-8 space-y-8">
      <header className="flex justify-between items-center border-b pb-4">
        <h1 className="text-3xl font-bold text-gray-900">Zyb-Agent Monitor</h1>
        <div className="flex gap-4 items-center">
          <input 
            type="text" 
            value={imageUrl}
            onChange={(e) => setImageUrl(e.target.value)}
            className="border p-2 rounded w-96 text-sm"
            placeholder="Image URL"
          />
          <button 
            onClick={() => createMutation.mutate(imageUrl)}
            disabled={createMutation.isPending}
            className="bg-blue-600 text-white px-4 py-2 rounded font-medium disabled:opacity-50"
          >
            {createMutation.isPending ? 'Starting...' : 'New Task'}
          </button>
        </div>
      </header>

      {activeTaskId && <TaskDetail taskId={activeTaskId} />}
    </div>
  )
}

function TaskDetail({ taskId }: { taskId: string }) {
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

  if (isLoading) return <div className="text-gray-500">Loading task data...</div>
  if (!task) return null

  const history = task.history ? JSON.parse(task.history) : {}
  const tokens = task.token_usage ? JSON.parse(task.token_usage) : {}

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-8 bg-white p-6 rounded-xl shadow-sm border">
      {/* Left: Original Image & Meta */}
      <div className="space-y-4 border-r pr-8">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold">Task: <span className="text-sm font-mono text-gray-500">{task.task_id}</span></h2>
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

        <div className="border rounded bg-gray-50 p-2 h-64 flex items-center justify-center overflow-hidden">
          <img src={task.image_url} alt="Task target" className="max-h-full object-contain" />
        </div>

        {task.error_code && (
          <div className="bg-red-50 text-red-700 p-4 rounded text-sm font-mono">
            <strong>Error:</strong> {task.error_code}
          </div>
        )}
      </div>

      {/* Right: Agent Outputs & Interventions */}
      <div className="space-y-6">
        {task.state === 'manual' || task.state === 'failed' ? (
          <div className="space-y-4">
            <h3 className="font-semibold text-red-600 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-red-600 inline-block"></span>
              Manual Intervention Required
            </h3>
            <div className="text-sm text-gray-600 mb-2">Reviewer Feedback: {history.review_feedback || 'System Error'}</div>
            <textarea 
              className="w-full h-64 p-4 border rounded font-mono text-sm bg-gray-50 focus:bg-white focus:ring-2 outline-none"
              defaultValue={history.draft_solution || ''}
              onChange={(e) => setDraftInput(e.target.value)}
              placeholder="Edit the draft solution here..."
            />
            <div className="flex gap-4">
              <button 
                onClick={() => manualMutation.mutate({ action: 'resume', draft: draftInput || history.draft_solution })}
                className="bg-green-600 text-white px-6 py-2 rounded font-medium hover:bg-green-700"
              >
                Approve & Resume
              </button>
              <button 
                onClick={() => manualMutation.mutate({ action: 'fail' })}
                className="bg-red-100 text-red-700 px-6 py-2 rounded font-medium hover:bg-red-200"
              >
                Mark as Failed
              </button>
            </div>
          </div>
        ) : task.state === 'completed' ? (
          <div className="space-y-4">
            <h3 className="font-semibold text-green-600 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-600 inline-block"></span>
              Final Output (Rendered)
            </h3>
            <div className="prose prose-sm max-w-none border p-6 rounded bg-gray-50 overflow-y-auto max-h-[500px]">
              <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
                {task.final_result || ''}
              </ReactMarkdown>
            </div>
          </div>
        ) : (
          <div className="space-y-4 h-full flex flex-col justify-center items-center text-gray-400">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mb-4"></div>
            <p>Agent is working on this task...</p>
            <p className="text-sm">Current Node: <span className="font-mono text-blue-500">{task.state}</span></p>
          </div>
        )}
      </div>
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-gray-100 py-8">
        <TaskDashboard />
      </div>
    </QueryClientProvider>
  )
}

export default App
