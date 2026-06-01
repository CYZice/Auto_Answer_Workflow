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
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
})

api.interceptors.request.use((config) => {
  const mineruApiToken = (localStorage.getItem(MINERU_API_TOKEN_STORAGE_KEY) || '').trim()
  const mineruApiBaseUrl = (localStorage.getItem(MINERU_API_BASE_URL_STORAGE_KEY) || '').trim()
  config.headers = config.headers ?? {}
  if (mineruApiToken) {
    config.headers['X-Mineru-Api-Token'] = mineruApiToken
  }
  if (mineruApiBaseUrl) {
    config.headers['X-Mineru-Api-Base-Url'] = mineruApiBaseUrl
  }
  return config
})

const RUNNING_TASK_STATES = ['queued', 'solving', 'reviewing', 'formatting']
const EXCEPTION_TASK_STATES = ['failed', 'manual', 'cancelled']
const SUBMITTED_TASKS_STORAGE_KEY = 'submitted_tasks'
const ACTIVE_TASK_ID_STORAGE_KEY = 'active_task_id'
const SOLVER_CONFIG_STORAGE_KEY = 'solver_config'
const REVIEWER_CONFIG_STORAGE_KEY = 'reviewer_config'
const FORMATTER_CONFIG_STORAGE_KEY = 'formatter_config'
const SHARED_BASE_URL_STORAGE_KEY = 'shared_base_url'
const SHARED_API_KEY_STORAGE_KEY = 'shared_api_key'
const MINERU_API_BASE_URL_STORAGE_KEY = 'mineru_api_base_url'
const MINERU_API_TOKEN_STORAGE_KEY = 'mineru_api_token'
const WORKFLOW_TEMPLATE_ID_STORAGE_KEY = 'workflow_template_id'
const INPUT_SELECTED_NODES_STORAGE_KEY = 'input_selected_nodes'
const WORKFLOW_NODE_ORDER = ['solver', 'reviewer', 'formatter'] as const
const PAPER_BUILDER_LOCAL_DRAFT_KEY = 'paper_builder_local_draft_v1'
const PAPER_BUILDER_REMOTE_DRAFT_ID = 'default'

type WorkflowNode = (typeof WORKFLOW_NODE_ORDER)[number]

const isOrderedWorkflowSelection = (nodes: WorkflowNode[]) => {
  if (nodes.length === 0) return false
  const indices = nodes.map((node) => WORKFLOW_NODE_ORDER.indexOf(node))
  return indices.every((idx, i) => i === 0 || indices[i - 1] < idx)
}

const getErrorMessage = (error: unknown, fallback: string) => {
  if (axios.isAxiosError(error)) {
    return (error.response?.data as { detail?: string } | undefined)?.detail || error.message || fallback
  }
  if (error instanceof Error) return error.message
  return fallback
}

const normalizeImageUrls = (imageUrls?: string[] | null, fallbackImageUrl?: string | null) => {
  const normalized: string[] = []
  if (Array.isArray(imageUrls)) {
    imageUrls.forEach((imageUrl) => {
      if (typeof imageUrl !== 'string') return
      const cleaned = imageUrl.trim()
      if (!cleaned || normalized.includes(cleaned)) return
      normalized.push(cleaned)
    })
  }
  if (typeof fallbackImageUrl === 'string') {
    const cleaned = fallbackImageUrl.trim()
    if (cleaned && !normalized.includes(cleaned)) {
      normalized.push(cleaned)
    }
  }
  return normalized
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
  image_urls?: string[];
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
  request_timeout_seconds: number;
  max_retries: number;
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
  sharedBaseUrl: string;
  sharedApiKey: string;
  mineruApiBaseUrl: string;
  mineruApiToken: string;
  activeTemplateId: string;
  requestTimeoutSeconds: number;
  maxRetries: number;
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

const mergeModelConfigWithShared = (
  config: ModelConfig,
  sharedBaseUrl: string,
  sharedApiKey: string
): ModelConfig => {
  const normalizedSharedBaseUrl = sharedBaseUrl.trim()
  const normalizedSharedApiKey = sharedApiKey.trim()
  return {
    ...config,
    base_url: normalizedSharedBaseUrl || (config.base_url || '').trim(),
    api_key: normalizedSharedApiKey || (config.api_key || '').trim(),
  }
}

const getLatestRetryConfigs = (): RetryModelConfigs => ({
  solver_config: mergeModelConfigWithShared(
    readStoredJson<ModelConfig>(SOLVER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 4096 }),
    localStorage.getItem(SHARED_BASE_URL_STORAGE_KEY) || '',
    localStorage.getItem(SHARED_API_KEY_STORAGE_KEY) || ''
  ),
  reviewer_config: mergeModelConfigWithShared(
    readStoredJson<ModelConfig>(REVIEWER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 2048 }),
    localStorage.getItem(SHARED_BASE_URL_STORAGE_KEY) || '',
    localStorage.getItem(SHARED_API_KEY_STORAGE_KEY) || ''
  ),
  formatter_config: mergeModelConfigWithShared(
    readStoredJson<ModelConfig>(FORMATTER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 1024 }),
    localStorage.getItem(SHARED_BASE_URL_STORAGE_KEY) || '',
    localStorage.getItem(SHARED_API_KEY_STORAGE_KEY) || ''
  ),
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
  const [pendingInputImages, setPendingInputImages] = useState<string[]>([])
  const [inputQuestionText, setInputQuestionText] = useState('')
  const [inputSkipReview, setInputSkipReview] = useState(false)
  const [inputSelectedNodes, setInputSelectedNodes] = useState<WorkflowNode[]>(() => {
    const saved = readStoredJson<WorkflowNode[]>(INPUT_SELECTED_NODES_STORAGE_KEY, [...WORKFLOW_NODE_ORDER])
    if (!Array.isArray(saved)) return [...WORKFLOW_NODE_ORDER]
    const normalized = WORKFLOW_NODE_ORDER.filter((node) => saved.includes(node))
    return normalized.length > 0 ? normalized : [...WORKFLOW_NODE_ORDER]
  })
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
  const [sharedBaseUrl, setSharedBaseUrl] = useState<string>(() => {
    const shared = (localStorage.getItem(SHARED_BASE_URL_STORAGE_KEY) || '').trim()
    if (shared) return shared
    const solverSaved = readStoredJson<ModelConfig>(SOLVER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 4096 })
    const reviewerSaved = readStoredJson<ModelConfig>(REVIEWER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 2048 })
    const formatterSaved = readStoredJson<ModelConfig>(FORMATTER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 1024 })
    return (solverSaved.base_url || reviewerSaved.base_url || formatterSaved.base_url || '').trim()
  })
  const [sharedApiKey, setSharedApiKey] = useState<string>(() => {
    const shared = (localStorage.getItem(SHARED_API_KEY_STORAGE_KEY) || '').trim()
    if (shared) return shared
    const solverSaved = readStoredJson<ModelConfig>(SOLVER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 4096 })
    const reviewerSaved = readStoredJson<ModelConfig>(REVIEWER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 2048 })
    const formatterSaved = readStoredJson<ModelConfig>(FORMATTER_CONFIG_STORAGE_KEY, { model_name: '', api_key: '', base_url: '', max_tokens: 1024 })
    return (solverSaved.api_key || reviewerSaved.api_key || formatterSaved.api_key || '').trim()
  })
  const [mineruApiBaseUrl, setMineruApiBaseUrl] = useState<string>(() => (
    localStorage.getItem(MINERU_API_BASE_URL_STORAGE_KEY) || ''
  ))
  const [mineruApiToken, setMineruApiToken] = useState<string>(() => (
    localStorage.getItem(MINERU_API_TOKEN_STORAGE_KEY) || ''
  ))
  const [runtimeLoading, setRuntimeLoading] = useState(false)
  const [runtimeError, setRuntimeError] = useState<string | null>(null)
  const [activeTemplateId, setActiveTemplateId] = useState<string>(() => localStorage.getItem(WORKFLOW_TEMPLATE_ID_STORAGE_KEY) || 'workflow_a')
  const [requestTimeoutSeconds, setRequestTimeoutSeconds] = useState<number>(300)
  const [maxRetries, setMaxRetries] = useState<number>(2)
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
    sharedBaseUrl,
    sharedApiKey,
    mineruApiBaseUrl,
    mineruApiToken,
    activeTemplateId,
    requestTimeoutSeconds,
    maxRetries,
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
      setRequestTimeoutSeconds(runtime.request_timeout_seconds || 300)
      setMaxRetries(runtime.max_retries ?? 2)

      const normalizedTemplates = Array.isArray(templates) ? templates : []
      setTemplateItems(normalizedTemplates)

      const runtimeTemplateId = (runtime.active_template_id || '').trim()
      const hasRuntimeTemplate = normalizedTemplates.some((item) => item.template_id === runtimeTemplateId)
      const pickedTemplateId = (hasRuntimeTemplate ? runtimeTemplateId : '')
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
      const nextSharedBaseUrl = (localStorage.getItem(SHARED_BASE_URL_STORAGE_KEY) || solverConfig.base_url || reviewerConfig.base_url || formatterConfig.base_url || '').trim()
      const nextSharedApiKey = (localStorage.getItem(SHARED_API_KEY_STORAGE_KEY) || solverConfig.api_key || reviewerConfig.api_key || formatterConfig.api_key || '').trim()
      const nextMineruApiBaseUrl = (localStorage.getItem(MINERU_API_BASE_URL_STORAGE_KEY) || '').trim()
      const nextMineruApiToken = (localStorage.getItem(MINERU_API_TOKEN_STORAGE_KEY) || '').trim()
      setSharedBaseUrl(nextSharedBaseUrl)
      setSharedApiKey(nextSharedApiKey)
      setMineruApiBaseUrl(nextMineruApiBaseUrl)
      setMineruApiToken(nextMineruApiToken)

      const baseline = toSettingsSnapshotString({
        solverConfig,
        reviewerConfig,
        formatterConfig,
        sharedBaseUrl: nextSharedBaseUrl,
        sharedApiKey: nextSharedApiKey,
        mineruApiBaseUrl: nextMineruApiBaseUrl,
        mineruApiToken: nextMineruApiToken,
        activeTemplateId: pickedTemplateId,
        requestTimeoutSeconds: runtime.request_timeout_seconds || 300,
        maxRetries: runtime.max_retries ?? 2,
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
    localStorage.setItem(SOLVER_CONFIG_STORAGE_KEY, JSON.stringify({ ...solverConfig, base_url: '', api_key: '' }))
    localStorage.setItem(REVIEWER_CONFIG_STORAGE_KEY, JSON.stringify({ ...reviewerConfig, base_url: '', api_key: '' }))
    localStorage.setItem(FORMATTER_CONFIG_STORAGE_KEY, JSON.stringify({ ...formatterConfig, base_url: '', api_key: '' }))
    localStorage.setItem(SHARED_BASE_URL_STORAGE_KEY, sharedBaseUrl.trim())
    localStorage.setItem(SHARED_API_KEY_STORAGE_KEY, sharedApiKey.trim())
    localStorage.setItem(MINERU_API_BASE_URL_STORAGE_KEY, mineruApiBaseUrl.trim())
    localStorage.setItem(MINERU_API_TOKEN_STORAGE_KEY, mineruApiToken.trim())
    localStorage.setItem(WORKFLOW_TEMPLATE_ID_STORAGE_KEY, activeTemplateId)

    try {
      await api.put('/api/settings/runtime', {
        active_template_id: activeTemplateId,
        request_timeout_seconds: requestTimeoutSeconds,
        max_retries: maxRetries,
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
      setPendingInputImages((prev) => {
        if (prev.includes(dataUrl)) return prev
        return [...prev, dataUrl]
      })
      setErrorMessage(null)
    } catch {
      setErrorMessage('读取本地图片失败，请重试。')
    }
  }

  // 处理剪贴板粘贴图片
  const handlePaste = (e: ClipboardEvent<HTMLDivElement>) => {
    const items = e.clipboardData.items;
    let hasImage = false
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf('image') !== -1) {
        hasImage = true
        const file = items[i].getAsFile();
        if (!file) continue;
        void loadInputImageFile(file)
      }
    }
    if (!hasImage) {
      setErrorMessage('剪贴板中未检测到图片。')
    }
  };

  const handlePickLocalImage = () => {
    fileInputRef.current?.click()
  }

  const handleLocalImageChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    files.forEach((file) => {
      void loadInputImageFile(file)
    })
    e.target.value = ''
  }

  const removePendingInputImage = (indexToDelete: number) => {
    setPendingInputImages((prev) => prev.filter((_, index) => index !== indexToDelete))
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
      imageUrls: string[];
      questionText?: string;
      entryPoint: WorkflowNode;
      targetNodes: WorkflowNode[];
    }) => api.post('/api/tasks', {
      image_urls: payload.imageUrls,
      question_text: payload.questionText || null,
      solver_config: withSharedConnection(solverConfig),
      reviewer_config: withSharedConnection(reviewerConfig),
      formatter_config: withSharedConnection(formatterConfig),
      workflow_template_id: activeTemplateId,
      entry_point: payload.entryPoint,
      target_nodes: payload.targetNodes
    }).then(res => res.data),
  })

  const orderedInputNodes = WORKFLOW_NODE_ORDER.filter((node) => inputSelectedNodes.includes(node))
  const effectiveInputNodes = inputSkipReview ? ['solver', 'formatter'] as WorkflowNode[] : orderedInputNodes
  const inputHasOrderedSelection = isOrderedWorkflowSelection(orderedInputNodes)
  const inputStartsAtSolver = effectiveInputNodes[0] === 'solver'
  const inputEntryPoint = effectiveInputNodes.length > 0 ? effectiveInputNodes[0] : undefined
  const inputQuestionTextValue = inputQuestionText.trim()
  const hasInputSource = pendingInputImages.length > 0 || inputQuestionTextValue.length > 0
  const canSubmitInputTask = hasInputSource
    && effectiveInputNodes.length > 0
    && inputStartsAtSolver
    && (inputSkipReview || inputHasOrderedSelection)
    && !createMutation.isPending
  const inputBlockedReason = !hasInputSource
    ? '请至少提供题目图片或文本。'
    : (effectiveInputNodes.length === 0
      ? '请至少选择一个工作流节点。'
      : (!inputStartsAtSolver
        ? '当前题目输入必须从 Solver 开始。'
      : (!inputSkipReview && !inputHasOrderedSelection
        ? '工作流节点必须按 solver -> reviewer -> formatter 的顺序选择。'
        : '')))

  const { data: activeTasksFromDb } = useQuery({
    queryKey: ['dashboard-active-tasks'],
    queryFn: async () => {
      const res = await api.get<AdminTask[] | { items?: AdminTask[] }>('/api/tasks/active/list')
      if (Array.isArray(res.data)) return res.data
      if (Array.isArray(res.data?.items)) return res.data.items
      return []
    },
    refetchInterval: 3000,
  })

  const mergedTaskIds = useMemo(() => {
    const ids = new Set<string>()
    submittedTasks.forEach((task) => {
      if (task.taskId.trim().length > 0) ids.add(task.taskId)
    })
      ; (Array.isArray(activeTasksFromDb) ? activeTasksFromDb : []).forEach((task) => {
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
    try {
      localStorage.setItem(INPUT_SELECTED_NODES_STORAGE_KEY, JSON.stringify(inputSelectedNodes))
    } catch {
      // 忽略持久化失败，避免影响主流程交互
    }
  }, [inputSelectedNodes])

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
    if (!canSubmitInputTask || !inputEntryPoint) return;
    setErrorMessage(null);

    try {
      const result = await createMutation.mutateAsync({
        imageUrls: pendingInputImages,
        questionText: inputQuestionTextValue,
        entryPoint: inputEntryPoint,
        targetNodes: effectiveInputNodes,
      });
      setSubmittedTasks((prev) => (
        prev.some((item) => item.taskId === result.task_id)
          ? prev
          : [...prev, { taskId: result.task_id }]
      ));
      setActiveTaskId(result.task_id);
      setPendingInputImages([]);
      setInputQuestionText('');
    } catch (error: unknown) {
      const errorMsg = getErrorMessage(error, "未知错误");
      setErrorMessage(`提交失败: ${errorMsg}`);
    }
  };

  const parseMaxTokens = (val: string) => {
    const parsed = parseInt(val, 10);
    return isNaN(parsed) ? 0 : parsed;
  };

  const parseNonNegativeInt = (val: string, fallback: number) => {
    const parsed = parseInt(val, 10)
    if (Number.isNaN(parsed) || parsed < 0) return fallback
    return parsed
  }

  const parsePositiveInt = (val: string, fallback: number) => {
    const parsed = parseInt(val, 10)
    if (Number.isNaN(parsed) || parsed < 1) return fallback
    return parsed
  }

  const withSharedConnection = (config: ModelConfig): ModelConfig => mergeModelConfigWithShared(
    config,
    sharedBaseUrl,
    sharedApiKey
  )

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
          <p className="text-sm text-gray-500 mt-2">提示: 可以直接在这个页面 <kbd className="bg-gray-100 px-1 rounded border">Ctrl+V</kbd> 粘贴图片，也可以输入题目文本；每次录入一题并提交后再开始下一题。</p>
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
          multiple
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
              className="flex items-center gap-2 bg-white text-blue-700 border border-blue-200 px-4 py-2 rounded-lg font-medium disabled:opacity-50 hover:bg-blue-50 transition-colors"
              title="从本地选择一张或多张图片"
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

        <div className="space-y-2">
          <label className="text-sm font-medium text-gray-700">题目文本输入</label>
          <textarea
            className="w-full min-h-32 p-3 border rounded-lg text-sm font-mono bg-white focus:ring-2 focus:ring-blue-500 outline-none"
            value={inputQuestionText}
            onChange={(e) => setInputQuestionText(e.target.value)}
            placeholder="可直接输入题目文本；可与图片同时提交，也可只提交文本。"
          />
        </div>

        {pendingInputImages.length > 0 ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between text-xs text-gray-600">
              <span>当前题目已添加 {pendingInputImages.length} 张图片</span>
              <button
                onClick={() => {
                  setPendingInputImages([])
                  setInputQuestionText('')
                }}
                className="text-red-600 hover:text-red-700"
              >
                清空本题图片
              </button>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {pendingInputImages.map((imageUrl, index) => (
                <div key={`${imageUrl.slice(0, 24)}-${index}`} className="relative group border rounded-lg overflow-hidden bg-gray-50 h-36 flex items-center justify-center">
                  <img src={imageUrl} alt={`pending-${index + 1}`} className="max-h-full object-contain" />
                  <div className="absolute inset-0 bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                    <button onClick={() => setPreviewImage(imageUrl)} className="p-2 bg-white rounded-full text-gray-700 hover:text-blue-600" title="预览">
                      <Maximize2 size={18} />
                    </button>
                    <button
                      onClick={() => removePendingInputImage(index)}
                      className="p-2 bg-white rounded-full text-gray-700 hover:text-red-600"
                      title="删除这张图"
                    >
                      <Trash2 size={18} />
                    </button>
                  </div>
                </div>
              ))}
            </div>

          </div>
        ) : (
          <div className="h-36 border-2 border-dashed border-gray-300 rounded-lg flex flex-col items-center justify-center text-gray-400 bg-gray-50">
            <Plus size={24} className="mb-2" />
            <span className="text-sm">支持 Ctrl+V 粘贴，或点击上方“本地选图”上传一张或多张题目截图</span>
            <span className="text-xs mt-1">也可以不传图片，直接在上方文本框输入题目后提交</span>
          </div>
        )}

        <div className="sticky bottom-0 z-10 -mx-6 px-6 py-4 pb-[calc(env(safe-area-inset-bottom)+0.75rem)] bg-white/95 backdrop-blur border-t border-gray-200 space-y-3">
          <label className="inline-flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={inputSkipReview}
              onChange={(e) => setInputSkipReview(e.target.checked)}
            />
            <span>跳过 Review，直接从 Solver 进入 Formatter</span>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            {WORKFLOW_NODE_ORDER.map((node, idx) => (
              <div key={node} className="flex items-center gap-2">
                <label className={`inline-flex items-center gap-2 text-sm px-3 py-1.5 rounded border bg-white border-gray-200 ${inputSkipReview ? 'opacity-50' : ''}`}>
                  <input
                    type="checkbox"
                    checked={inputSelectedNodes.includes(node)}
                    onChange={() => toggleInputNodeSelection(node)}
                    disabled={inputSkipReview}
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
            <div>目标节点: <span className="font-mono">{effectiveInputNodes.join(', ') || '-'}</span></div>
          </div>

          {inputBlockedReason && !canSubmitInputTask && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-100 px-3 py-2 rounded">
              {inputBlockedReason}
            </div>
          )}
        </div>
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
              <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2"><Settings size={20} /> 全局运行设置</h2>
              <button onClick={tryCloseSettings} className="text-gray-500 hover:text-gray-800"><X size={24} /></button>
            </div>

            <div className="p-6 space-y-8 max-h-[70vh] overflow-y-auto">
              <div className="space-y-4 border border-indigo-100 rounded-xl p-4 bg-indigo-50/30">
                <h3 className="text-lg font-semibold text-indigo-700 border-b border-indigo-100 pb-2">提示词设置</h3>
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

              <div className="space-y-6 border border-amber-100 rounded-xl p-4 bg-amber-50/30">
                <h3 className="text-lg font-semibold text-amber-700 border-b border-amber-100 pb-2">模型设置</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">全局超时时间（秒）</label>
                    <input
                      type="number"
                      min={1}
                      value={requestTimeoutSeconds}
                      onChange={(e) => setRequestTimeoutSeconds(parsePositiveInt(e.target.value, 300))}
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">全局重试次数</label>
                    <input
                      type="number"
                      min={0}
                      value={maxRetries}
                      onChange={(e) => setMaxRetries(parseNonNegativeInt(e.target.value, 2))}
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">MinerU Base URL</label>
                    <input
                      type="text"
                      value={mineruApiBaseUrl}
                      onChange={(e) => setMineruApiBaseUrl(e.target.value)}
                      placeholder="https://mineru.net/api/v4"
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">MinerU API Token</label>
                    <input
                      type="password"
                      value={mineruApiToken}
                      onChange={(e) => setMineruApiToken(e.target.value)}
                      placeholder="mtk-..."
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">共享 Base URL</label>
                    <input
                      type="text"
                      value={sharedBaseUrl}
                      onChange={(e) => setSharedBaseUrl(e.target.value)}
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">共享 API Key <span className="text-xs text-gray-400 font-normal">(留空则使用后端默认配置)</span></label>
                    <input
                      type="password"
                      value={sharedApiKey}
                      onChange={(e) => setSharedApiKey(e.target.value)}
                      placeholder="sk-..."
                      className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                    />
                  </div>
                </div>
                <div className="space-y-4">
                  <h4 className="text-sm font-semibold text-amber-700">Fallback 模型列表</h4>
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
                <div className="space-y-4">
                  <h4 className="text-lg font-semibold text-blue-600 border-b pb-2">Solver (解题) 节点</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                      <input type="text" value={solverConfig.model_name} onChange={e => setSolverConfig({ ...solverConfig, model_name: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                      <input type="number" value={solverConfig.max_tokens || ''} onChange={e => setSolverConfig({ ...solverConfig, max_tokens: parseMaxTokens(e.target.value) })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
                    </div>
                  </div>
                </div>
                <div className="space-y-4">
                  <h4 className="text-lg font-semibold text-purple-600 border-b pb-2">Reviewer (审查) 节点</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                      <input type="text" value={reviewerConfig.model_name} onChange={e => setReviewerConfig({ ...reviewerConfig, model_name: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                      <input type="number" value={reviewerConfig.max_tokens || ''} onChange={e => setReviewerConfig({ ...reviewerConfig, max_tokens: parseMaxTokens(e.target.value) })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none" />
                    </div>
                  </div>
                </div>
                <div className="space-y-4">
                  <h4 className="text-lg font-semibold text-green-600 border-b pb-2">Formatter (排版) 节点</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                      <input type="text" value={formatterConfig.model_name} onChange={e => setFormatterConfig({ ...formatterConfig, model_name: e.target.value })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
                      <input type="number" value={formatterConfig.max_tokens || ''} onChange={e => setFormatterConfig({ ...formatterConfig, max_tokens: parseMaxTokens(e.target.value) })} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-green-500 outline-none" />
                    </div>
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
  const [modelRequest, setModelRequest] = useState<{
    modelName: string;
    timeout: number;
    attempt: number;
    maxRetries: number;
    startTime: number;
  } | null>(null);
  const [elapsedTime, setElapsedTime] = useState<number>(0);

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
    setCustomDraftInput('');
    setModelRequest(null);
    setElapsedTime(0);
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
    let interval: ReturnType<typeof setInterval>;
    if (modelRequest) {
      interval = setInterval(() => {
        setElapsedTime(Math.floor((Date.now() - modelRequest.startTime) / 1000));
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [modelRequest]);

  useEffect(() => {
    if (isTaskEnded) return;

    const sse = new EventSource(`${import.meta.env.VITE_API_BASE_URL || ''}/api/tasks/${taskId}/stream`);

    sse.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.event === 'on_chat_model_stream') {
          setStreamedContent(prev => prev + (data.chunk || ''));
          if (data.node) setCurrentNode(data.node);
        } else if (data.event === 'node_start') {
          if (data.node) setCurrentNode(data.node);
        } else if (data.event === 'model_request_start') {
          setModelRequest({
            modelName: data.model_name,
            timeout: data.timeout,
            attempt: data.attempt,
            maxRetries: data.max_retries,
            startTime: Date.now()
          });
          setElapsedTime(0);
        } else if (data.event === 'end') {
          sse.close(); // 后端主动通知结束，断开连接避免重试
          setModelRequest(null);
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
  const hasOrderedSelection = isOrderedWorkflowSelection(orderedSelectedNodes)
  const customEntryPoint = orderedSelectedNodes.length > 0 ? orderedSelectedNodes[0] : undefined
  const customNeedsDraft = customEntryPoint === 'reviewer' || customEntryPoint === 'formatter'
  const customDraftValue = customDraftInput.trim().length > 0
    ? customDraftInput.trim()
    : (typeof history.draft_solution === 'string' ? history.draft_solution : '')
  const canSubmitCustomRun = orderedSelectedNodes.length > 0
    && hasOrderedSelection
    && (!customNeedsDraft || customDraftValue.trim().length > 0)
    && !manualMutation.isPending
  const customRunBlockedReason = orderedSelectedNodes.length === 0
    ? '请至少选择一个节点。'
    : (!hasOrderedSelection
      ? '工作流节点必须按 solver -> reviewer -> formatter 的顺序选择。'
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
  const taskImageUrls = normalizeImageUrls(task.image_urls, task.image_url)
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

        <div className="grid grid-cols-2 gap-3">
          {taskImageUrls.map((imageUrl, index) => (
            <div
              key={`${task.task_id}-image-${index}`}
              className="relative border rounded bg-gray-50 p-2 h-48 flex items-center justify-center group overflow-hidden cursor-pointer"
              onClick={() => onPreview(imageUrl)}
            >
              <img src={imageUrl} alt={`Task target ${index + 1}`} className="max-h-full object-contain" />
              <div className="absolute inset-0 bg-black/10 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center">
                <Maximize2 className="text-gray-700 bg-white/80 p-2 rounded-full w-10 h-10 shadow-sm" />
              </div>
            </div>
          ))}
          {taskImageUrls.length === 0 && (
            <div className="col-span-2 text-sm text-gray-500 border rounded p-4 bg-gray-50">无题目图片</div>
          )}
        </div>

        <div className="text-xs text-gray-500">
          图片数量: {taskImageUrls.length}
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
                <div className="flex flex-col">
                  <p className="font-medium text-gray-700">Agent is working...</p>
                  {modelRequest && (
                    <p className="text-xs text-gray-500 font-mono mt-0.5">
                      Model: {modelRequest.modelName} ({modelRequest.attempt}/{modelRequest.maxRetries + 1}) |
                      Time: <span className={elapsedTime > (modelRequest.timeout * 0.8) ? 'text-red-500' : ''}>{elapsedTime}s</span> / {modelRequest.timeout}s
                    </p>
                  )}
                </div>
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
  const [isBatchDeleting, setIsBatchDeleting] = useState(false)
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

      if (!isOrderedWorkflowSelection(orderedNodes)) {
        throw new Error('节点选择必须按 solver -> reviewer -> formatter 的顺序排列')
      }

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

      await api.post(`/api/tasks/${selectedTaskId}/manual`, {
        action: 'custom_run',
        draft_solution: draftSolution || undefined,
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

  const handleBatchDelete = async () => {
    if (selectedExportIds.length === 0) {
      setOperationMessage('请先勾选任务')
      return
    }

    const confirmed = window.confirm(`确认删除已勾选的 ${selectedExportIds.length} 条任务吗？`)
    if (!confirmed) return

    setIsBatchDeleting(true)
    try {
      const results = await Promise.allSettled(selectedExportIds.map((taskId) => api.delete(`/api/admin/tasks/${taskId}`)))
      const successCount = results.filter((result) => result.status === 'fulfilled').length
      const failedCount = results.length - successCount

      if (successCount > 0) {
        queryClient.invalidateQueries({ queryKey: ['admin-tasks'] })
        queryClient.invalidateQueries({ queryKey: ['admin-task-detail'] })
        queryClient.invalidateQueries({ queryKey: ['admin-logs'] })

        setSelectedExportIds((prev) => prev.filter((_, index) => results[index]?.status !== 'fulfilled'))
        if (selectedTaskId && selectedExportIds.includes(selectedTaskId)) {
          setSelectedTaskId(null)
        }
      }

      if (failedCount === 0) {
        setOperationMessage(`批量删除成功，共删除 ${successCount} 条`)
      } else {
        setOperationMessage(`批量删除完成，成功 ${successCount} 条，失败 ${failedCount} 条`)
      }
    } catch (error: unknown) {
      setOperationMessage(getErrorMessage(error, '批量删除失败'))
    } finally {
      setIsBatchDeleting(false)
    }
  }

  const canRetrySelectedTask = !!selectedTask && ['manual', 'failed'].includes(selectedTask.state)
  const canCustomRunSelectedTask = !!selectedTask && ['manual', 'failed', 'completed', 'cancelled'].includes(selectedTask.state)

  const orderedCustomRunNodes = WORKFLOW_NODE_ORDER.filter((node) => customRunNodes.includes(node))
  const customRunEntryPoint = orderedCustomRunNodes.length > 0 ? orderedCustomRunNodes[0] : undefined
  const customRunOrdered = isOrderedWorkflowSelection(orderedCustomRunNodes)
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
    && customRunOrdered
    && (!customRunNeedDraft || customRunDraftValue.trim().length > 0)
    && !customRunMutation.isPending

  const customRunHint = !canCustomRunSelectedTask
    ? '当前任务状态不支持自定义执行。'
    : (orderedCustomRunNodes.length === 0
      ? '请至少勾选一个节点。'
      : (!customRunOrdered
        ? '节点必须按 solver -> reviewer -> formatter 的顺序选择。'
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

  const copyAnswerPreview = async () => {
    const sanitizedText = (selectedTask?.answer_preview || '').replace(/\$/g, '')
    try {
      await navigator.clipboard.writeText(sanitizedText)
      setOperationMessage('answer_preview 已复制（已移除 $ 符号）')
    } catch {
      setOperationMessage('复制失败，请检查浏览器剪贴板权限')
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
                  onClick={() => void handleBatchDelete()}
                  disabled={isBatchDeleting || selectedExportIds.length === 0}
                  className="inline-flex items-center gap-1 text-xs px-2.5 py-1 text-red-600 border border-red-200 rounded disabled:opacity-50 hover:bg-red-50"
                >
                  <Trash2 size={12} />
                  {isBatchDeleting ? '删除中...' : `批量删除(${selectedExportIds.length})`}
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
                <div className="grid grid-cols-2 gap-3">
                  {normalizeImageUrls(selectedTask.image_urls, selectedTask.image_url).map((imageUrl, index) => (
                    <img key={`admin-task-image-${index}`} src={imageUrl} alt={`task-${index + 1}`} className="max-h-60 border rounded bg-gray-50 object-contain" />
                  ))}
                </div>
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
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-gray-700">answer_preview</label>
                    <button
                      type="button"
                      onClick={() => void copyAnswerPreview()}
                      className="text-xs px-2.5 py-1 border rounded hover:bg-gray-50"
                    >
                      复制
                    </button>
                  </div>
                  <textarea
                    value={selectedTask.answer_preview || ''}
                    readOnly
                    className="w-full min-h-32 border rounded-lg px-3 py-2 text-xs font-mono bg-gray-50"
                  />
                </div>

                <div className="space-y-1 lg:col-span-2">
                  <div className="text-xs text-gray-500">Markdown 渲染预览（截图建议使用此区域）</div>
                  <div className="prose prose-sm max-w-none border rounded-lg p-4 bg-white">
                    <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
                      {selectedTask.answer_preview || '暂无 answer_preview 内容'}
                    </ReactMarkdown>
                  </div>
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
  const [batchTargetGroupId, setBatchTargetGroupId] = useState('')
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

  useEffect(() => {
    if (groups.length === 0) {
      setBatchTargetGroupId('')
      return
    }
    setBatchTargetGroupId((prev) => {
      if (prev && groups.some((group) => group.id === prev)) return prev
      return groups[0]?.id || ''
    })
  }, [groups])

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

  const sortTaskIdsByTime = (taskIds: string[]) => {
    const toTimestamp = (value?: string | null) => {
      if (!value) return Number.NaN
      const parsed = Date.parse(value)
      return Number.isNaN(parsed) ? Number.NaN : parsed
    }

    return [...taskIds].sort((leftId, rightId) => {
      const leftTask = taskMap.get(leftId)
      const rightTask = taskMap.get(rightId)
      const leftTime = toTimestamp(leftTask?.created_at) || toTimestamp(leftTask?.updated_at)
      const rightTime = toTimestamp(rightTask?.created_at) || toTimestamp(rightTask?.updated_at)

      const leftValid = Number.isFinite(leftTime)
      const rightValid = Number.isFinite(rightTime)
      if (leftValid && rightValid && leftTime !== rightTime) return leftTime - rightTime
      if (leftValid !== rightValid) return leftValid ? -1 : 1
      return leftId.localeCompare(rightId)
    })
  }

  const addSelectedTasksToGroup = (groupId: string) => {
    if (selectedTaskIds.length === 0) {
      setOperationMessage('请先在任务池勾选题目，再执行批量分配')
      return
    }
    const sortedSelectedTaskIds = sortTaskIdsByTime(selectedTaskIds)
    setGroups((prev) => prev.map((group) => {
      if (group.id !== groupId) return group
      const next = [...group.taskIds]
      sortedSelectedTaskIds.forEach((taskId) => {
        if (!next.includes(taskId)) {
          next.push(taskId)
        }
      })
      return { ...group, taskIds: next }
    }))
    setOperationMessage(`已按时间顺序批量分配 ${sortedSelectedTaskIds.length} 题到「${groups.find((group) => group.id === groupId)?.name || '目标题型'}」`)
  }

  const addSelectedTasksToBatchTarget = () => {
    if (!batchTargetGroupId) {
      setOperationMessage('请先创建题型，再执行批量分配')
      return
    }
    addSelectedTasksToGroup(batchTargetGroupId)
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

  const selectedCount = selectedTaskIds.length
  const completedTaskCount = tasks.filter((task) => task.state === 'completed').length
  const batchTargetGroupName = groups.find((group) => group.id === batchTargetGroupId)?.name || ''

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
          <div className="space-y-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-gray-800">任务池（与数据库状态一致）</h2>
                <p className="text-xs text-gray-500">勾选题目后，直接在这里指定题型并批量加入，不必再去右侧逐个点。</p>
              </div>
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-indigo-700">已选 {selectedCount} 题</span>
                <span>可分配 {completedTaskCount} 题</span>
              </div>
            </div>
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-3 space-y-3">
              <div className="flex flex-wrap gap-2">
                <button onClick={selectAllCompletedTasks} className="text-xs px-3 py-1.5 border rounded-lg bg-white hover:bg-gray-100">
                  全选已完成
                </button>
                <button onClick={clearTaskSelection} className="text-xs px-3 py-1.5 border rounded-lg bg-white hover:bg-gray-100">
                  清空选择
                </button>
                <button
                  onClick={removeSelectedTasksFromAllGroups}
                  disabled={selectedCount === 0}
                  className="text-xs px-3 py-1.5 border rounded-lg bg-white text-amber-700 hover:bg-amber-50 disabled:opacity-50"
                >
                  从所有题型移除
                </button>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <select
                  value={batchTargetGroupId}
                  onChange={(e) => setBatchTargetGroupId(e.target.value)}
                  disabled={groups.length === 0}
                  className="flex-1 rounded-lg border bg-white px-3 py-2 text-sm disabled:bg-gray-100 disabled:text-gray-400"
                >
                  {groups.length === 0 ? (
                    <option value="">请先在右侧创建题型</option>
                  ) : (
                    groups.map((group) => (
                      <option key={group.id} value={group.id}>
                        加入到：{group.name}
                      </option>
                    ))
                  )}
                </select>
                <button
                  onClick={addSelectedTasksToBatchTarget}
                  disabled={selectedCount === 0 || groups.length === 0}
                  className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {selectedCount === 0
                    ? '先勾选题目'
                    : batchTargetGroupName
                      ? `加入「${batchTargetGroupName}」`
                      : '批量加入题型'}
                </button>
              </div>
            </div>
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
                        className="h-5 w-5 cursor-pointer rounded border-gray-300 accent-indigo-600"
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
  const [currentView, setCurrentView] = useState<'dashboard' | 'admin' | 'builder' | 'smart-parser'>('dashboard')
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
            <button
              onClick={() => setCurrentView('smart-parser')}
              className={`px-3 py-1.5 text-sm rounded-lg ${currentView === 'smart-parser' ? 'bg-indigo-600 text-white' : 'text-gray-700 hover:bg-gray-50'}`}
            >
              智能解析
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
        {currentView === 'smart-parser' && (
          <SmartPaperParser onBack={() => setCurrentView('dashboard')} />
        )}
      </div>
    </QueryClientProvider>
  )
}

// === 智能解析试卷组件 ===
interface MineruParseResultResponse {
  mineru_task_id: string;
  status: string;
  markdown_url?: string;
  markdown_content?: string;
  error_message?: string;
  extract_progress?: {
    extracted_pages: number;
    total_pages: number;
  };
}

interface ParsedQuestion {
  number: number;
  type: string;
  content: string;
  images: string[];
}

interface ParsedQuestionsResponse {
  total: number;
  questions: ParsedQuestion[];
  grouped: Record<string, Array<{ number: number; content: string }>>;
}

interface QuestionSolveStatus {
  task_id: string;
  number: number;
  type: string;
  status: string;
  final_result?: string;
}

interface PaperSolveStatusResponse {
  paper_task_id: string;
  total: number;
  completed: number;
  results: QuestionSolveStatus[];
}

type ParseStage = 'idle' | 'uploading' | 'waiting' | 'parsing' | 'stopped' | 'done' | 'error';
type SolveProgress = 'idle' | 'solving' | 'completed' | 'error';

function SmartPaperParser({ onBack }: { onBack: () => void }) {
  const parseSessionRef = useRef(0)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(
    sessionStorage.getItem('smartParser_preview')
  )
  const [parseStage, setParseStage] = useState<ParseStage>(
    (sessionStorage.getItem('smartParser_stage') as ParseStage) || 'idle'
  )
  const [batchId, setBatchId] = useState<string | null>(sessionStorage.getItem('smartParser_batchId'))
  const [parseProgress, setParseProgress] = useState<{ extracted: number; total: number } | null>(
    JSON.parse(sessionStorage.getItem('smartParser_parseProgress') || 'null')
  )
  const [markdownContent, setMarkdownContent] = useState<string | null>(
    sessionStorage.getItem('smartParser_markdown')
  )
  const [errorMessage, setErrorMessage] = useState<string | null>(
    sessionStorage.getItem('smartParser_error')
  )
  const [questions, setQuestions] = useState<ParsedQuestion[]>(
    JSON.parse(sessionStorage.getItem('smartParser_questions') || '[]')
  )
  const [editableQuestions, setEditableQuestions] = useState<ParsedQuestion[]>(
    JSON.parse(sessionStorage.getItem('smartParser_editable_questions') || '[]')
  )
  const [groupedQuestions, setGroupedQuestions] = useState<Record<string, Array<{ number: number; content: string }>>>(
    JSON.parse(sessionStorage.getItem('smartParser_grouped') || '{}')
  )
  const [solveProgress, setSolveProgress] = useState<SolveProgress>(
    (sessionStorage.getItem('smartParser_solveProgress') as SolveProgress) || 'idle'
  )
  const [solveResult, setSolveResult] = useState<{ paper_task_id: string; question_count: number; thread_id: string; task_ids: string[] } | null>(
    JSON.parse(sessionStorage.getItem('smartParser_result') || 'null')
  )
  const [questionStatuses, setQuestionStatuses] = useState<Record<string, QuestionSolveStatus>>(
    JSON.parse(sessionStorage.getItem('smartParser_statuses') || '{}')
  )
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([])
  const [skipReviewOnRetry, setSkipReviewOnRetry] = useState(false)
  const [batchActionLoading, setBatchActionLoading] = useState(false)
  const [originalImages, setOriginalImages] = useState<string[]>([])
  const [paperSubject, setPaperSubject] = useState('')
  const [paperTitle, setPaperTitle] = useState('')

  const readImageFileAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result)
        return
      }
      reject(new Error('图片读取失败'))
    }
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(file)
  })

  const loadOriginalImagesForSolve = async (file: File) => {
    if (!file.type.startsWith('image/')) {
      setOriginalImages([])
      return
    }
    try {
      const dataUrl = await readImageFileAsDataUrl(file)
      setOriginalImages([dataUrl])
    } catch {
      setOriginalImages([])
    }
  }

  const rebuildGroupedQuestions = (nextQuestions: ParsedQuestion[]) => {
    return nextQuestions.reduce<Record<string, Array<{ number: number; content: string }>>>((acc, question) => {
      const type = (question.type || '').trim() || 'Uncategorized'
      if (!acc[type]) {
        acc[type] = []
      }
      acc[type].push({
        number: question.number,
        content: question.content,
      })
      acc[type].sort((a, b) => a.number - b.number)
      return acc
    }, {})
  }

  const replaceEditableQuestions = (updater: (prev: ParsedQuestion[]) => ParsedQuestion[]) => {
    setEditableQuestions((prev) => {
      const next = updater(prev)
      setGroupedQuestions(rebuildGroupedQuestions(next))
      return next
    })
  }

  const updateEditableQuestion = (number: number, patch: Partial<ParsedQuestion>) => {
    replaceEditableQuestions((prev) => prev.map((question) => {
      if (question.number !== number) return question
      const nextType = patch.type !== undefined ? patch.type : question.type
      const nextContent = patch.content !== undefined ? patch.content : question.content
      return {
        ...question,
        ...patch,
        type: nextType,
        content: nextContent,
      }
    }))
  }

  const addEditableQuestion = () => {
    replaceEditableQuestions((prev) => {
      const nextNumber = prev.reduce((max, question) => Math.max(max, question.number), 0) + 1
      return [...prev, { number: nextNumber, type: '', content: '', images: [] }]
    })
  }

  const deleteEditableQuestion = (number: number) => {
    replaceEditableQuestions((prev) => prev.filter((question) => question.number !== number))
  }

  const sortEditableQuestions = () => {
    replaceEditableQuestions((prev) => [...prev].sort((a, b) => a.number - b.number))
  }

  const moveQuestionByOffset = (number: number, offset: -1 | 1) => {
    replaceEditableQuestions((prev) => {
      const sorted = [...prev].sort((a, b) => a.number - b.number)
      const currentIndex = sorted.findIndex((question) => question.number === number)
      const targetIndex = currentIndex + offset
      if (currentIndex < 0 || targetIndex < 0 || targetIndex >= sorted.length) {
        return prev
      }
      const currentQuestion = sorted[currentIndex]
      const targetQuestion = sorted[targetIndex]
      return prev.map((question) => {
        if (question.number === currentQuestion.number) {
          return { ...question, number: targetQuestion.number }
        }
        if (question.number === targetQuestion.number) {
          return { ...question, number: currentQuestion.number }
        }
        return question
      })
    })
  }

  const moveImageToQuestion = (sourceNumber: number, imageUrl: string, targetNumber: number) => {
    if (sourceNumber === targetNumber) return
    replaceEditableQuestions((prev) => {
      if (!prev.some((question) => question.number === targetNumber)) {
        return prev
      }
      return prev.map((question) => {
        if (question.number === sourceNumber) {
          return {
            ...question,
            images: (question.images || []).filter((image) => image !== imageUrl),
          }
        }
        if (question.number === targetNumber) {
          const nextImages = question.images || []
          if (nextImages.includes(imageUrl)) {
            return question
          }
          return {
            ...question,
            images: [...nextImages, imageUrl],
          }
        }
        return question
      })
    })
  }

  const removeQuestionImage = (number: number, imageUrl: string) => {
    updateEditableQuestion(number, {
      images: (editableQuestions.find((question) => question.number === number)?.images || []).filter((image) => image !== imageUrl),
    })
  }

  // 状态变化时持久化到 sessionStorage
  useEffect(() => {
    sessionStorage.setItem('smartParser_stage', parseStage)
  }, [parseStage])
  useEffect(() => {
    sessionStorage.setItem('smartParser_batchId', batchId || '')
  }, [batchId])
  useEffect(() => {
    sessionStorage.setItem('smartParser_markdown', markdownContent || '')
  }, [markdownContent])
  useEffect(() => {
    sessionStorage.setItem('smartParser_error', errorMessage || '')
  }, [errorMessage])
  useEffect(() => {
    sessionStorage.setItem('smartParser_questions', JSON.stringify(questions))
  }, [questions])

  useEffect(() => {
    if (questions.length === 0) return
    if (editableQuestions.length > 0) return
    setEditableQuestions(questions)
    setGroupedQuestions(rebuildGroupedQuestions(questions))
  }, [questions, editableQuestions.length])

  useEffect(() => {
    sessionStorage.setItem('smartParser_editable_questions', JSON.stringify(editableQuestions))
  }, [editableQuestions])
  useEffect(() => {
    sessionStorage.setItem('smartParser_grouped', JSON.stringify(groupedQuestions))
  }, [groupedQuestions])
  useEffect(() => {
    sessionStorage.setItem('smartParser_solveProgress', solveProgress)
  }, [solveProgress])
  useEffect(() => {
    sessionStorage.setItem('smartParser_result', JSON.stringify(solveResult))
  }, [solveResult])
  useEffect(() => {
    sessionStorage.setItem('smartParser_statuses', JSON.stringify(questionStatuses))
  }, [questionStatuses])
  useEffect(() => {
    sessionStorage.setItem('smartParser_preview', previewUrl || '')
  }, [previewUrl])
  useEffect(() => {
    sessionStorage.setItem('smartParser_parseProgress', JSON.stringify(parseProgress))
  }, [parseProgress])

  // 组件挂载时：如果 solveProgress 是 'solving' 且有 solveResult，说明页面刷新后轮询中断了，重新开始轮询
  useEffect(() => {
    if (solveProgress === 'solving' && solveResult?.thread_id) {
      pollSolveStatus(solveResult.thread_id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    return () => {
      parseSessionRef.current += 1
    }
  }, [])

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      setSelectedFile(file)
      void loadOriginalImagesForSolve(file)
      const url = URL.createObjectURL(file)
      setPreviewUrl(url)
      setParseStage('idle')
      setMarkdownContent(null)
      setErrorMessage(null)
      setQuestions([])
      setEditableQuestions([])
      setGroupedQuestions({})
      setSolveResult(null)
      setSolveProgress('idle')
      setQuestionStatuses({})
      setSelectedTaskIds([])
    }
  }

  const handleStopParse = () => {
    parseSessionRef.current += 1
    setParseStage('stopped')
    setParseProgress(null)
    setErrorMessage('Stopped manually')
  }

  const handleParse = async () => {
    if (!selectedFile) return
    const sessionId = parseSessionRef.current + 1
    setErrorMessage('Stopped manually')

    setParseStage('uploading')
    setErrorMessage(null)
    setParseProgress(null)

    try {
      // 1. 后端代理上传文件到 OSS，返回 batch_id
      const formData = new FormData()
      formData.append('file', selectedFile)
      const parseRes = await api.post<MineruParseResultResponse>(
        '/api/mineru/parse/file',
        formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      if (parseSessionRef.current !== sessionId) return
      const batch_id = parseRes.data.mineru_task_id
      setBatchId(batch_id)

      setParseStage('parsing')

      // 2. 轮询等待解析完成
      const maxWait = 600000 // 10分钟
      const startTime = Date.now()

      while (Date.now() - startTime < maxWait) {
        await new Promise(resolve => setTimeout(resolve, 3000))
        if (parseSessionRef.current !== sessionId) return

        try {
          const resultRes = await api.get<MineruParseResultResponse>(`/api/mineru/parse/${batch_id}`)
          if (parseSessionRef.current !== sessionId) return
          const result = resultRes.data

          console.log('[DEBUG parse polling] batch_id:', batch_id, 'status:', result.status, 'extract_progress:', result.extract_progress)

          // 更新进度
          if (result.extract_progress) {
            setParseProgress({
              extracted: result.extract_progress.extracted_pages,
              total: result.extract_progress.total_pages,
            })
          }

          if (result.status === 'done') {
            console.log('[DEBUG parse polling] status is DONE, fetching questions...')
            setMarkdownContent(result.markdown_content || '')
            setParseStage('done')

            // 获取题目列表
            try {
              console.log('[DEBUG parse polling] fetching questions from:', `/api/mineru/paper/${batch_id}/questions`)
              const questionsRes = await api.get<ParsedQuestionsResponse>(`/api/mineru/paper/${batch_id}/questions`)
              if (parseSessionRef.current !== sessionId) return
              const parsedQuestions = questionsRes.data.questions || []
              console.log('[DEBUG parse polling] got questions:', parsedQuestions.length)
              setQuestions(parsedQuestions)
              setEditableQuestions(parsedQuestions)
              setGroupedQuestions(rebuildGroupedQuestions(parsedQuestions))
            } catch (e) {
              console.error('[DEBUG parse polling] failed to fetch questions:', e)
              // 题目列表获取失败不影响主流程
            }

            return
          }

          if (result.status === 'failed') {
            console.log('[DEBUG parse polling] status is FAILED:', result.error_message)
            throw new Error(result.error_message || '解析失败')
          }

          console.log('[DEBUG parse polling] current status is:', result.status, '- continuing to poll...')
        } catch (e) {
          console.error('[DEBUG parse polling] error during poll:', e)
          // 继续等待
        }
      }

      throw new Error('解析超时')
    } catch (err) {
      if (parseSessionRef.current !== sessionId) return
      setErrorMessage(getErrorMessage(err, '解析失败'))
      setParseStage('error')
    }
  }

  const handleStartSolve = async () => {
    if (!batchId) return

    const sourceQuestions = editableQuestions.length > 0 ? editableQuestions : questions

    // 检查是否有使用题号勾选的题目（格式：number:X）
    const selectedNumbers = selectedTaskIds
      .filter((id) => id.startsWith('number:'))
      .map((id) => parseInt(id.split(':')[1], 10))

    // 如果有题号勾选，只提交被勾选的题目；否则提交所有题目
    const questionsToSubmit = selectedNumbers.length > 0
      ? sourceQuestions.filter((q) => selectedNumbers.includes(q.number))
      : sourceQuestions

    const normalizedQuestions = questionsToSubmit.map((question) => ({
      number: question.number,
      type: (question.type || '').trim(),
      content: (question.content || '').trim(),
      images: question.images || [],
    }))

    if (normalizedQuestions.length === 0) {
      setErrorMessage('请先勾选要提交的题目')
      return
    }

    const emptyTypeQuestion = normalizedQuestions.find((question) => question.type.length === 0)
    if (emptyTypeQuestion) {
      setErrorMessage(`第 ${emptyTypeQuestion.number} 题题型不能为空`)
      return
    }
    const emptyContentQuestion = normalizedQuestions.find((question) => question.content.length === 0)
    if (emptyContentQuestion) {
      setErrorMessage(`第 ${emptyContentQuestion.number} 题题干不能为空`)
      return
    }
    const numberSet = new Set<number>()
    for (const question of normalizedQuestions) {
      if (numberSet.has(question.number)) {
        setErrorMessage(`题号重复：${question.number}`)
        return
      }
      numberSet.add(question.number)
    }

    setSolveProgress('solving')
    setQuestionStatuses({})
    setSelectedTaskIds([])
    try {
      const runtimeConfigs = getLatestRetryConfigs()
      const res = await api.post<{
        paper_task_id: string;
        question_count: number;
        task_ids: string[];
        thread_id: string;
      }>(
        `/api/mineru/paper/${batchId}/solve`,
        {
          original_images: originalImages,
          questions_override: normalizedQuestions,
          solver_config: runtimeConfigs.solver_config,
          reviewer_config: runtimeConfigs.reviewer_config,
          formatter_config: runtimeConfigs.formatter_config,
          workflow_template_id: runtimeConfigs.workflow_template_id,
        }
      )
      setSolveResult({
        paper_task_id: res.data.paper_task_id,
        question_count: res.data.question_count,
        thread_id: res.data.thread_id,
        task_ids: res.data.task_ids,
      })
      res.data.task_ids.forEach((taskId) => {
        persistTaskForDashboard(taskId)
      })

      // 轮询解题状态
      pollSolveStatus(res.data.thread_id)
    } catch (err) {
      setErrorMessage(getErrorMessage(err, '启动解题失败'))
      setSolveProgress('error')
    }
  }

  const refreshSolveStatusOnce = async (threadId: string) => {
    const res = await api.get<PaperSolveStatusResponse>(
      `/api/mineru/paper/${threadId}/status`
    )
    const data = res.data
    const newStatuses: Record<string, QuestionSolveStatus> = {}
    let completedCount = 0
    let terminalCount = 0
    for (const result of data.results) {
      newStatuses[result.task_id] = result
      if (result.status === 'completed') {
        completedCount++
      }
      if (['completed', 'failed', 'manual', 'cancelled'].includes(result.status)) {
        terminalCount++
      }
    }
    setQuestionStatuses(newStatuses)
    if (terminalCount === data.total && data.total > 0) {
      setSolveProgress(completedCount === data.total ? 'completed' : 'error')
      return true
    }
    return false
  }

  const pollSolveStatus = async (threadId: string) => {
    const maxWait = 600000 // 10分钟
    const startTime = Date.now()

    while (Date.now() - startTime < maxWait) {
      try {
        const isCompleted = await refreshSolveStatusOnce(threadId)
        if (isCompleted) return
      } catch {
        // 继续轮询
      }

      await new Promise(resolve => setTimeout(resolve, 3000))
    }

    setSolveProgress('error')
  }

  const runningTaskIds = useMemo(() => {
    return Object.values(questionStatuses)
      .filter((item) => ['queued', 'solving', 'reviewing', 'formatting'].includes(item.status))
      .map((item) => item.task_id)
  }, [questionStatuses])

  const retryableTaskIds = useMemo(() => {
    return Object.values(questionStatuses)
      .filter((item) => ['failed', 'manual', 'cancelled'].includes(item.status))
      .map((item) => item.task_id)
  }, [questionStatuses])

  const toggleTaskSelection = (taskId: string) => {
    setSelectedTaskIds((prev) => (
      prev.includes(taskId) ? prev.filter((id) => id !== taskId) : [...prev, taskId]
    ))
  }

  const selectAllRunning = () => {
    setSelectedTaskIds(runningTaskIds)
  }

  const selectAllRetryable = () => {
    setSelectedTaskIds(retryableTaskIds)
  }

  const clearSelectedTasks = () => {
    setSelectedTaskIds([])
  }

  const handleBatchPause = async () => {
    if (selectedTaskIds.length === 0) {
      setErrorMessage('请先勾选要暂停的题目')
      return
    }
    const toPause = selectedTaskIds.filter((taskId) => runningTaskIds.includes(taskId))
    if (toPause.length === 0) {
      setErrorMessage('当前勾选题目中没有可暂停任务')
      return
    }
    setBatchActionLoading(true)
    try {
      const results = await Promise.allSettled(toPause.map((taskId) => api.post(`/api/tasks/${taskId}/cancel`)))
      const successCount = results.filter((result) => result.status === 'fulfilled').length
      const failedCount = results.length - successCount
      setErrorMessage(null)
      if (failedCount > 0) {
        setErrorMessage(`批量暂停完成：成功 ${successCount}，失败 ${failedCount}`)
      }
      if (solveResult?.thread_id) {
        await refreshSolveStatusOnce(solveResult.thread_id)
      }
    } finally {
      setBatchActionLoading(false)
    }
  }

  const handleBatchRetry = async () => {
    if (selectedTaskIds.length === 0) {
      setErrorMessage('请先勾选要重试的题目')
      return
    }
    const toRetry = selectedTaskIds.filter((taskId) => retryableTaskIds.includes(taskId))
    if (toRetry.length === 0) {
      setErrorMessage('当前勾选题目中没有可重试任务')
      return
    }

    setBatchActionLoading(true)
    try {
      const retryConfigs = getLatestRetryConfigs()
      const results = await Promise.allSettled(
        toRetry.map((taskId) => {
          const status = questionStatuses[taskId]?.status
          if (!skipReviewOnRetry && (status === 'failed' || status === 'manual')) {
            return api.post(`/api/tasks/${taskId}/manual`, {
              action: 'resume',
              draft_solution: undefined,
              ...retryConfigs,
            })
          }
          return api.post(`/api/tasks/${taskId}/manual`, {
            action: 'custom_run',
            entry_point: 'solver',
            target_nodes: skipReviewOnRetry ? ['solver', 'formatter'] : ['solver', 'reviewer', 'formatter'],
            ...retryConfigs,
          })
        })
      )
      const successCount = results.filter((result) => result.status === 'fulfilled').length
      const failedCount = results.length - successCount
      setErrorMessage(null)
      if (failedCount > 0) {
        setErrorMessage(`批量重试完成：成功 ${successCount}，失败 ${failedCount}`)
      }
      setSolveProgress('solving')
      if (solveResult?.thread_id) {
        await refreshSolveStatusOnce(solveResult.thread_id)
      }
    } finally {
      setBatchActionLoading(false)
    }
  }

  const handleExportDocx = async () => {
    if (!batchId) return

    const params = new URLSearchParams()
    if (paperSubject.trim()) params.set('paper_subject', paperSubject.trim())
    if (paperTitle.trim()) params.set('paper_title', paperTitle.trim())
    const queryString = params.toString()
    const exportUrl = `/api/mineru/paper/${batchId}/export/docx${queryString ? `?${queryString}` : ''}`

    try {
      const res = await fetch(exportUrl)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = '试卷解析结果.docx'
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setErrorMessage(getErrorMessage(err, '导出失败'))
    }
  }

  const renderParseStatus = () => {
    switch (parseStage) {
      case 'idle':
        return <span className="text-gray-500">等待上传</span>
      case 'uploading':
        return <span className="text-blue-600">获取上传链接中...</span>
      case 'waiting':
        return <span className="text-blue-600">文件上传中...</span>
      case 'parsing':
        if (parseProgress) {
          return (
            <span className="text-blue-600">
              解析中 ({parseProgress.extracted}/{parseProgress.total} 页)
            </span>
          )
        }
        return <span className="text-blue-600">解析中...</span>
      case 'done':
        return <span className="text-green-600">解析完成</span>
      case 'stopped':
        return <span className="text-amber-600">Stopped manually</span>
      case 'error':
        return <span className="text-red-600">解析失败</span>
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-8 pb-4">
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-2xl font-bold">智能解析试卷</h2>
        <button
          onClick={onBack}
          className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
        >
        return <span className="text-amber-600">Stopped manually</span>
        </button>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* 左侧：上传区域 */}
        <div className="bg-white rounded-xl shadow-sm p-6">
          <h3 className="text-lg font-semibold mb-4">上传试卷</h3>

          <div className="border-2 border-dashed border-gray-200 rounded-xl p-6 text-center">
            <input
              type="file"
              accept="image/*,.pdf,.doc,.docx,.ppt,.pptx,.html,.htm,.epub,.txt,.md"
              onChange={handleFileSelect}
              className="hidden"
              id="paper-upload"
            />
            <label
              htmlFor="paper-upload"
              className="cursor-pointer flex flex-col items-center gap-2"
            >
              <ImageIcon className="w-10 h-10 text-gray-400" />
              <span className="text-gray-600 text-sm">
                {selectedFile ? selectedFile.name : '点击选择试卷'}
              </span>
              <span className="text-xs text-gray-400">
                JPG、PNG、PDF
              </span>
            </label>
          </div>

          {previewUrl && parseStage === 'idle' && (
            <div className="mt-4">
              <img
                src={previewUrl}
                alt="试卷预览"
                className="max-w-full rounded-lg shadow-sm"
              />
            </div>
          )}

          <div className="mt-4 flex items-center gap-2">
            <div className={`w-3 h-3 rounded-full ${parseStage === 'idle' ? 'bg-gray-300' :
              parseStage === 'done' ? 'bg-green-500' :
                parseStage === 'stopped' ? 'bg-amber-500' :
                parseStage === 'error' ? 'bg-red-500' :
                  'bg-blue-500 animate-pulse'
              }`} />
            {renderParseStatus()}
          </div>

          <div className="mt-4 flex gap-2">
            <button
              onClick={handleParse}
              disabled={!selectedFile || !['idle', 'done', 'error', 'stopped'].includes(parseStage)}
              className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm"
            >
              {['uploading', 'waiting', 'parsing'].includes(parseStage) ? (
                <>解析中...</>
              ) : (
                <>开始解析</>
              )}
            </button>
            {['uploading', 'waiting', 'parsing'].includes(parseStage) && (
              <button
                onClick={handleStopParse}
                className="px-4 py-2 bg-amber-100 text-amber-700 rounded-lg hover:bg-amber-200 text-sm"
              >
                Stop parse
              </button>
            )}
          </div>

          {batchId && (
            <div className="mt-3 text-xs text-gray-400 break-all">
              任务ID: {batchId}
            </div>
          )}

          {errorMessage && (
            <div className="mt-4 p-3 bg-red-50 rounded-lg">
              <p className="text-sm text-red-800">{errorMessage}</p>
            </div>
          )}
        </div>

        {/* 右侧：题目列表 */}
        <div className="bg-white rounded-xl shadow-sm p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold">题目列表</h3>
            {questions.length > 0 && (
              <span className="text-sm text-gray-500">{questions.length} 题</span>
            )}
          </div>

          {questions.length === 0 && (
            <div className="h-80 flex items-center justify-center text-gray-400 text-sm">
              解析完成后显示题目
            </div>
          )}

          {questions.length > 0 && solveProgress === 'idle' && (
            <div className="mb-3 flex flex-wrap gap-2">
              <button
                onClick={addEditableQuestion}
                className="px-3 py-2 text-xs border border-gray-300 rounded hover:bg-gray-50"
              >
                Add question
              </button>
              <button
                onClick={sortEditableQuestions}
                className="px-3 py-2 text-xs border border-gray-300 rounded hover:bg-gray-50"
              >
                Sort by number
              </button>
            </div>
          )}
          {questions.length > 0 && (
            <div className="space-y-4 max-h-[calc(100vh-320px)] overflow-y-auto">
              {Object.entries(groupedQuestions).map(([type, qs]) => (
                <div key={type}>
                  <h4 className="text-sm font-medium text-indigo-600 mb-2">{type}</h4>
                  <div className="space-y-2">
                    {qs.map((q) => {
                      const editable = editableQuestions.find((item) => item.number === q.number)
                      const taskId = Object.keys(questionStatuses).find((key) => questionStatuses[key].number === q.number)
                      const status = taskId ? questionStatuses[taskId] : null
                      const statusColor = !status ? 'bg-gray-300' :
                        status.status === 'completed' ? 'bg-green-500' :
                          status.status === 'failed' ? 'bg-red-500' :
                            status.status === 'manual' ? 'bg-amber-500' :
                              status.status === 'cancelled' ? 'bg-gray-500' :
                                'bg-blue-500 animate-pulse'
                      const canEditBeforeSolve = solveProgress === 'idle'
                      // 始终显示复选框：解题前使用题号作为标识，解题后使用task_id
                      const questionId = `number:${q.number}`
                      const isSelectedBeforeSolve = selectedTaskIds.includes(questionId)
                      const isSelectedAfterSolve = status?.task_id ? selectedTaskIds.includes(status.task_id) : false
                      const isSelected = isSelectedBeforeSolve || isSelectedAfterSolve
                      const handleToggle = () => {
                        if (status?.task_id) {
                          toggleTaskSelection(status.task_id)
                        } else {
                          toggleTaskSelection(questionId)
                        }
                      }

                      return (
                        <div key={q.number} className="text-sm p-3 bg-gray-50 rounded border border-gray-200 space-y-2">
                          <div className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={handleToggle}
                            />
                            <div className={`w-2 h-2 rounded-full ${statusColor}`} />
                            <span className="font-medium">Q{q.number}</span>
                            {canEditBeforeSolve && (
                              <div className="ml-auto flex items-center gap-1">
                                <button type="button" onClick={() => moveQuestionByOffset(q.number, -1)} className="px-2 py-1 text-[11px] border rounded hover:bg-white">Up</button>
                                <button type="button" onClick={() => moveQuestionByOffset(q.number, 1)} className="px-2 py-1 text-[11px] border rounded hover:bg-white">Down</button>
                                <button type="button" onClick={() => deleteEditableQuestion(q.number)} className="px-2 py-1 text-[11px] border border-red-200 text-red-600 rounded hover:bg-red-50">Delete</button>
                              </div>
                            )}
                            {status && (
                              <span className="text-xs text-gray-500 ml-auto">
                                {status.status === 'completed' ? '完成' :
                                  status.status === 'failed' ? '失败' :
                                    status.status === 'manual' ? '人工处理' :
                                      status.status === 'cancelled' ? '已暂停' :
                                        status.status === 'solving' ? '解题中' :
                                          status.status === 'reviewing' ? '审查中' :
                                            status.status === 'formatting' ? '排版中' :
                                              status.status}
                              </span>
                            )}
                          </div>

                          <div className="grid grid-cols-1 gap-2">
                            <div className="grid grid-cols-3 gap-2">
                              <input
                                type="number"
                                value={editable?.number ?? q.number}
                                disabled={!canEditBeforeSolve}
                                onChange={(event) => updateEditableQuestion(q.number, { number: parseInt(event.target.value || '0', 10) || q.number })}
                                placeholder="No."
                                className="w-full rounded border border-gray-300 px-2 py-1 text-xs disabled:bg-gray-100"
                              />
                              <input
                                value={editable?.type || ''}
                                disabled={!canEditBeforeSolve}
                                onChange={(event) => updateEditableQuestion(q.number, { type: event.target.value })}
                                placeholder="Type"
                                className="col-span-2 w-full rounded border border-gray-300 px-2 py-1 text-xs disabled:bg-gray-100"
                              />
                            </div>
                            <textarea
                              value={editable?.content || ''}
                              disabled={!canEditBeforeSolve}
                              onChange={(event) => updateEditableQuestion(q.number, { content: event.target.value })}
                              placeholder="Question text"
                              className="w-full min-h-16 rounded border border-gray-300 px-2 py-1 text-xs disabled:bg-gray-100"
                            />
                            {(editable?.images || []).length > 0 && (
                              <div className="space-y-2">
                                <div className="text-[11px] text-gray-500">Image review</div>
                                <div className="grid grid-cols-2 gap-2">
                                  {(editable?.images || []).map((imageUrl, imageIndex) => (
                                    <div key={`${q.number}-${imageIndex}`} className="rounded border border-gray-200 bg-white p-2 space-y-2">
                                      <img src={imageUrl} alt={`question-${q.number}-${imageIndex + 1}`} className="h-24 w-full rounded object-contain bg-gray-50" />
                                      {canEditBeforeSolve && (
                                        <div className="flex flex-wrap gap-1">
                                          <button type="button" onClick={() => {
                                            const raw = window.prompt('Target question number', String(q.number + 1))
                                            if (!raw) return
                                            const targetNumber = parseInt(raw, 10)
                                            if (Number.isNaN(targetNumber)) {
                                              setErrorMessage('Target question number must be numeric')
                                              return
                                            }
                                            setErrorMessage(null)
                                            moveImageToQuestion(q.number, imageUrl, targetNumber)
                                          }} className="px-2 py-1 text-[11px] border rounded hover:bg-gray-50">Move</button>
                                          <button type="button" onClick={() => removeQuestionImage(q.number, imageUrl)} className="px-2 py-1 text-[11px] border border-red-200 text-red-600 rounded hover:bg-red-50">Remove</button>
                                        </div>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

          {questions.length > 0 && solveProgress === 'idle' && (
            <div className="mt-4 pt-4 border-t flex gap-2">
              <button
                onClick={() => {
                  // 全选/取消全选：检查是否所有题目都被选中
                  const allSelected = editableQuestions.every((q) => selectedTaskIds.includes(`number:${q.number}`))
                  if (allSelected) {
                    // 取消全选：移除所有 number:X 格式的勾选
                    setSelectedTaskIds((prev) => prev.filter((id) => !id.startsWith('number:')))
                  } else {
                    // 全选：添加所有题号为勾选状态
                    setSelectedTaskIds((prev) => {
                      const withoutNumbers = prev.filter((id) => !id.startsWith('number:'))
                      return [...withoutNumbers, ...editableQuestions.map((q) => `number:${q.number}`)]
                    })
                  }
                }}
                className="px-3 py-2 text-xs border border-gray-300 rounded hover:bg-gray-50"
              >
                {editableQuestions.every((q) => selectedTaskIds.includes(`number:${q.number}`))
                  ? '取消全选'
                  : '全选'}
              </button>
              <button
                onClick={handleStartSolve}
                className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 text-sm"
              >
                {selectedTaskIds.filter((id) => id.startsWith('number:')).length > 0
                  ? `开始解题 (${selectedTaskIds.filter((id) => id.startsWith('number:')).length})`
                  : '开始解题'}
              </button>
            </div>
          )}

          {solveProgress === 'solving' && (
            <div className="mt-4 pt-4 border-t">
              <div className="flex items-center gap-2 text-sm text-blue-600">
                <div className="w-3 h-3 rounded-full bg-blue-500 animate-pulse" />
                解题中...
              </div>
              <p className="text-xs text-gray-500 mt-1">
                {Object.values(questionStatuses).filter(s => s.status === 'completed').length} / {questions.length} 题完成
              </p>
              <label className="mt-3 inline-flex items-center gap-2 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={skipReviewOnRetry}
                  onChange={(e) => setSkipReviewOnRetry(e.target.checked)}
                />
                <span>批量重试时跳过 Review，直接进入 Formatter</span>
              </label>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button
                  onClick={selectAllRunning}
                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                >
                  全选可暂停
                </button>
                <button
                  onClick={selectAllRetryable}
                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                >
                  全选可重试
                </button>
                <button
                  onClick={clearSelectedTasks}
                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                >
                  清空勾选
                </button>
                <div className="text-xs text-gray-500 flex items-center justify-end">
                  已勾选 {selectedTaskIds.length} 题
                </div>
              </div>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={handleBatchPause}
                  disabled={batchActionLoading || selectedTaskIds.length === 0}
                  className="flex-1 px-3 py-2 text-xs rounded border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-50"
                >
                  {batchActionLoading ? '处理中...' : '批量暂停'}
                </button>
                <button
                  onClick={handleBatchRetry}
                  disabled={batchActionLoading || selectedTaskIds.length === 0}
                  className="flex-1 px-3 py-2 text-xs rounded border border-blue-300 text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                >
                  {batchActionLoading ? '处理中...' : '批量重试'}
                </button>
              </div>
            </div>
          )}

          {solveProgress === 'completed' && (
            <div className="mt-4 pt-4 border-t">
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">试卷科目</label>
                  <input
                    type="text"
                    value={paperSubject}
                    onChange={(e) => setPaperSubject(e.target.value)}
                    placeholder="如：电工基础"
                    className="w-full px-3 py-2 border rounded text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">试卷名称/年份</label>
                  <input
                    type="text"
                    value={paperTitle}
                    onChange={(e) => setPaperTitle(e.target.value)}
                    placeholder="如：2024-2025学年期末考试"
                    className="w-full px-3 py-2 border rounded text-sm"
                  />
                </div>
              </div>
              <button
                onClick={handleExportDocx}
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 text-sm"
              >
                导出DOCX
              </button>
            </div>
          )}

          {solveProgress === 'error' && (
            <div className="mt-4 pt-4 border-t">
              <p className="text-sm text-red-600 mb-3">部分题目解题失败</p>
              <label className="mb-3 inline-flex items-center gap-2 text-xs text-gray-600">
                <input
                  type="checkbox"
                  checked={skipReviewOnRetry}
                  onChange={(e) => setSkipReviewOnRetry(e.target.checked)}
                />
                <span>批量重试时跳过 Review，直接进入 Formatter</span>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={selectAllRunning}
                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                >
                  全选可暂停
                </button>
                <button
                  onClick={selectAllRetryable}
                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                >
                  全选可重试
                </button>
                <button
                  onClick={clearSelectedTasks}
                  className="px-2 py-1 text-xs border rounded hover:bg-gray-50"
                >
                  清空勾选
                </button>
                <div className="text-xs text-gray-500 flex items-center justify-end">
                  已勾选 {selectedTaskIds.length} 题
                </div>
              </div>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={handleBatchPause}
                  disabled={batchActionLoading || selectedTaskIds.length === 0}
                  className="flex-1 px-3 py-2 text-xs rounded border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-50"
                >
                  {batchActionLoading ? '处理中...' : '批量暂停'}
                </button>
                <button
                  onClick={handleBatchRetry}
                  disabled={batchActionLoading || selectedTaskIds.length === 0}
                  className="flex-1 px-3 py-2 text-xs rounded border border-blue-300 text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                >
                  {batchActionLoading ? '处理中...' : '批量重试'}
                </button>
              </div>
            </div>
          )}

          {solveProgress === 'solving' && solveResult && (
            <div className="mt-4 p-3 bg-green-50 rounded-lg">
              <p className="text-sm text-green-800">
                解题流程已启动 ({solveResult.question_count} 题)
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default App
