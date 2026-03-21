import { QueryClient, QueryClientProvider, useMutation, useQueries, useQuery } from '@tanstack/react-query'
import axios from 'axios'
import 'katex/dist/katex.min.css'
import { ChevronDown, ChevronUp, Database, Download, Image as ImageIcon, Maximize2, Play, Plus, Save, Settings, Trash2, X } from 'lucide-react'
import { ChangeEvent, ClipboardEvent, DragEvent, MouseEvent, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

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
const WORKFLOW_TEMPLATE_ID_STORAGE_KEY = 'workflow_template_id'
const WORKFLOW_NODE_ORDER = ['solver', 'reviewer', 'formatter'] as const
const PAPER_BUILDER_LOCAL_DRAFT_KEY = 'paper_builder_local_draft_v1'
const PAPER_BUILDER_REMOTE_DRAFT_ID = 'default'

type WorkflowNode = (typeof WORKFLOW_NODE_ORDER)[number]

const getErrorMessage = (error: unknown, fallback: string) => {
  if (axios.isAxiosError(error)) {
    return (error.response?.data as { detail?: string } | undefined)?.detail || error.message || fallback
  }
  if (error instanceof Error) return error.message
  return fallback
}

// --- Types ---
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
  question_preview?: string | null;
  answer_preview?: string | null;
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

interface PaperGroup {
  id: string;
  name: string;
  taskIds: string[];
}

interface PaperBuilderDraftResponse {
  draft_id: string;
  name: string;
  paper_subject?: string;
  paper_title?: string;
  groups: Array<{
    group_id: string;
    group_name: string;
    task_ids: string[];
  }>;
  updated_at?: string | null;
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
  workflow_template_id: string;
}

interface RuntimeSettingsResponse {
  active_template_id: string;
  fallback: {
    global: string[];
    nodes: {
      solver: string[];
      reviewer: string[];
      formatter: string[];
    };
  };
}

interface PromptTemplateItem {
  template_id: string;
  name: string;
  description?: string;
}

interface PromptTemplateDetail extends PromptTemplateItem {
  prompts: {
    solver: { system: string; user: string };
    reviewer: { system: string; user: string };
    formatter: { system: string; user: string };
  };
}

interface SettingsSnapshot {
  solverConfig: ModelConfig;
  reviewerConfig: ModelConfig;
  formatterConfig: ModelConfig;
  activeTemplateId: string;
  globalFallbackText: string;
  solverFallbackText: string;
  reviewerFallbackText: string;
  formatterFallbackText: string;
  templateName: string;
  templateDescription: string;
  solverSystemPrompt: string;
  solverUserPrompt: string;
  reviewerSystemPrompt: string;
  reviewerUserPrompt: string;
  formatterSystemPrompt: string;
  formatterUserPrompt: string;
}

const toSettingsSnapshotString = (snapshot: SettingsSnapshot) => JSON.stringify(snapshot)

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
  formatter_config: readStoredJson<ModelConfig>(FORMATTER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 1024 }),
  workflow_template_id: localStorage.getItem(WORKFLOW_TEMPLATE_ID_STORAGE_KEY) || 'workflow_a'
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
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [pendingInputImage, setPendingInputImage] = useState<string | null>(null)
  const [inputDraft, setInputDraft] = useState('')
  const [inputSelectedNodes, setInputSelectedNodes] = useState<WorkflowNode[]>([...WORKFLOW_NODE_ORDER])
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
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
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
  const [runtimeLoading, setRuntimeLoading] = useState(false)
  const [runtimeError, setRuntimeError] = useState<string | null>(null)
  const [activeTemplateId, setActiveTemplateId] = useState<string>(() => localStorage.getItem(WORKFLOW_TEMPLATE_ID_STORAGE_KEY) || 'workflow_a')
  const [globalFallbackText, setGlobalFallbackText] = useState('')
  const [solverFallbackText, setSolverFallbackText] = useState('')
  const [reviewerFallbackText, setReviewerFallbackText] = useState('')
  const [formatterFallbackText, setFormatterFallbackText] = useState('')
  const [templateItems, setTemplateItems] = useState<PromptTemplateItem[]>([])
  const [templateName, setTemplateName] = useState('')
  const [templateDescription, setTemplateDescription] = useState('')
  const [solverSystemPrompt, setSolverSystemPrompt] = useState('')
  const [solverUserPrompt, setSolverUserPrompt] = useState('')
  const [reviewerSystemPrompt, setReviewerSystemPrompt] = useState('')
  const [reviewerUserPrompt, setReviewerUserPrompt] = useState('')
  const [formatterSystemPrompt, setFormatterSystemPrompt] = useState('')
  const [formatterUserPrompt, setFormatterUserPrompt] = useState('')
  const [settingsBaseline, setSettingsBaseline] = useState('')
  const [settingsSaving, setSettingsSaving] = useState(false)

  const currentSettingsSnapshot = toSettingsSnapshotString({
    solverConfig,
    reviewerConfig,
    formatterConfig,
    activeTemplateId,
    globalFallbackText,
    solverFallbackText,
    reviewerFallbackText,
    formatterFallbackText,
    templateName,
    templateDescription,
    solverSystemPrompt,
    solverUserPrompt,
    reviewerSystemPrompt,
    reviewerUserPrompt,
    formatterSystemPrompt,
    formatterUserPrompt
  })

  const isSettingsDirty = showSettings
    && settingsBaseline.length > 0
    && settingsBaseline !== currentSettingsSnapshot

  const listToText = (items: string[]) => items.join('\n')
  const textToList = (text: string) => Array.from(new Set(
    text
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter((item) => item.length > 0)
  ))

  const loadTemplateDetail = async (templateId: string) => {
    const detail = await api.get<PromptTemplateDetail>(`/api/templates/${templateId}`).then((res) => res.data)
    setTemplateName(detail.name || templateId)
    setTemplateDescription(detail.description || '')
    setSolverSystemPrompt(detail.prompts?.solver?.system || '')
    setSolverUserPrompt(detail.prompts?.solver?.user || '')
    setReviewerSystemPrompt(detail.prompts?.reviewer?.system || '')
    setReviewerUserPrompt(detail.prompts?.reviewer?.user || '')
    setFormatterSystemPrompt(detail.prompts?.formatter?.system || '')
    setFormatterUserPrompt(detail.prompts?.formatter?.user || '')
  }

  const openSettingsModal = async () => {
    setRuntimeLoading(true)
    setRuntimeError(null)
    try {
      const [runtime, templates] = await Promise.all([
        api.get<RuntimeSettingsResponse>('/api/settings/runtime').then((res) => res.data),
        api.get<PromptTemplateItem[]>('/api/templates').then((res) => res.data)
      ])

      setGlobalFallbackText(listToText(runtime.fallback?.global || []))
      setSolverFallbackText(listToText(runtime.fallback?.nodes?.solver || []))
      setReviewerFallbackText(listToText(runtime.fallback?.nodes?.reviewer || []))
      setFormatterFallbackText(listToText(runtime.fallback?.nodes?.formatter || []))

      const normalizedTemplates = Array.isArray(templates) ? templates : []
      setTemplateItems(normalizedTemplates)

      const pickedTemplateId = runtime.active_template_id
        || normalizedTemplates[0]?.template_id
        || 'workflow_a'
      const detail = await api.get<PromptTemplateDetail>(`/api/templates/${pickedTemplateId}`).then((res) => res.data)

      setActiveTemplateId(pickedTemplateId)
      setTemplateName(detail.name || pickedTemplateId)
      setTemplateDescription(detail.description || '')
      setSolverSystemPrompt(detail.prompts?.solver?.system || '')
      setSolverUserPrompt(detail.prompts?.solver?.user || '')
      setReviewerSystemPrompt(detail.prompts?.reviewer?.system || '')
      setReviewerUserPrompt(detail.prompts?.reviewer?.user || '')
      setFormatterSystemPrompt(detail.prompts?.formatter?.system || '')
      setFormatterUserPrompt(detail.prompts?.formatter?.user || '')

      const baseline = toSettingsSnapshotString({
        solverConfig,
        reviewerConfig,
        formatterConfig,
        activeTemplateId: pickedTemplateId,
        globalFallbackText: listToText(runtime.fallback?.global || []),
        solverFallbackText: listToText(runtime.fallback?.nodes?.solver || []),
        reviewerFallbackText: listToText(runtime.fallback?.nodes?.reviewer || []),
        formatterFallbackText: listToText(runtime.fallback?.nodes?.formatter || []),
        templateName: detail.name || pickedTemplateId,
        templateDescription: detail.description || '',
        solverSystemPrompt: detail.prompts?.solver?.system || '',
        solverUserPrompt: detail.prompts?.solver?.user || '',
        reviewerSystemPrompt: detail.prompts?.reviewer?.system || '',
        reviewerUserPrompt: detail.prompts?.reviewer?.user || '',
        formatterSystemPrompt: detail.prompts?.formatter?.system || '',
        formatterUserPrompt: detail.prompts?.formatter?.user || ''
      })
      setSettingsBaseline(baseline)
      setShowSettings(true)
    } catch (error: unknown) {
      setRuntimeError(getErrorMessage(error, '加载设置失败'))
    } finally {
      setRuntimeLoading(false)
    }
  }

  // 保存设置到 localStorage
  const saveSettings = async () => {
    setSettingsSaving(true)
    localStorage.setItem(SOLVER_CONFIG_STORAGE_KEY, JSON.stringify(solverConfig))
    localStorage.setItem(REVIEWER_CONFIG_STORAGE_KEY, JSON.stringify(reviewerConfig))
    localStorage.setItem(FORMATTER_CONFIG_STORAGE_KEY, JSON.stringify(formatterConfig))
    localStorage.setItem(WORKFLOW_TEMPLATE_ID_STORAGE_KEY, activeTemplateId)

    try {
      await api.put('/api/settings/runtime', {
        active_template_id: activeTemplateId,
        fallback: {
          global: textToList(globalFallbackText),
          nodes: {
            solver: textToList(solverFallbackText),
            reviewer: textToList(reviewerFallbackText),
            formatter: textToList(formatterFallbackText)
          }
        }
      })

      await api.put(`/api/templates/${activeTemplateId}`, {
        name: templateName || activeTemplateId,
        description: templateDescription,
        prompts: {
          solver: { system: solverSystemPrompt, user: solverUserPrompt },
          reviewer: { system: reviewerSystemPrompt, user: reviewerUserPrompt },
          formatter: { system: formatterSystemPrompt, user: formatterUserPrompt }
        }
      })
    } catch (error: unknown) {
      setErrorMessage(getErrorMessage(error, '保存设置失败'))
      setSettingsSaving(false)
      return
    }

    setSettingsBaseline(currentSettingsSnapshot)
    setSuccessMessage('设置保存成功')
    setSettingsSaving(false)
    setShowSettings(false)
  }

  const tryCloseSettings = () => {
    if (settingsSaving) return
    if (isSettingsDirty) {
      const confirmed = window.confirm('当前设置有未保存改动，确认要关闭吗？')
      if (!confirmed) return
    }
    setShowSettings(false)
  }

  useEffect(() => {
    if (!showSettings || !isSettingsDirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [showSettings, isSettingsDirty])

  useEffect(() => {
    if (!successMessage) return
    const timer = window.setTimeout(() => setSuccessMessage(null), 2500)
    return () => window.clearTimeout(timer)
  }, [successMessage])

  const readImageAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (event) => {
      const result = event.target?.result
      if (typeof result === 'string') {
        resolve(result)
        return
      }
      reject(new Error('图片读取失败'))
    }
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })

  const loadInputImageFile = async (file: File) => {
    if (!file.type.startsWith('image/')) {
      setErrorMessage('请选择图片文件。')
      return
    }
    try {
      const dataUrl = await readImageAsDataUrl(file)
      setPendingInputImage(dataUrl)
      setErrorMessage(null)
    } catch {
      setErrorMessage('读取本地图片失败，请重试。')
    }
  }

  // 处理剪贴板粘贴图片
  const handlePaste = (e: ClipboardEvent<HTMLDivElement>) => {
    if (pendingInputImage) {
      setErrorMessage('当前已有待提交题目，请先提交或删除后再粘贴下一题。')
      return
    }
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image') !== -1) {
        const file = items[i].getAsFile();
        if (!file) continue;
        void loadInputImageFile(file)
        break;
      }
    }
  };

  const handlePickLocalImage = () => {
    if (pendingInputImage) {
      setErrorMessage('当前已有待提交题目，请先提交或删除后再选择下一题。')
      return
    }
    fileInputRef.current?.click()
  }

  const handleLocalImageChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    void loadInputImageFile(file)
    e.target.value = ''
  }

  const toggleInputNodeSelection = (node: WorkflowNode) => {
    setInputSelectedNodes((prev) => {
      if (prev.includes(node)) {
        return prev.filter((item) => item !== node)
      }
      return [...prev, node]
    })
  }

  // 创建任务的 Mutation
  const createMutation = useMutation({
    mutationFn: (payload: {
      imageUrl: string;
      entryPoint: WorkflowNode;
      targetNodes: WorkflowNode[];
      draftSolution?: string;
    }) => api.post('/api/tasks', {
      image_url: payload.imageUrl,
      solver_config: solverConfig,
      reviewer_config: reviewerConfig,
      formatter_config: formatterConfig,
      workflow_template_id: activeTemplateId,
      entry_point: payload.entryPoint,
      target_nodes: payload.targetNodes,
      draft_solution: payload.draftSolution || null
    }).then(res => res.data),
  })

  const orderedInputNodes = WORKFLOW_NODE_ORDER.filter((node) => inputSelectedNodes.includes(node))
  const inputNodeIndices = orderedInputNodes.map((node) => WORKFLOW_NODE_ORDER.indexOf(node))
  const inputHasContiguousSelection = inputNodeIndices.every((idx, i) => i === 0 || idx - inputNodeIndices[i - 1] === 1)
  const inputEntryPoint = orderedInputNodes.length > 0 ? orderedInputNodes[0] : undefined
  const inputNeedsDraft = inputEntryPoint === 'reviewer' || inputEntryPoint === 'formatter'
  const inputDraftValue = inputDraft.trim()
  const canSubmitInputTask = Boolean(pendingInputImage)
    && orderedInputNodes.length > 0
    && inputHasContiguousSelection
    && (!inputNeedsDraft || inputDraftValue.length > 0)
    && !createMutation.isPending
  const inputBlockedReason = !pendingInputImage
    ? '请先粘贴一张题目图片。'
    : (orderedInputNodes.length === 0
      ? '请至少选择一个工作流节点。'
      : (!inputHasContiguousSelection
        ? '工作流节点必须连续，不能跳选。'
        : (inputNeedsDraft && inputDraftValue.length === 0
          ? '从 Reviewer 或 Formatter 开始时，草稿文本为必填。'
          : '')))

  const { data: activeTasksFromDb } = useQuery({
    queryKey: ['dashboard-active-tasks'],
    queryFn: () => api.get<AdminTask[]>('/api/tasks/active').then((res) => res.data),
    refetchInterval: 3000,
  })

  const mergedTaskIds = useMemo(() => {
    const ids = new Set<string>()
    submittedTasks.forEach((task) => {
      if (task.taskId.trim().length > 0) ids.add(task.taskId)
    })
      ; (activeTasksFromDb || []).forEach((task) => {
        if (task?.task_id) ids.add(task.task_id)
      })
    return Array.from(ids)
  }, [submittedTasks, activeTasksFromDb])

  const submittedTaskStatusQueries = useQueries({
    queries: mergedTaskIds.map((taskId) => ({
      queryKey: ['task', taskId],
      queryFn: () => api.get<AdminTask>(`/api/tasks/${taskId}`).then((res) => res.data),
      refetchInterval: (query: { state: { data?: AdminTask } }) => {
        const state = query.state.data?.state;
        if (state === 'completed' || state === 'failed' || state === 'manual' || state === 'cancelled') return false;
        return 2000;
      },
    })),
  })
  const submittedTaskItems = useMemo(
    () =>
      mergedTaskIds.map((taskId, index) => {
        const taskData = submittedTaskStatusQueries[index]?.data as AdminTask | undefined
        return {
          taskId,
          state: taskData?.state || 'queued'
        }
      }),
    [mergedTaskIds, submittedTaskStatusQueries]
  )

  const queryStateByTaskId = useMemo(() => {
    const map = new Map<string, (typeof submittedTaskStatusQueries)[number]>()
    mergedTaskIds.forEach((taskId, index) => {
      map.set(taskId, submittedTaskStatusQueries[index])
    })
    return map
  }, [mergedTaskIds, submittedTaskStatusQueries])

  useEffect(() => {
    if (submittedTasks.length === 0) return

    const taskIdsToRemove = submittedTasks.reduce<string[]>((acc, task) => {
      const queryState = queryStateByTaskId.get(task.taskId)
      const queryError = queryState?.error
      if (axios.isAxiosError(queryError) && queryError.response?.status === 404) {
        acc.push(task.taskId)
        return acc
      }
      const taskState = (queryState?.data as AdminTask | undefined)?.state
      if (taskState === 'completed') {
        acc.push(task.taskId)
      }
      return acc
    }, [])

    if (taskIdsToRemove.length === 0) return

    const taskIdsToRemoveSet = new Set(taskIdsToRemove)
    setSubmittedTasks((prev) => prev.filter((task) => !taskIdsToRemoveSet.has(task.taskId)))
    setActiveTaskId((prev) => {
      if (!prev) return prev
      if (taskIdsToRemoveSet.has(prev)) return null
      return prev
    })
  }, [submittedTasks, queryStateByTaskId])
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
    if (mergedTaskIds.length === 0) {
      if (activeTaskId !== null) {
        setActiveTaskId(null)
      }
      return
    }
    if (activeTaskId && mergedTaskIds.includes(activeTaskId)) {
      return
    }
    setActiveTaskId(mergedTaskIds[0])
  }, [mergedTaskIds, activeTaskId])

  // 处理“提交本题”逻辑
  const handleSubmitCurrentTask = async () => {
    if (!canSubmitInputTask || !pendingInputImage || !inputEntryPoint) return;
    setErrorMessage(null);

    try {
      const result = await createMutation.mutateAsync({
        imageUrl: pendingInputImage,
        entryPoint: inputEntryPoint,
        targetNodes: orderedInputNodes,
        draftSolution: inputNeedsDraft ? inputDraftValue : undefined,
      });
      setSubmittedTasks((prev) => (
        prev.some((item) => item.taskId === result.task_id)
          ? prev
          : [...prev, { taskId: result.task_id }]
      ));
      setActiveTaskId(result.task_id);
      setPendingInputImage(null);
      setInputDraft('');
      setInputSelectedNodes([...WORKFLOW_NODE_ORDER]);
    } catch (error: unknown) {
      const errorMsg = getErrorMessage(error, "未知错误");
      setErrorMessage(`提交失败: ${errorMsg}`);
    }
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
          <button onClick={() => setErrorMessage(null)} className="text-red-500 hover:text-red-700"><X size={18} /></button>
        </div>
      )}

      {successMessage && (
        <div className="bg-green-50 border-l-4 border-green-500 p-4 rounded shadow-sm flex justify-between items-start">
          <div className="text-green-700">
            <p className="font-bold">操作成功</p>
            <p className="text-sm">{successMessage}</p>
          </div>
          <button onClick={() => setSuccessMessage(null)} className="text-green-500 hover:text-green-700"><X size={18} /></button>
        </div>
      )}

      {/* 顶部标题与说明 */}
      <header className="border-b pb-4 flex justify-between items-start">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Zyb-Agent 生产流水线</h1>
          <p className="text-sm text-gray-500 mt-2">提示: 直接在这个页面 <kbd className="bg-gray-100 px-1 rounded border">Ctrl+V</kbd> 粘贴图片，每次仅允许录入一题并提交后再录入下一题。</p>
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
            onClick={() => void openSettingsModal()}
            className="p-2 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-full transition-colors"
            title="模型配置"
            disabled={runtimeLoading}
          >
            <Settings size={24} />
          </button>
        </div>
      </header>

      {runtimeError && (
        <div className="bg-yellow-50 border-l-4 border-yellow-500 p-4 rounded shadow-sm text-yellow-700 text-sm">
          {runtimeError}
        </div>
      )}

      {/* 单题输入区域 */}
      <div className="bg-white p-6 rounded-xl shadow-sm border space-y-4">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={handleLocalImageChange}
        />
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <ImageIcon size={20} className="text-blue-500" />
            当前题目输入
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handlePickLocalImage}
              disabled={!!pendingInputImage}
              className="flex items-center gap-2 bg-white text-blue-700 border border-blue-200 px-4 py-2 rounded-lg font-medium disabled:opacity-50 hover:bg-blue-50 transition-colors"
              title={pendingInputImage ? '当前已有待提交题目' : '从本地选择图片'}
            >
              <Plus size={18} />
              本地选图
            </button>
            <button
              onClick={handleSubmitCurrentTask}
              disabled={!canSubmitInputTask}
              className="flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg font-medium disabled:opacity-50 hover:bg-blue-700 transition-colors"
              title={inputBlockedReason || '提交当前题目'}
            >
              <Play size={18} />
              {createMutation.isPending ? '正在提交...' : '提交本题'}
            </button>
          </div>
        </div>

        {pendingInputImage ? (
          <div className="space-y-4">
            <div className="relative group border rounded-lg overflow-hidden bg-gray-50 h-48 flex items-center justify-center">
              <img src={pendingInputImage} alt="pending" className="max-h-full object-contain" />
              <div className="absolute inset-0 bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                <button onClick={() => setPreviewImage(pendingInputImage)} className="p-2 bg-white rounded-full text-gray-700 hover:text-blue-600" title="预览">
                  <Maximize2 size={18} />
                </button>
                <button
                  onClick={() => {
                    setPendingInputImage(null)
                    setInputDraft('')
                    setInputSelectedNodes([...WORKFLOW_NODE_ORDER])
                  }}
                  className="p-2 bg-white rounded-full text-gray-700 hover:text-red-600"
                  title="删除当前题目"
                >
                  <Trash2 size={18} />
                </button>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {WORKFLOW_NODE_ORDER.map((node, idx) => (
                <div key={node} className="flex items-center gap-2">
                  <label className="inline-flex items-center gap-2 text-sm px-3 py-1.5 rounded border bg-white border-gray-200">
                    <input
                      type="checkbox"
                      checked={inputSelectedNodes.includes(node)}
                      onChange={() => toggleInputNodeSelection(node)}
                    />
                    <span className="font-medium">
                      {node === 'solver' ? 'Solver 解题' : node === 'reviewer' ? 'Reviewer 审查' : 'Formatter 排版'}
                    </span>
                  </label>
                  {idx < WORKFLOW_NODE_ORDER.length - 1 && <span className="text-gray-400">-&gt;</span>}
                </div>
              ))}
            </div>

            <div className="text-xs text-gray-600 bg-white rounded border px-3 py-2">
              <div>入口节点: <span className="font-mono">{inputEntryPoint || '-'}</span></div>
              <div>目标节点: <span className="font-mono">{orderedInputNodes.join(', ') || '-'}</span></div>
            </div>

            {inputNeedsDraft && (
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">草稿文本（必填）</label>
                <textarea
                  className="w-full h-28 p-3 border rounded text-sm font-mono bg-white focus:ring-2 focus:ring-blue-500 outline-none resize-none"
                  value={inputDraft}
                  onChange={(e) => setInputDraft(e.target.value)}
                  placeholder="从 Reviewer/Formatter 开始执行时，请输入可用草稿文本"
                />
              </div>
            )}

            {inputBlockedReason && !canSubmitInputTask && (
              <div className="text-xs text-red-600 bg-red-50 border border-red-100 px-3 py-2 rounded">
                {inputBlockedReason}
              </div>
            )}
          </div>
        ) : (
          <div className="h-36 border-2 border-dashed border-gray-300 rounded-lg flex flex-col items-center justify-center text-gray-400 bg-gray-50">
            <Plus size={24} className="mb-2" />
            <span className="text-sm">支持 Ctrl+V 粘贴，或点击上方“本地选图”上传题目截图</span>
            <span className="text-xs mt-1">一次只录入一题，提交后再开始下一题</span>
          </div>
        )}
      </div>

      {submittedTaskItems.length > 0 && (
        <div className="bg-white p-4 rounded-xl shadow-sm border space-y-3">
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setTaskFilter('all')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${taskFilter === 'all'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                }`}
            >
              全部 ({submittedTaskItems.length})
            </button>
            <button
              onClick={() => setTaskFilter('running')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${taskFilter === 'running'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                }`}
            >
              运行中 ({runningCount})
            </button>
            <button
              onClick={() => setTaskFilter('completed')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${taskFilter === 'completed'
                ? 'border-blue-500 bg-blue-50 text-blue-700'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                }`}
            >
              已完成 ({completedCount})
            </button>
            <button
              onClick={() => setTaskFilter('exception')}
              className={`px-3 py-1.5 rounded-lg border text-sm transition-colors ${taskFilter === 'exception'
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={tryCloseSettings}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b flex justify-between items-center bg-gray-50">
              <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2"><Settings size={20} /> 节点模型配置</h2>
              <button onClick={tryCloseSettings} className="text-gray-500 hover:text-gray-800"><X size={24} /></button>
            </div>

            <div className="p-6 space-y-8 max-h-[70vh] overflow-y-auto">
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-indigo-600 border-b pb-2">工作流模板与 Prompt</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">激活模板</label>
                    <select
                      value={activeTemplateId}
                      onChange={async (e) => {
                        const templateId = e.target.value
                        setActiveTemplateId(templateId)
                        await loadTemplateDetail(templateId)
                      }}
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                    >
                      {templateItems.map((item) => (
                        <option key={item.template_id} value={item.template_id}>
                          {item.name} ({item.template_id})
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模板名称</label>
                    <input
                      type="text"
                      value={templateName}
                      onChange={(e) => setTemplateName(e.target.value)}
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">模板描述</label>
                    <input
                      type="text"
                      value={templateDescription}
                      onChange={(e) => setTemplateDescription(e.target.value)}
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Solver System Prompt</label>
                    <textarea value={solverSystemPrompt} onChange={(e) => setSolverSystemPrompt(e.target.value)} className="w-full min-h-24 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Solver User Prompt</label>
                    <textarea value={solverUserPrompt} onChange={(e) => setSolverUserPrompt(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Reviewer System Prompt</label>
                    <textarea value={reviewerSystemPrompt} onChange={(e) => setReviewerSystemPrompt(e.target.value)} className="w-full min-h-24 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Reviewer User Prompt</label>
                    <textarea value={reviewerUserPrompt} onChange={(e) => setReviewerUserPrompt(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Formatter System Prompt</label>
                    <textarea value={formatterSystemPrompt} onChange={(e) => setFormatterSystemPrompt(e.target.value)} className="w-full min-h-24 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Formatter User Prompt</label>
                    <textarea value={formatterUserPrompt} onChange={(e) => setFormatterUserPrompt(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-amber-600 border-b pb-2">Fallback 模型列表</h3>
                <p className="text-xs text-gray-500">每行一个模型名。节点列表为空时会回退到全局列表。</p>
                <div className="grid grid-cols-1 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">全局 Fallback</label>
                    <textarea value={globalFallbackText} onChange={(e) => setGlobalFallbackText(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-amber-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Solver 节点 Fallback</label>
                    <textarea value={solverFallbackText} onChange={(e) => setSolverFallbackText(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-amber-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Reviewer 节点 Fallback</label>
                    <textarea value={reviewerFallbackText} onChange={(e) => setReviewerFallbackText(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-amber-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Formatter 节点 Fallback</label>
                    <textarea value={formatterFallbackText} onChange={(e) => setFormatterFallbackText(e.target.value)} className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono focus:ring-2 focus:ring-amber-500 outline-none" />
                  </div>
                </div>
              </div>

              {/* Solver 配置 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-blue-600 border-b pb-2">Solver (解题) 节点</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                    <input type="text" value={solverConfig.model_name} onChange={e => setSolverConfig({ ...solverConfig, model_name: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                    <input type="number" value={solverConfig.max_tokens || ''} onChange={e => setSolverConfig({ ...solverConfig, max_tokens: parseMaxTokens(e.target.value) })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                    <input type="text" value={solverConfig.base_url} onChange={e => setSolverConfig({ ...solverConfig, base_url: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input type="password" value={solverConfig.api_key} onChange={e => setSolverConfig({ ...solverConfig, api_key: e.target.value })} placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                  </div>
                </div>
              </div>

              {/* Reviewer 配置 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-purple-600 border-b pb-2">Reviewer (审查) 节点</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                    <input type="text" value={reviewerConfig.model_name} onChange={e => setReviewerConfig({ ...reviewerConfig, model_name: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                    <input type="number" value={reviewerConfig.max_tokens || ''} onChange={e => setReviewerConfig({ ...reviewerConfig, max_tokens: parseMaxTokens(e.target.value) })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                    <input type="text" value={reviewerConfig.base_url} onChange={e => setReviewerConfig({ ...reviewerConfig, base_url: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input type="password" value={reviewerConfig.api_key} onChange={e => setReviewerConfig({ ...reviewerConfig, api_key: e.target.value })} placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                  </div>
                </div>
              </div>

              {/* Formatter 配置 */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-green-600 border-b pb-2">Formatter (排版) 节点</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                    <input type="text" value={formatterConfig.model_name} onChange={e => setFormatterConfig({ ...formatterConfig, model_name: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                    <input type="number" value={formatterConfig.max_tokens || ''} onChange={e => setFormatterConfig({ ...formatterConfig, max_tokens: parseMaxTokens(e.target.value) })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
                    <input type="text" value={formatterConfig.base_url} onChange={e => setFormatterConfig({ ...formatterConfig, base_url: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-700 mb-1">API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input type="password" value={formatterConfig.api_key} onChange={e => setFormatterConfig({ ...formatterConfig, api_key: e.target.value })} placeholder="sk-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                  </div>
                </div>
              </div>
            </div>

            <div className="p-4 border-t bg-gray-50 flex justify-end gap-3">
              <button onClick={tryCloseSettings} className="px-4 py-2 text-gray-600 hover:bg-gray-200 rounded-lg transition-colors font-medium">取消</button>
              <button onClick={saveSettings} disabled={settingsSaving} className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium shadow-sm disabled:opacity-50">
                {settingsSaving ? '保存中...' : '保存配置'}
              </button>
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
      className={`px-3 py-1.5 rounded-lg border text-sm font-mono transition-colors ${isActive
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
  const [customDraftInput, setCustomDraftInput] = useState('')
  const [selectedNodes, setSelectedNodes] = useState<WorkflowNode[]>([...WORKFLOW_NODE_ORDER])
  const [streamedContent, setStreamedContent] = useState('')
  const [currentNode, setCurrentNode] = useState('')

  type ManualAction = 'resume' | 'skip_review' | 'fail' | 'custom_run'
  type ManualMutationPayload = {
    action: ManualAction
    draft?: string
    entryPoint?: WorkflowNode
    targetNodes?: WorkflowNode[]
  }

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
    setSelectedNodes([...WORKFLOW_NODE_ORDER]);
    setCustomDraftInput('');
  }, [taskId]);

  useEffect(() => {
    if (!task) return
    try {
      const parsedHistory = task.history ? JSON.parse(task.history) : {}
      if (typeof parsedHistory?.draft_solution === 'string') {
        setCustomDraftInput(parsedHistory.draft_solution)
      }
      const historyTargetNodes = Array.isArray(parsedHistory?.target_nodes)
        ? parsedHistory.target_nodes.filter((node: unknown): node is WorkflowNode => (
          typeof node === 'string' && WORKFLOW_NODE_ORDER.includes(node as WorkflowNode)
        ))
        : []
      if (historyTargetNodes.length > 0) {
        setSelectedNodes(historyTargetNodes)
      }
    } catch {
      // 历史字段非 JSON 时保持默认值，不中断详情页渲染
    }
  }, [task]);

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
    mutationFn: ({ action, draft, entryPoint, targetNodes }: ManualMutationPayload) => {
      const payload: Record<string, unknown> = { action, draft_solution: draft }
      if (action === 'resume' || action === 'skip_review' || action === 'custom_run') {
        Object.assign(payload, getLatestRetryConfigs())
      }
      if (action === 'custom_run') {
        payload.entry_point = entryPoint
        payload.target_nodes = targetNodes
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

  let history: Record<string, unknown> = {}
  try {
    history = task.history ? JSON.parse(task.history) : {}
  } catch {
    history = {}
  }

  let tokens: Record<string, unknown> = {}
  try {
    tokens = task.token_usage ? JSON.parse(task.token_usage) : {}
  } catch {
    tokens = {}
  }

  const orderedSelectedNodes = WORKFLOW_NODE_ORDER.filter((node) => selectedNodes.includes(node))
  const selectedNodeIndices = orderedSelectedNodes.map((node) => WORKFLOW_NODE_ORDER.indexOf(node))
  const hasContiguousSelection = selectedNodeIndices.every((idx, i) => i === 0 || idx - selectedNodeIndices[i - 1] === 1)
  const customEntryPoint = orderedSelectedNodes.length > 0 ? orderedSelectedNodes[0] : undefined
  const customNeedsDraft = customEntryPoint === 'reviewer' || customEntryPoint === 'formatter'
  const customDraftValue = customDraftInput.trim().length > 0
    ? customDraftInput.trim()
    : (typeof history.draft_solution === 'string' ? history.draft_solution : '')
  const canSubmitCustomRun = orderedSelectedNodes.length > 0
    && hasContiguousSelection
    && (!customNeedsDraft || customDraftValue.trim().length > 0)
    && !manualMutation.isPending
  const customRunBlockedReason = orderedSelectedNodes.length === 0
    ? '请至少选择一个节点。'
    : (!hasContiguousSelection
      ? '工作流节点必须连续，不能跳选。'
      : (customNeedsDraft && customDraftValue.trim().length === 0
        ? '从 Reviewer 或 Formatter 开始时，草稿文本为必填。'
        : ''))

  const toggleNodeSelection = (node: WorkflowNode) => {
    setSelectedNodes((prev) => {
      if (prev.includes(node)) {
        return prev.filter((item) => item !== node)
      }
      return [...prev, node]
    })
  }

  const handleCustomRun = () => {
    if (!canSubmitCustomRun || !customEntryPoint) return
    manualMutation.mutate({
      action: 'custom_run',
      draft: customDraftValue,
      entryPoint: customEntryPoint,
      targetNodes: orderedSelectedNodes
    })
  }

  const isTerminalState = ['completed', 'failed', 'manual', 'cancelled'].includes(task.state)
  const historyDraftSolution = typeof history.draft_solution === 'string' ? history.draft_solution : ''
  const historyReviewFeedback = typeof history.review_feedback === 'string' ? history.review_feedback : ''
  const totalTokens = typeof tokens.total_tokens === 'number' ? tokens.total_tokens : 0
  const shouldShowTaskError = Boolean(task.error_code) && ['failed', 'manual', 'cancelled'].includes(task.state)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 bg-white p-6 rounded-xl shadow-sm border">
      {/* Left: Original Image & Meta */}
      <div className="space-y-4 border-r pr-8">
        <div className="flex items-center justify-between">
          <h3 className="text-xl font-semibold">Task: <span className="text-sm font-mono text-gray-500">{task.task_id}</span></h3>
          <span className={`px-3 py-1 rounded-full text-xs font-bold uppercase ${task.state === 'completed' ? 'bg-green-100 text-green-700' :
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
          <div><strong>Total Tokens:</strong> {totalTokens}</div>
        </div>

        <div className="relative border rounded bg-gray-50 p-2 h-64 flex items-center justify-center group overflow-hidden cursor-pointer" onClick={() => onPreview(task.image_url)}>
          <img src={task.image_url} alt="Task target" className="max-h-full object-contain" />
          <div className="absolute inset-0 bg-black/10 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
            <Maximize2 className="text-gray-700 bg-white/80 p-2 rounded-full w-10 h-10 shadow-sm" />
          </div>
        </div>

        {shouldShowTaskError && (
          <div className="bg-red-50 text-red-700 p-4 rounded text-sm font-mono border border-red-100">
            <strong>Error:</strong> {task.error_code}
          </div>
        )}
      </div>

      {/* Right: Agent Outputs & Interventions */}
      <div className="space-y-6">
        {isTerminalState && (
          <div className="space-y-4 border rounded-lg p-4 bg-blue-50/40 border-blue-100">
            <div>
              <h3 className="font-semibold text-blue-700">自定义工作流执行</h3>
              <p className="text-xs text-gray-600 mt-1">仅针对当前任务生效，选择连续节点后可从指定入口重跑。</p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {WORKFLOW_NODE_ORDER.map((node, idx) => (
                <div key={node} className="flex items-center gap-2">
                  <label className="inline-flex items-center gap-2 text-sm px-3 py-1.5 rounded border bg-white border-gray-200">
                    <input
                      type="checkbox"
                      checked={selectedNodes.includes(node)}
                      onChange={() => toggleNodeSelection(node)}
                    />
                    <span className="font-medium">
                      {node === 'solver' ? 'Solver 解题' : node === 'reviewer' ? 'Reviewer 审查' : 'Formatter 排版'}
                    </span>
                  </label>
                  {idx < WORKFLOW_NODE_ORDER.length - 1 && <span className="text-gray-400">-&gt;</span>}
                </div>
              ))}
            </div>

            <div className="text-xs text-gray-600 bg-white rounded border px-3 py-2">
              <div>入口节点: <span className="font-mono">{customEntryPoint || '-'}</span></div>
              <div>目标节点: <span className="font-mono">{orderedSelectedNodes.join(', ') || '-'}</span></div>
            </div>

            {customNeedsDraft && (
              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">草稿文本（必填）</label>
                <textarea
                  className="w-full h-32 p-3 border rounded text-sm font-mono bg-white focus:ring-2 focus:ring-blue-500 outline-none resize-none"
                  value={customDraftInput}
                  onChange={(e) => setCustomDraftInput(e.target.value)}
                  placeholder="从 Reviewer/Formatter 开始执行时，请输入可用草稿文本"
                />
              </div>
            )}

            {customRunBlockedReason && (
              <div className="text-xs text-red-600 bg-red-50 border border-red-100 px-3 py-2 rounded">
                {customRunBlockedReason}
              </div>
            )}

            <div className="flex justify-end">
              <button
                onClick={handleCustomRun}
                disabled={!canSubmitCustomRun}
                className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                title={customRunBlockedReason || '执行当前自定义工作流'}
              >
                {manualMutation.isPending ? '执行中...' : '执行自定义工作流'}
              </button>
            </div>
          </div>
        )}

        {task.state === 'manual' || task.state === 'failed' ? (
          <div className="space-y-4">
            <h3 className="font-semibold text-red-600 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-red-600 inline-block animate-pulse"></span>
              Manual Intervention Required
            </h3>
            <div className="text-sm text-gray-700 bg-red-50 p-3 rounded border border-red-100">
              <span className="font-bold">Reviewer Feedback:</span> {historyReviewFeedback || 'System Error'}
            </div>
            <textarea
              className="w-full h-[280px] p-4 border rounded font-mono text-sm bg-gray-50 focus:bg-white focus:ring-2 focus:ring-blue-500 outline-none resize-none"
              defaultValue={historyDraftSolution}
              onChange={(e) => setDraftInput(e.target.value)}
              placeholder="Edit the draft solution here..."
            />
            <div className="flex gap-4 pt-2">
              <button
                onClick={() => manualMutation.mutate({ action: 'resume', draft: draftInput || historyDraftSolution })}
                className="bg-green-600 text-white px-6 py-2.5 rounded font-medium hover:bg-green-700 transition-colors shadow-sm"
              >
                Approve & Resume
              </button>
              <button
                onClick={() => manualMutation.mutate({ action: 'skip_review', draft: draftInput || historyDraftSolution })}
                className="bg-blue-600 text-white px-6 py-2.5 rounded font-medium hover:bg-blue-700 transition-colors shadow-sm"
              >
                Skip Review & Format
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
                  {currentNode ? `${task.state} · ${currentNode}` : task.state}
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

function AdminPanel({
  onBack,
  initialTaskId,
}: {
  onBack: () => void;
  initialTaskId?: string | null;
}) {
  const [searchTaskId, setSearchTaskId] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(initialTaskId || null)
  const [selectedExportIds, setSelectedExportIds] = useState<string[]>([])
  const [draggingExportId, setDraggingExportId] = useState<string | null>(null)
  const [hoveredExportId, setHoveredExportId] = useState<string | null>(null)
  const [hoverPreviewPos, setHoverPreviewPos] = useState<{ x: number; y: number } | null>(null)
  const [isExporting, setIsExporting] = useState(false)
  const [editState, setEditState] = useState('')
  const [editHistory, setEditHistory] = useState('')
  const [editFinalResult, setEditFinalResult] = useState('')
  const [editErrorCode, setEditErrorCode] = useState('')
  const [editManualOperator, setEditManualOperator] = useState('')
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const [isCustomRunPanelOpen, setIsCustomRunPanelOpen] = useState(false)
  const [customRunNodes, setCustomRunNodes] = useState<WorkflowNode[]>([...WORKFLOW_NODE_ORDER])
  const [customRunDraft, setCustomRunDraft] = useState('')

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
    setCustomRunNodes([...WORKFLOW_NODE_ORDER])
    setCustomRunDraft('')

    try {
      const parsedHistory = selectedTask.history ? JSON.parse(selectedTask.history) : {}
      const historyTargetNodes = Array.isArray(parsedHistory?.target_nodes)
        ? parsedHistory.target_nodes.filter((node: unknown): node is WorkflowNode => (
          typeof node === 'string' && WORKFLOW_NODE_ORDER.includes(node as WorkflowNode)
        ))
        : []
      if (historyTargetNodes.length > 0) {
        setCustomRunNodes(historyTargetNodes)
      }
      if (typeof parsedHistory?.draft_solution === 'string') {
        setCustomRunDraft(parsedHistory.draft_solution)
      }
    } catch {
      // 历史字段无效时保持默认值，避免 Admin 面板不可用
    }
  }, [selectedTask])

  useEffect(() => {
    setIsCustomRunPanelOpen(false)
  }, [selectedTaskId])

  useEffect(() => {
    if (!selectedTaskId && listData && listData.items.length > 0) {
      setSelectedTaskId(listData.items[0].task_id)
    }
  }, [selectedTaskId, listData])

  useEffect(() => {
    if (initialTaskId && initialTaskId !== selectedTaskId) {
      setSelectedTaskId(initialTaskId)
    }
  }, [initialTaskId, selectedTaskId])

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

  const customRunMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTaskId || !selectedTask) throw new Error('请先选择任务')

      const allowedStates = ['manual', 'failed', 'completed', 'cancelled']
      if (!allowedStates.includes(selectedTask.state)) {
        throw new Error('仅 manual/failed/completed/cancelled 任务可自定义执行')
      }

      const orderedNodes = WORKFLOW_NODE_ORDER.filter((node) => customRunNodes.includes(node))
      if (orderedNodes.length === 0) throw new Error('请至少选择一个节点')

      const nodeIndices = orderedNodes.map((node) => WORKFLOW_NODE_ORDER.indexOf(node))
      const isContiguous = nodeIndices.every((idx, i) => i === 0 || idx - nodeIndices[i - 1] === 1)
      if (!isContiguous) throw new Error('节点选择必须连续，不能跳选')

      const entryPoint = orderedNodes[0]

      let parsedHistory: Record<string, unknown> = {}
      const trimmedHistory = (editHistory || '').trim()
      if (trimmedHistory) {
        try {
          parsedHistory = JSON.parse(trimmedHistory)
        } catch {
          throw new Error('history 不是合法 JSON，请先修正后再执行 custom_run')
        }
      }

      const historyDraft = typeof parsedHistory.draft_solution === 'string' ? parsedHistory.draft_solution : ''
      const draftSolution = customRunDraft.trim().length > 0 ? customRunDraft.trim() : historyDraft
      if ((entryPoint === 'reviewer' || entryPoint === 'formatter') && draftSolution.trim().length === 0) {
        throw new Error('从 reviewer/formatter 开始时，draft_solution 为必填')
      }

      await api.post(`/api/tasks/${selectedTaskId}/manual`, {
        action: 'custom_run',
        draft_solution: draftSolution,
        entry_point: entryPoint,
        target_nodes: orderedNodes,
        ...getLatestRetryConfigs()
      })
    },
    onSuccess: () => {
      if (selectedTaskId) {
        persistTaskForDashboard(selectedTaskId)
      }
      setOperationMessage(`已触发自定义执行：${selectedTaskId}`)
      queryClient.invalidateQueries({ queryKey: ['admin-task-detail', selectedTaskId] })
      queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
      queryClient.invalidateQueries({ queryKey: ['task', selectedTaskId] })
    },
    onError: (error: unknown) => {
      setOperationMessage(getErrorMessage(error, '自定义执行失败'))
    }
  })

  const handleDelete = (taskId: string) => {
    const confirmed = window.confirm(`确认删除任务 ${taskId} 吗？`)
    if (!confirmed) return
    deleteMutation.mutate(taskId)
  }
  const canRetrySelectedTask = !!selectedTask && ['manual', 'failed'].includes(selectedTask.state)
  const canCustomRunSelectedTask = !!selectedTask && ['manual', 'failed', 'completed', 'cancelled'].includes(selectedTask.state)

  const orderedCustomRunNodes = WORKFLOW_NODE_ORDER.filter((node) => customRunNodes.includes(node))
  const customRunEntryPoint = orderedCustomRunNodes.length > 0 ? orderedCustomRunNodes[0] : undefined
  const customRunIndices = orderedCustomRunNodes.map((node) => WORKFLOW_NODE_ORDER.indexOf(node))
  const customRunContiguous = customRunIndices.every((idx, i) => i === 0 || idx - customRunIndices[i - 1] === 1)
  const customRunNeedDraft = customRunEntryPoint === 'reviewer' || customRunEntryPoint === 'formatter'

  let customRunHistoryDraft = ''
  try {
    const parsedHistory = editHistory ? JSON.parse(editHistory) : {}
    customRunHistoryDraft = typeof parsedHistory?.draft_solution === 'string' ? parsedHistory.draft_solution : ''
  } catch {
    customRunHistoryDraft = ''
  }

  const customRunDraftValue = customRunDraft.trim().length > 0 ? customRunDraft.trim() : customRunHistoryDraft
  const canSubmitCustomRun = canCustomRunSelectedTask
    && orderedCustomRunNodes.length > 0
    && customRunContiguous
    && (!customRunNeedDraft || customRunDraftValue.trim().length > 0)
    && !customRunMutation.isPending

  const customRunHint = !canCustomRunSelectedTask
    ? '当前任务状态不支持自定义执行。'
    : (orderedCustomRunNodes.length === 0
      ? '请至少勾选一个节点。'
      : (!customRunContiguous
        ? '节点必须连续，不能跳选。'
        : (customRunNeedDraft && customRunDraftValue.trim().length === 0
          ? '从 Reviewer/Formatter 开始时草稿文本必填。'
          : '')))

  const toggleExportSelection = (taskId: string) => {
    setSelectedExportIds((prev) => (
      prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
    ))
  }

  const getTaskPreviewText = (task: AdminTask) => {
    const question = (task.question_preview || '').trim()
    if (question) return question

    const full = (task.final_result || '').trim()
    if (!full) return '暂无题目预览'

    const splitIndex = (() => {
      const solveIndex = full.indexOf('【正解】')
      const analysisIndex = full.indexOf('【解析】')
      if (solveIndex >= 0 && analysisIndex >= 0) return Math.min(solveIndex, analysisIndex)
      if (solveIndex >= 0) return solveIndex
      return analysisIndex
    })()

    return (splitIndex >= 0 ? full.slice(0, splitIndex) : full).trim() || '暂无题目预览'
  }

  const getTaskPreviewSnippet = (task: AdminTask) => {
    const text = getTaskPreviewText(task).replace(/\s+/g, ' ').trim()
    return text.length > 120 ? `${text.slice(0, 120)}...` : text
  }

  const toggleSelectAllInList = () => {
    const ids = (listData?.items || []).map((item) => item.task_id)
    if (ids.length === 0) return
    const allSelected = ids.every((id) => selectedExportIds.includes(id))
    setSelectedExportIds(allSelected ? [] : ids)
  }

  const selectedExportTasks = useMemo(() => {
    const taskMap = new Map((listData?.items || []).map((item) => [item.task_id, item]))
    return selectedExportIds
      .map((taskId) => taskMap.get(taskId))
      .filter((task): task is AdminTask => !!task)
  }, [listData, selectedExportIds])

  const reorderExportIds = (dragId: string, targetId: string) => {
    if (dragId === targetId) return
    setSelectedExportIds((prev) => {
      const fromIndex = prev.indexOf(dragId)
      const toIndex = prev.indexOf(targetId)
      if (fromIndex < 0 || toIndex < 0) return prev
      const next = [...prev]
      const [moved] = next.splice(fromIndex, 1)
      next.splice(toIndex, 0, moved)
      return next
    })
  }

  const handleExportDragStart = (taskId: string) => {
    setDraggingExportId(taskId)
  }

  const handleExportDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
  }

  const handleExportDrop = (targetTaskId: string) => {
    if (!draggingExportId) return
    reorderExportIds(draggingExportId, targetTaskId)
    setDraggingExportId(null)
  }

  const handleExportDragEnd = () => {
    setDraggingExportId(null)
  }

  const handleExportItemMouseEnter = (taskId: string, event: MouseEvent<HTMLDivElement>) => {
    setHoveredExportId(taskId)
    setHoverPreviewPos({ x: event.clientX, y: event.clientY })
  }

  const handleExportItemMouseMove = (event: MouseEvent<HTMLDivElement>) => {
    setHoverPreviewPos({ x: event.clientX, y: event.clientY })
  }

  const handleExportItemMouseLeave = (taskId: string) => {
    setHoveredExportId((prev) => (prev === taskId ? null : prev))
    setHoverPreviewPos(null)
  }

  const hoveredExportTask = useMemo(() => {
    if (!hoveredExportId) return null
    return selectedExportTasks.find((item) => item.task_id === hoveredExportId) || null
  }, [hoveredExportId, selectedExportTasks])

  const exportFinalResults = async () => {
    if (selectedExportIds.length === 0) {
      setOperationMessage('请先勾选任务')
      return
    }
    setIsExporting(true)
    try {
      const response = await api.post(
        '/api/admin/tasks/export/md',
        { task_ids: selectedExportIds },
        { responseType: 'blob' }
      )
      const blob = new Blob([response.data], { type: 'text/markdown;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
      link.href = url
      link.download = `final_results_${timestamp}.md`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      setOperationMessage(`MD 导出成功（已按自定义顺序拆分题目与答案）`)
    } catch (error: unknown) {
      setOperationMessage(getErrorMessage(error, 'MD 导出失败'))
    } finally {
      setIsExporting(false)
    }
  }

  const exportFinalResultsDocx = async () => {
    if (selectedExportIds.length === 0) {
      setOperationMessage('请先勾选任务')
      return
    }
    setIsExporting(true)
    try {
      const response = await api.post(
        '/api/admin/tasks/export/docx',
        { task_ids: selectedExportIds },
        { responseType: 'blob' }
      )
      const blob = new Blob([response.data], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
      link.href = url
      link.download = `final_results_${timestamp}.docx`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      setOperationMessage('DOCX 导出成功（已按自定义顺序拆分题目与答案）')
    } catch (error: unknown) {
      setOperationMessage(getErrorMessage(error, 'DOCX 导出失败（可能后端缺少 pandoc）'))
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
                <div className="flex bg-indigo-600 rounded">
                  <button
                    onClick={exportFinalResults}
                    disabled={isExporting || selectedExportIds.length === 0}
                    className="inline-flex items-center gap-1 text-xs px-2.5 py-1 text-white border-r border-indigo-700 disabled:opacity-50 hover:bg-indigo-700 rounded-l"
                  >
                    <Download size={12} />
                    {isExporting ? '导出中...' : `导出 MD(${selectedExportIds.length})`}
                  </button>
                  <button
                    onClick={exportFinalResultsDocx}
                    disabled={isExporting || selectedExportIds.length === 0}
                    className="inline-flex items-center gap-1 text-xs px-2.5 py-1 text-white disabled:opacity-50 hover:bg-indigo-700 rounded-r"
                  >
                    <Download size={12} />
                    导出 DOCX
                  </button>
                </div>
              </div>
            </div>
            {selectedExportTasks.length > 0 && (
              <div className="mb-3 p-2 rounded border bg-gray-50">
                <div className="text-xs text-gray-600 mb-2">拖拽调整导出顺序（题目区和答案区使用同一顺序）</div>
                <div className="space-y-1 max-h-36 overflow-y-auto">
                  {selectedExportTasks.map((task, index) => (
                    <div
                      key={`export-order-${task.task_id}`}
                      draggable
                      onDragStart={() => handleExportDragStart(task.task_id)}
                      onDragOver={handleExportDragOver}
                      onDrop={() => handleExportDrop(task.task_id)}
                      onDragEnd={handleExportDragEnd}
                      onMouseEnter={(event) => handleExportItemMouseEnter(task.task_id, event)}
                      onMouseMove={handleExportItemMouseMove}
                      onMouseLeave={() => handleExportItemMouseLeave(task.task_id)}
                      className={`relative text-xs px-2 py-1.5 rounded border bg-white cursor-move ${draggingExportId === task.task_id ? 'opacity-60 border-indigo-300' : 'border-gray-200'}`}
                    >
                      {index + 1}. {task.task_id}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {listLoading && <div className="text-sm text-gray-500">加载中...</div>}
              {!listLoading && (listData?.items || []).map((task) => (
                <div
                  key={task.task_id}
                  className={`w-full text-left p-3 rounded-lg border transition-colors ${selectedTaskId === task.task_id ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 hover:bg-gray-50'
                    }`}
                >
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={selectedExportIds.includes(task.task_id)}
                      onChange={() => toggleExportSelection(task.task_id)}
                      className="mt-0.5"
                    />
                    <button
                      onClick={() => setSelectedTaskId(task.task_id)}
                      className="flex-1 text-left"
                    >
                      <div className="text-xs font-mono text-gray-700 truncate">{task.task_id}</div>
                      <div className="text-xs text-gray-500 mt-1">{task.state} · retry {task.retry_count}</div>
                      <div className="text-[11px] text-gray-400 mt-1 truncate">题目预览：{getTaskPreviewSnippet(task)}</div>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {hoveredExportTask && hoverPreviewPos && (
          <div
            className="fixed w-72 rounded-lg border border-gray-200 bg-white shadow-lg p-3 z-50 pointer-events-none"
            style={{ left: hoverPreviewPos.x + 14, top: hoverPreviewPos.y + 12 }}
          >
            <div className="text-[11px] text-gray-500 mb-1">题目预览</div>
            <div className="text-xs text-gray-700 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
              {getTaskPreviewText(hoveredExportTask)}
            </div>
          </div>
        )}

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

              <div className="space-y-3 border rounded-lg p-4 bg-indigo-50/40 border-indigo-100">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-indigo-700">自定义节点执行 (custom_run)</h3>
                    {!isCustomRunPanelOpen && (
                      <p className="text-xs text-gray-600 mt-1">
                        入口节点: <span className="font-mono">{customRunEntryPoint || '-'}</span>
                        {' · '}目标节点: <span className="font-mono">{orderedCustomRunNodes.join(', ') || '-'}</span>
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsCustomRunPanelOpen((prev) => !prev)}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-indigo-700 border border-indigo-200 rounded hover:bg-indigo-100"
                  >
                    {isCustomRunPanelOpen ? (
                      <>
                        收起
                        <ChevronUp size={14} />
                      </>
                    ) : (
                      <>
                        展开
                        <ChevronDown size={14} />
                      </>
                    )}
                  </button>
                </div>

                {isCustomRunPanelOpen && (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      {WORKFLOW_NODE_ORDER.map((node, idx) => (
                        <div key={`admin-${node}`} className="flex items-center gap-2">
                          <label className="inline-flex items-center gap-2 text-xs px-2.5 py-1.5 rounded border bg-white border-gray-200">
                            <input
                              type="checkbox"
                              checked={customRunNodes.includes(node)}
                              onChange={() => {
                                setCustomRunNodes((prev) => (
                                  prev.includes(node)
                                    ? prev.filter((item) => item !== node)
                                    : [...prev, node]
                                ))
                              }}
                            />
                            <span>{node}</span>
                          </label>
                          {idx < WORKFLOW_NODE_ORDER.length - 1 && <span className="text-gray-400 text-xs">-&gt;</span>}
                        </div>
                      ))}
                    </div>

                    <div className="text-xs text-gray-600 bg-white rounded border px-3 py-2">
                      <div>入口节点: <span className="font-mono">{customRunEntryPoint || '-'}</span></div>
                      <div>目标节点: <span className="font-mono">{orderedCustomRunNodes.join(', ') || '-'}</span></div>
                    </div>

                    {customRunNeedDraft && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-gray-700">草稿文本（必填）</label>
                        <textarea
                          value={customRunDraft}
                          onChange={(e) => setCustomRunDraft(e.target.value)}
                          className="w-full min-h-24 border rounded-lg px-3 py-2 text-xs font-mono bg-white"
                          placeholder="从 reviewer/formatter 起跑时，输入或修订 draft_solution"
                        />
                      </div>
                    )}

                    {customRunHint && (
                      <div className="text-xs text-red-600 bg-red-50 border border-red-100 px-3 py-2 rounded">
                        {customRunHint}
                      </div>
                    )}

                    <div className="flex justify-end">
                      <button
                        onClick={() => customRunMutation.mutate()}
                        disabled={!canSubmitCustomRun}
                        className="inline-flex items-center gap-2 px-3 py-1.5 text-xs text-indigo-700 border border-indigo-200 rounded hover:bg-indigo-50 disabled:opacity-50"
                        title={customRunHint || '按勾选节点执行'}
                      >
                        {customRunMutation.isPending ? '执行中...' : '执行 custom_run'}
                      </button>
                    </div>
                  </>
                )}
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
                <label className="text-sm font-medium text-gray-700">image_url</label>
                <img src={selectedTask.image_url} alt="task" className="max-h-60 border rounded bg-gray-50 object-contain" />
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">final_result</label>
                <textarea value={editFinalResult} onChange={(e) => setEditFinalResult(e.target.value)} className="w-full min-h-60 border rounded-lg px-3 py-2 text-xs font-mono" />
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-700">question_preview</label>
                  <textarea
                    value={selectedTask.question_preview || ''}
                    readOnly
                    className="w-full min-h-32 border rounded-lg px-3 py-2 text-xs font-mono bg-gray-50"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-700">answer_preview</label>
                  <textarea
                    value={selectedTask.answer_preview || ''}
                    readOnly
                    className="w-full min-h-32 border rounded-lg px-3 py-2 text-xs font-mono bg-gray-50"
                  />
                </div>
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
                <label className="text-sm font-medium text-gray-700">token_usage（只读）</label>
                <pre className="w-full min-h-20 border rounded-lg px-3 py-2 text-xs font-mono bg-gray-50 overflow-auto whitespace-pre-wrap">{selectedTask.token_usage || ''}</pre>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-700">history</label>
                <textarea value={editHistory} onChange={(e) => setEditHistory(e.target.value)} className="w-full min-h-36 border rounded-lg px-3 py-2 text-xs font-mono" />
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

function PaperBuilder({
  onOpenAdminTask,
}: {
  onOpenAdminTask: (taskId: string) => void;
}) {
  const [groups, setGroups] = useState<PaperGroup[]>([])
  const [draftName, setDraftName] = useState('默认排版草稿')
  const [paperSubject, setPaperSubject] = useState('')
  const [paperTitle, setPaperTitle] = useState('')
  const [groupNameInput, setGroupNameInput] = useState('')
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const [isExporting, setIsExporting] = useState(false)
  const [isSavingDraft, setIsSavingDraft] = useState(false)
  const [isLoadingDraft, setIsLoadingDraft] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null)
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([])
  const [draggingTaskId, setDraggingTaskId] = useState<string | null>(null)
  const [dragOverGroupId, setDragOverGroupId] = useState<string | null>(null)
  const [dragOverTaskKey, setDragOverTaskKey] = useState<string | null>(null)
  const [hasInitializedDraft, setHasInitializedDraft] = useState(false)

  const { data: listData, isLoading } = useQuery({
    queryKey: ['builder-admin-tasks'],
    queryFn: () => api.get<AdminTaskListResponse>('/api/admin/tasks', {
      params: { page: 1, page_size: 200 }
    }).then((res) => res.data),
    refetchInterval: 5000,
  })

  const tasks = listData?.items || []
  const taskMap = useMemo(() => new Map(tasks.map((item) => [item.task_id, item])), [tasks])

  const groupedTaskIdSet = useMemo(() => {
    const ids = new Set<string>()
    groups.forEach((group) => {
      group.taskIds.forEach((taskId) => ids.add(taskId))
    })
    return ids
  }, [groups])

  const normalizeGroups = (rawGroups: PaperGroup[]): PaperGroup[] => {
    const normalized: PaperGroup[] = []
    const seenGroupIds = new Set<string>()
    const globalTaskIds = new Set<string>()

    rawGroups.forEach((group, index) => {
      const rawId = typeof group.id === 'string' ? group.id.trim() : ''
      const id = rawId.length > 0 ? rawId : `group-${Date.now()}-${index}`
      if (seenGroupIds.has(id)) return
      seenGroupIds.add(id)
      const name = (group.name || '').trim() || `题型${index + 1}`

      const taskIds: string[] = []
      group.taskIds.forEach((rawTaskId) => {
        const taskId = (rawTaskId || '').trim()
        if (!taskId || globalTaskIds.has(taskId)) return
        globalTaskIds.add(taskId)
        taskIds.push(taskId)
      })

      normalized.push({ id, name, taskIds })
    })

    return normalized
  }

  const saveLocalDraft = (
    name: string,
    subject: string,
    title: string,
    draftGroups: PaperGroup[],
  ) => {
    localStorage.setItem(PAPER_BUILDER_LOCAL_DRAFT_KEY, JSON.stringify({
      name,
      paper_subject: subject,
      paper_title: title,
      groups: draftGroups,
      saved_at: new Date().toISOString(),
    }))
  }

  const loadLocalDraft = () => {
    const raw = localStorage.getItem(PAPER_BUILDER_LOCAL_DRAFT_KEY)
    if (!raw) return null
    try {
      const parsed = JSON.parse(raw) as {
        name?: string;
        paper_subject?: string;
        paper_title?: string;
        groups?: PaperGroup[];
        saved_at?: string;
      }
      const loadedGroups = Array.isArray(parsed.groups) ? normalizeGroups(parsed.groups) : []
      return {
        name: (parsed.name || '默认排版草稿').trim() || '默认排版草稿',
        paperSubject: (parsed.paper_subject || '').trim(),
        paperTitle: (parsed.paper_title || '').trim(),
        groups: loadedGroups,
        savedAt: parsed.saved_at || null,
      }
    } catch {
      return null
    }
  }

  const loadRemoteDraft = async () => {
    try {
      const remote = await api.get<PaperBuilderDraftResponse>(`/api/paper-builder/drafts/${PAPER_BUILDER_REMOTE_DRAFT_ID}`).then((res) => res.data)
      const loadedGroups = normalizeGroups((remote.groups || []).map((group) => ({
        id: group.group_id,
        name: group.group_name,
        taskIds: group.task_ids,
      })))
      setDraftName((remote.name || '默认排版草稿').trim() || '默认排版草稿')
      setPaperSubject((remote.paper_subject || '').trim())
      setPaperTitle((remote.paper_title || '').trim())
      setGroups(loadedGroups)
      setLastSavedAt(remote.updated_at || null)
      saveLocalDraft(
        (remote.name || '默认排版草稿').trim() || '默认排版草稿',
        (remote.paper_subject || '').trim(),
        (remote.paper_title || '').trim(),
        loadedGroups,
      )
      return true
    } catch {
      return false
    }
  }

  useEffect(() => {
    let active = true
    const bootstrap = async () => {
      setIsLoadingDraft(true)
      const local = loadLocalDraft()
      if (active && local) {
        setDraftName(local.name)
        setPaperSubject(local.paperSubject)
        setPaperTitle(local.paperTitle)
        setGroups(local.groups)
        setLastSavedAt(local.savedAt)
      }

      const loadedRemote = await loadRemoteDraft()
      if (active && !loadedRemote && local) {
        setLastSavedAt(local.savedAt)
      }

      if (active) {
        setIsLoadingDraft(false)
        setHasInitializedDraft(true)
      }
    }

    void bootstrap()
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!hasInitializedDraft) return
    const safeGroups = normalizeGroups(groups)
    saveLocalDraft(draftName, paperSubject, paperTitle, safeGroups)

    const timer = window.setTimeout(async () => {
      setIsSavingDraft(true)
      try {
        const payload = {
          name: (draftName || '默认排版草稿').trim() || '默认排版草稿',
          paper_subject: paperSubject.trim(),
          paper_title: paperTitle.trim(),
          groups: safeGroups.map((group) => ({
            group_id: group.id,
            group_name: group.name,
            task_ids: group.taskIds,
          })),
        }
        const remote = await api.put<PaperBuilderDraftResponse>(`/api/paper-builder/drafts/${PAPER_BUILDER_REMOTE_DRAFT_ID}`, payload).then((res) => res.data)
        setLastSavedAt(remote.updated_at || new Date().toISOString())
      } catch {
        // 后端保存失败时保留本地草稿，不中断编辑
      } finally {
        setIsSavingDraft(false)
      }
    }, 800)

    return () => {
      window.clearTimeout(timer)
    }
  }, [draftName, paperSubject, paperTitle, groups, hasInitializedDraft])

  useEffect(() => {
    if (!tasks.length) return
    const available = new Set(tasks.map((task) => task.task_id))
    setSelectedTaskIds((prev) => prev.filter((taskId) => available.has(taskId)))
  }, [tasks])

  const getStateBadgeClass = (state: string) => {
    if (state === 'completed') return 'bg-green-100 text-green-700 border-green-200'
    if (state === 'failed') return 'bg-red-100 text-red-700 border-red-200'
    if (state === 'manual') return 'bg-amber-100 text-amber-700 border-amber-200'
    if (state === 'cancelled') return 'bg-gray-100 text-gray-700 border-gray-200'
    return 'bg-blue-100 text-blue-700 border-blue-200'
  }

  const addGroup = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return
    setGroups((prev) => [...prev, { id: `${Date.now()}-${Math.random()}`, name: trimmed, taskIds: [] }])
    setGroupNameInput('')
  }

  const getTaskTextPreview = (task: AdminTask | undefined) => {
    if (!task) return '暂无题干预览'
    const text = (task.question_preview || task.final_result || '暂无题干预览').replace(/\s+/g, ' ').trim()
    return text.length > 120 ? `${text.slice(0, 120)}...` : text
  }

  const toggleTaskSelection = (taskId: string) => {
    setSelectedTaskIds((prev) => (
      prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
    ))
  }

  const selectAllCompletedTasks = () => {
    const ids = tasks.filter((task) => task.state === 'completed').map((task) => task.task_id)
    setSelectedTaskIds(ids)
  }

  const clearTaskSelection = () => {
    setSelectedTaskIds([])
  }

  const addSelectedTasksToGroup = (groupId: string) => {
    if (selectedTaskIds.length === 0) {
      setOperationMessage('请先在任务池勾选题目，再执行批量分配')
      return
    }
    setGroups((prev) => prev.map((group) => {
      if (group.id !== groupId) return group
      const next = [...group.taskIds]
      selectedTaskIds.forEach((taskId) => {
        if (!next.includes(taskId)) {
          next.push(taskId)
        }
      })
      return { ...group, taskIds: next }
    }))
    setOperationMessage(`已批量分配 ${selectedTaskIds.length} 题到「${groups.find((group) => group.id === groupId)?.name || '目标题型'}」`)
  }

  const removeSelectedTasksFromAllGroups = () => {
    if (selectedTaskIds.length === 0) {
      setOperationMessage('请先勾选要移除的题目')
      return
    }
    const selectedSet = new Set(selectedTaskIds)
    setGroups((prev) => prev.map((group) => ({
      ...group,
      taskIds: group.taskIds.filter((taskId) => !selectedSet.has(taskId)),
    })))
    setOperationMessage(`已从所有题型移除 ${selectedTaskIds.length} 题`)
  }

  const removeTaskFromAllGroups = (taskId: string) => {
    setGroups((prev) => prev.map((group) => ({
      ...group,
      taskIds: group.taskIds.filter((id) => id !== taskId),
    })))
  }

  const putTaskIntoGroup = (taskId: string, targetGroupId: string, targetIndex?: number) => {
    setGroups((prev) => {
      const removed = prev.map((group) => ({
        ...group,
        taskIds: group.taskIds.filter((id) => id !== taskId),
      }))
      return removed.map((group) => {
        if (group.id !== targetGroupId) return group
        const next = [...group.taskIds]
        const insertAt = typeof targetIndex === 'number' ? Math.max(0, Math.min(targetIndex, next.length)) : next.length
        next.splice(insertAt, 0, taskId)
        return { ...group, taskIds: next }
      })
    })
  }

  const moveGroup = (groupId: string, direction: 'up' | 'down') => {
    setGroups((prev) => {
      const index = prev.findIndex((group) => group.id === groupId)
      if (index < 0) return prev
      const target = direction === 'up' ? index - 1 : index + 1
      if (target < 0 || target >= prev.length) return prev
      const next = [...prev]
      const [moved] = next.splice(index, 1)
      next.splice(target, 0, moved)
      return next
    })
  }

  const buildNormalizedExportGroups = () => {
    const preparedGroups = groups.map((group) => ({
      group_id: group.id,
      group_name: group.name.trim() || '未命名题型',
      task_ids: group.taskIds,
    }))

    const emptyGroups = preparedGroups.filter((group) => group.task_ids.length === 0)
    const nonEmptyGroups = preparedGroups.filter((group) => group.task_ids.length > 0)
    const duplicateTaskIds: string[] = []
    const seen = new Set<string>()
    const nonCompletedTaskIds: string[] = []

    nonEmptyGroups.forEach((group) => {
      group.task_ids.forEach((taskId) => {
        if (seen.has(taskId)) {
          duplicateTaskIds.push(taskId)
        } else {
          seen.add(taskId)
        }
        const task = taskMap.get(taskId)
        if (task && task.state !== 'completed') {
          nonCompletedTaskIds.push(taskId)
        }
      })
    })

    return {
      nonEmptyGroups,
      emptyGroups,
      duplicateTaskIds: Array.from(new Set(duplicateTaskIds)),
      nonCompletedTaskIds: Array.from(new Set(nonCompletedTaskIds)),
    }
  }

  const validateBeforeExport = () => {
    const { nonEmptyGroups, emptyGroups, duplicateTaskIds, nonCompletedTaskIds } = buildNormalizedExportGroups()

    const blockingErrors: string[] = []
    const warnings: string[] = []

    if (groups.length === 0) {
      blockingErrors.push('尚未创建题型分组。')
    }
    if (nonEmptyGroups.length === 0) {
      blockingErrors.push('所有题型都为空，无法导出。')
    }
    if (duplicateTaskIds.length > 0) {
      blockingErrors.push(`存在重复分配题目：${duplicateTaskIds.slice(0, 5).join(', ')}${duplicateTaskIds.length > 5 ? '...' : ''}`)
    }
    if (emptyGroups.length > 0) {
      warnings.push(`存在空题型 ${emptyGroups.length} 个，导出时会自动跳过。`)
    }
    if (nonCompletedTaskIds.length > 0) {
      warnings.push(`包含非 completed 状态题目 ${nonCompletedTaskIds.length} 个，导出内容可能不完整。`)
    }

    return {
      ok: blockingErrors.length === 0,
      blockingErrors,
      warnings,
      groups: nonEmptyGroups,
    }
  }

  const doExport = async (format: 'md' | 'docx') => {
    const validation = validateBeforeExport()
    if (!validation.ok) {
      setOperationMessage(`导出校验失败：${validation.blockingErrors.join('；')}`)
      return
    }

    if (validation.warnings.length > 0) {
      const confirmed = window.confirm(`导出前提示：\n${validation.warnings.join('\n')}\n\n确认继续导出吗？`)
      if (!confirmed) return
    }

    setIsExporting(true)
    try {
      const endpoint = format === 'docx' ? '/api/admin/tasks/export/docx' : '/api/admin/tasks/export/md'
      const responseType = format === 'docx' ? 'blob' : 'blob'
      const response = await api.post(endpoint, {
        groups: validation.groups,
        paper_subject: paperSubject.trim(),
        paper_title: paperTitle.trim(),
      }, { responseType })
      const mimeType = format === 'docx'
        ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        : 'text/markdown;charset=utf-8'
      const blob = new Blob([response.data], { type: mimeType })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const stamp = new Date().toISOString().replace(/[:.]/g, '-')
      link.href = url
      link.download = format === 'docx' ? `paper_builder_${stamp}.docx` : `paper_builder_${stamp}.md`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
      setOperationMessage(format === 'docx' ? '排版台 DOCX 导出成功' : '排版台 Markdown 导出成功')
    } catch (error: unknown) {
      setOperationMessage(getErrorMessage(error, format === 'docx' ? '排版台 DOCX 导出失败' : '排版台 Markdown 导出失败'))
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <div className="max-w-7xl mx-auto p-8 space-y-6">
      <header className="border-b pb-4 flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">排版台</h1>
          <p className="text-sm text-gray-500 mt-2">按题型组卷、组内排序、导出结构化试卷</p>
          <p className="text-xs text-gray-500 mt-1">
            {isLoadingDraft ? '正在加载草稿...' : (isSavingDraft ? '草稿自动保存中...' : `草稿状态：已保存 ${lastSavedAt ? new Date(lastSavedAt).toLocaleString() : '（本地草稿）'}`)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => addGroup('选择题')}
            className="px-3 py-2 text-sm rounded-lg border bg-white hover:bg-gray-50"
          >
            + 选择题
          </button>
          <button
            onClick={() => addGroup('填空题')}
            className="px-3 py-2 text-sm rounded-lg border bg-white hover:bg-gray-50"
          >
            + 填空题
          </button>
          <button
            onClick={() => addGroup('判断题')}
            className="px-3 py-2 text-sm rounded-lg border bg-white hover:bg-gray-50"
          >
            + 判断题
          </button>
          <button
            onClick={() => addGroup('计算题')}
            className="px-3 py-2 text-sm rounded-lg border bg-white hover:bg-gray-50"
          >
            + 计算题
          </button>
          <button
            onClick={() => void doExport('md')}
            disabled={isExporting}
            className="px-3 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {isExporting ? '导出中...' : '导出 Markdown'}
          </button>
          <button
            onClick={() => void doExport('docx')}
            disabled={isExporting}
            className="px-3 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {isExporting ? '导出中...' : '导出 DOCX'}
          </button>
        </div>
      </header>

      {operationMessage && (
        <div className="bg-blue-50 border border-blue-200 text-blue-700 px-4 py-2 rounded">
          {operationMessage}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white border rounded-xl p-4 space-y-4">
          <h2 className="text-lg font-semibold text-gray-800">任务池（与数据库状态一致）</h2>
          <div className="grid grid-cols-2 gap-2">
            <button onClick={selectAllCompletedTasks} className="text-xs px-2.5 py-1.5 border rounded hover:bg-gray-50">
              全选已完成
            </button>
            <button onClick={clearTaskSelection} className="text-xs px-2.5 py-1.5 border rounded hover:bg-gray-50">
              清空选择
            </button>
            <button onClick={removeSelectedTasksFromAllGroups} className="col-span-2 text-xs px-2.5 py-1.5 border rounded hover:bg-gray-50">
              从所有题型移除已选
            </button>
          </div>
          {isLoading ? (
            <div className="text-sm text-gray-500">加载中...</div>
          ) : (
            <div className="space-y-3 max-h-[620px] overflow-y-auto">
              {tasks.map((task) => (
                <div
                  key={task.task_id}
                  className={`border rounded-lg p-3 ${draggingTaskId === task.task_id ? 'bg-indigo-50 border-indigo-300 opacity-70' : 'bg-gray-50'} ${groupedTaskIdSet.has(task.task_id) ? 'ring-1 ring-emerald-200' : ''}`}
                  draggable
                  onDragStart={(event) => {
                    setDraggingTaskId(task.task_id)
                    event.dataTransfer.setData('application/json', JSON.stringify({
                      type: 'task',
                      taskId: task.task_id,
                    }))
                  }}
                  onDragEnd={() => {
                    setDraggingTaskId(null)
                    setDragOverGroupId(null)
                    setDragOverTaskKey(null)
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <input
                        type="checkbox"
                        checked={selectedTaskIds.includes(task.task_id)}
                        onChange={() => toggleTaskSelection(task.task_id)}
                      />
                      <div className="font-mono text-xs text-gray-700 truncate">{task.task_id}</div>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded border ${getStateBadgeClass(task.state)}`}>
                      {task.state}
                    </span>
                  </div>
                  <div className="text-sm text-gray-700 mt-2 line-clamp-2">
                    {getTaskTextPreview(task)}
                  </div>
                  <div className="mt-2 flex justify-end">
                    <button
                      onClick={() => onOpenAdminTask(task.task_id)}
                      className="text-xs px-2 py-1 border rounded hover:bg-white"
                    >
                      任务管理
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white border rounded-xl p-4 space-y-4">
          <h2 className="text-lg font-semibold text-gray-800">组卷排版区</h2>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-700">草稿名称</label>
            <input
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm"
              placeholder="输入草稿名称"
            />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-gray-700">试卷科目（导出封面第一行）</label>
              <input
                value={paperSubject}
                onChange={(e) => setPaperSubject(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm"
                placeholder="例如：《电路分析》"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-gray-700">试卷名称/年份（导出封面第二行）</label>
              <input
                value={paperTitle}
                onChange={(e) => setPaperTitle(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm"
                placeholder="例如：2020-2021学年第二学期期末考试试卷"
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={groupNameInput}
              onChange={(e) => setGroupNameInput(e.target.value)}
              placeholder="输入自定义题型名称"
              className="flex-1 border rounded-lg px-3 py-2 text-sm"
            />
            <button
              onClick={() => addGroup(groupNameInput)}
              className="px-3 py-2 text-sm rounded-lg border bg-white hover:bg-gray-50"
            >
              新增题型
            </button>
          </div>

          <div className="space-y-4 max-h-[620px] overflow-y-auto">
            {groups.length === 0 && (
              <div className="text-sm text-gray-500 border border-dashed rounded-lg p-6 text-center">
                先创建题型，再把左侧任务拖拽到对应题型。
              </div>
            )}

            {groups.map((group) => (
              <div
                key={group.id}
                className={`border rounded-lg p-3 transition-colors ${dragOverGroupId === group.id ? 'bg-indigo-50 border-indigo-300' : 'bg-gray-50'}`}
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragOverGroupId(group.id)
                }}
                onDragLeave={() => {
                  if (dragOverGroupId === group.id) {
                    setDragOverGroupId(null)
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault()
                  const raw = event.dataTransfer.getData('application/json')
                  if (!raw) return
                  try {
                    const payload = JSON.parse(raw) as { type?: string; taskId?: string }
                    if (payload.type === 'task' && payload.taskId) {
                      putTaskIntoGroup(payload.taskId, group.id)
                    }
                  } catch {
                    // ignore invalid payload
                  }
                  setDragOverGroupId(null)
                  setDragOverTaskKey(null)
                }}
              >
                <div className="flex items-center gap-2">
                  <input
                    value={group.name}
                    onChange={(e) => {
                      const nextName = e.target.value
                      setGroups((prev) => prev.map((item) => (
                        item.id === group.id ? { ...item, name: nextName } : item
                      )))
                    }}
                    className="flex-1 bg-white border rounded px-2 py-1 text-sm"
                  />
                  <button onClick={() => moveGroup(group.id, 'up')} className="text-xs px-2 py-1 border rounded bg-white">上移</button>
                  <button onClick={() => moveGroup(group.id, 'down')} className="text-xs px-2 py-1 border rounded bg-white">下移</button>
                  <button
                    onClick={() => setGroups((prev) => prev.filter((item) => item.id !== group.id))}
                    className="text-xs px-2 py-1 border rounded bg-white text-red-600"
                  >
                    删除
                  </button>
                  <button
                    onClick={() => addSelectedTasksToGroup(group.id)}
                    className="text-xs px-2 py-1 border rounded bg-white text-indigo-700"
                    title="将任务池中已勾选题目批量加入当前题型"
                  >
                    批量放入
                  </button>
                </div>

                <div className="mt-3 space-y-2">
                  {group.taskIds.length === 0 && (
                    <div className={`text-xs border border-dashed rounded p-2 ${dragOverGroupId === group.id ? 'text-indigo-600 bg-indigo-50 border-indigo-300' : 'text-gray-500 bg-white'}`}>
                      将任务拖到此处
                    </div>
                  )}
                  {group.taskIds.map((taskId, idx) => {
                    const task = taskMap.get(taskId)
                    const taskKey = `${group.id}-${taskId}`
                    return (
                      <div
                        key={taskKey}
                        className={`bg-white border rounded p-2 text-xs ${dragOverTaskKey === taskKey ? 'border-indigo-300 bg-indigo-50' : ''}`}
                        draggable
                        onDragStart={(event) => {
                          setDraggingTaskId(taskId)
                          event.dataTransfer.setData('application/json', JSON.stringify({
                            type: 'task',
                            taskId,
                          }))
                        }}
                        onDragEnd={() => {
                          setDraggingTaskId(null)
                          setDragOverTaskKey(null)
                          setDragOverGroupId(null)
                        }}
                        onDragOver={(event) => {
                          event.preventDefault()
                          setDragOverTaskKey(taskKey)
                        }}
                        onDragLeave={() => {
                          if (dragOverTaskKey === taskKey) {
                            setDragOverTaskKey(null)
                          }
                        }}
                        onDrop={(event) => {
                          event.preventDefault()
                          const raw = event.dataTransfer.getData('application/json')
                          if (!raw) return
                          try {
                            const payload = JSON.parse(raw) as { type?: string; taskId?: string }
                            if (payload.type === 'task' && payload.taskId) {
                              putTaskIntoGroup(payload.taskId, group.id, idx)
                            }
                          } catch {
                            // ignore invalid payload
                          }
                          setDragOverTaskKey(null)
                          setDragOverGroupId(null)
                        }}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <div className="font-mono truncate">{taskId}</div>
                          <span className={`px-2 py-0.5 border rounded ${getStateBadgeClass(task?.state || '')}`}>
                            {task?.state || 'unknown'}
                          </span>
                        </div>
                        <div className="mt-1 flex justify-between items-center">
                          <span className="text-gray-500">组内序号: {idx + 1}</span>
                          <button
                            onClick={() => removeTaskFromAllGroups(taskId)}
                            className="text-xs px-2 py-0.5 border rounded"
                          >
                            移除
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function App() {
  const [currentView, setCurrentView] = useState<'dashboard' | 'admin' | 'builder'>('dashboard')
  const [adminFocusTaskId, setAdminFocusTaskId] = useState<string | null>(null)

  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-gray-100/50 py-8 font-sans text-gray-800">
        <div className="max-w-7xl mx-auto px-8 pb-4">
          <div className="inline-flex rounded-xl border bg-white p-1 shadow-sm gap-1">
            <button
              onClick={() => setCurrentView('dashboard')}
              className={`px-3 py-1.5 text-sm rounded-lg ${currentView === 'dashboard' ? 'bg-indigo-600 text-white' : 'text-gray-700 hover:bg-gray-50'}`}
            >
              工作台
            </button>
            <button
              onClick={() => setCurrentView('admin')}
              className={`px-3 py-1.5 text-sm rounded-lg ${currentView === 'admin' ? 'bg-indigo-600 text-white' : 'text-gray-700 hover:bg-gray-50'}`}
            >
              数据库
            </button>
            <button
              onClick={() => setCurrentView('builder')}
              className={`px-3 py-1.5 text-sm rounded-lg ${currentView === 'builder' ? 'bg-indigo-600 text-white' : 'text-gray-700 hover:bg-gray-50'}`}
            >
              排版台
            </button>
          </div>
        </div>

        {currentView === 'dashboard' && (
          <TaskDashboard onOpenAdmin={() => setCurrentView('admin')} />
        )}
        {currentView === 'admin' && (
          <AdminPanel
            initialTaskId={adminFocusTaskId}
            onBack={() => setCurrentView('dashboard')}
          />
        )}
        {currentView === 'builder' && (
          <PaperBuilder
            onOpenAdminTask={(taskId) => {
              setAdminFocusTaskId(taskId)
              setCurrentView('admin')
            }}
          />
        )}
      </div>
    </QueryClientProvider>
  )
}

export default App
