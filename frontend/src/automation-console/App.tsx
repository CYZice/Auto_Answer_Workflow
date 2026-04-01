import { useEffect, useMemo, useState } from 'react'

import {
    confirmSubmit,
    deleteTasks,
    getRunStatus,
    listLogs,
    listTasks,
    pauseRun,
    resumeRun,
    saveReview,
    selectTasks,
    startGrab,
    startScan,
    startSession,
    startSolve,
    stopRun,
} from './api'
import { LogPanel } from './components/LogPanel'
import { ReviewPanel } from './components/ReviewPanel'
import { RunControls } from './components/RunControls'
import { TaskTable } from './components/TaskTable'
import type { LogItem, RunStatus, TaskItem } from './types'

const LAST_RUN_ID_KEY = 'automation_console_last_run_id'

function App() {
    const [username, setUsername] = useState('13320115908')
    const [password, setPassword] = useState('2011590xue')
    const [mode, setMode] = useState<'headed' | 'headless'>('headed')
    const [runId, setRunId] = useState('')
    const [tasks, setTasks] = useState<TaskItem[]>([])
    const [logs, setLogs] = useState<LogItem[]>([])
    const [runState, setRunState] = useState<RunStatus['state']>('idle')
    const [selected, setSelected] = useState<Set<string>>(new Set())
    const [activeReview, setActiveReview] = useState<TaskItem | null>(null)
    const [sourcePreview, setSourcePreview] = useState<TaskItem | null>(null)

    const hasRun = runId.length > 0

    const refresh = async () => {
        if (!runId) return
        const [statusResp, taskResp, logResp] = await Promise.allSettled([
            getRunStatus(runId),
            listTasks(runId),
            listLogs(runId),
        ])

        if (statusResp.status === 'fulfilled') {
            setRunState(statusResp.value.state)
        } else {
            setRunState('idle')
        }

        if (taskResp.status === 'fulfilled') {
            setTasks(taskResp.value.items)
            if (activeReview) {
                const latest = taskResp.value.items.find((item) => item.task_id === activeReview.task_id) || null
                setActiveReview(latest)
            }
        } else {
            setTasks([])
            setActiveReview(null)
        }

        if (logResp.status === 'fulfilled') {
            setLogs(logResp.value)
        } else {
            setLogs([])
        }
    }

    useEffect(() => {
        const savedRunId = window.localStorage.getItem(LAST_RUN_ID_KEY)
        if (savedRunId) {
            setRunId(savedRunId)
        }
    }, [])

    useEffect(() => {
        if (!runId) {
            window.localStorage.removeItem(LAST_RUN_ID_KEY)
            return
        }
        window.localStorage.setItem(LAST_RUN_ID_KEY, runId)
    }, [runId])

    useEffect(() => {
        if (!runId) return
        const timer = window.setInterval(() => {
            refresh().catch(() => {
                // polling errors are ignored; next tick will retry.
            })
        }, 1500)
        return () => window.clearInterval(timer)
    }, [runId, activeReview])

    const selectedIds = useMemo(() => Array.from(selected), [selected])

    const getErrorMessage = (error: unknown): string => {
        if (typeof error === 'object' && error !== null) {
            const maybeAxiosError = error as {
                response?: { data?: { detail?: string; message?: string } }
                message?: string
            }
            const detail = maybeAxiosError.response?.data?.detail || maybeAxiosError.response?.data?.message
            if (detail) return String(detail)
            if (maybeAxiosError.message) return String(maybeAxiosError.message)
        }
        return '未知错误'
    }

    const runAction = async (
        name: string,
        action: () => Promise<void | boolean>,
        successMessage?: string,
    ) => {
        try {
            const shouldShowSuccess = await action()
            if (successMessage && shouldShowSuccess !== false) {
                window.alert(successMessage)
            }
        } catch (error) {
            window.alert(`${name}失败: ${getErrorMessage(error)}`)
        }
    }

    return (
        <div className="min-h-screen bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-50 p-6">
            <div className="mx-auto max-w-7xl space-y-4">
                <h1 className="text-3xl font-black tracking-tight text-slate-900">自动化接题控制台</h1>
                <RunControls
                    hasRun={hasRun}
                    runId={runId}
                    runState={runState}
                    username={username}
                    password={password}
                    mode={mode}
                    onUsernameChange={setUsername}
                    onPasswordChange={setPassword}
                    onModeChange={setMode}
                    onStart={async () => {
                        await runAction('启动会话', async () => {
                            const session = await startSession(username, password, mode)
                            setRunId(session.run_id)
                            setRunState(session.state)
                            await refresh()
                        }, '启动会话成功')
                    }}
                    onScan={async () => {
                        await runAction('扫描', async () => {
                            await startScan(runId)
                            await refresh()
                        }, '扫描已启动，请留意运行日志')
                    }}
                    onSelect={async () => {
                        await runAction('确认勾选', async () => {
                            if (selectedIds.length === 0) {
                                window.alert('请先勾选至少一条任务')
                                return false
                            }
                            await selectTasks(runId, selectedIds)
                            await refresh()
                            return true
                        }, '确认勾选成功')
                    }}
                    onGrab={async () => {
                        await runAction('接单', async () => {
                            if (selectedIds.length > 0) {
                                await selectTasks(runId, selectedIds)
                            }
                            await startGrab(runId)
                            await refresh()
                        }, '接单已启动，请留意运行日志')
                    }}
                    onSolve={async () => {
                        await runAction('解题', async () => {
                            await startSolve(runId)
                            await refresh()
                        }, '解题已启动，请留意运行日志')
                    }}
                    onPause={async () => {
                        await runAction('暂停', async () => {
                            await pauseRun(runId)
                            await refresh()
                        }, '已暂停')
                    }}
                    onResume={async () => {
                        await runAction('继续', async () => {
                            await resumeRun(runId)
                            await refresh()
                        }, '已恢复运行')
                    }}
                    onStop={async () => {
                        await runAction('硬停止', async () => {
                            await stopRun(runId)
                            await refresh()
                        }, '已发送硬停止指令')
                    }}
                />

                <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
                    <div className="space-y-4 xl:col-span-2">
                        <TaskTable
                            tasks={tasks}
                            selected={selected}
                            onToggle={(taskId) => {
                                setSelected((prev) => {
                                    const next = new Set(prev)
                                    if (next.has(taskId)) {
                                        next.delete(taskId)
                                    } else {
                                        next.add(taskId)
                                    }
                                    return next
                                })
                            }}
                            onPickReview={setActiveReview}
                            onViewOriginal={setSourcePreview}
                            onBatchDelete={async (taskIds) => {
                                if (!runId || taskIds.length === 0) return
                                const ok = window.confirm(`确认删除 ${taskIds.length} 条任务吗？`)
                                if (!ok) return
                                await deleteTasks(runId, taskIds)
                                setSelected((prev) => {
                                    const next = new Set(prev)
                                    taskIds.forEach((id) => next.delete(id))
                                    return next
                                })
                                if (activeReview && taskIds.includes(activeReview.task_id)) {
                                    setActiveReview(null)
                                }
                                if (sourcePreview && taskIds.includes(sourcePreview.task_id)) {
                                    setSourcePreview(null)
                                }
                                await refresh()
                            }}
                        />
                        <LogPanel logs={logs} />
                    </div>
                    <ReviewPanel
                        task={activeReview}
                        onSave={async (taskId, analysis, extension) => {
                            await saveReview(taskId, analysis, extension)
                            await refresh()
                        }}
                        onSubmit={async (taskId) => {
                            await confirmSubmit(taskId)
                            await refresh()
                        }}
                    />
                </div>

                {hasRun && (
                    <button
                        className="rounded bg-slate-900 px-3 py-2 text-sm text-white"
                        onClick={async () => {
                            await refresh()
                        }}
                    >
                        刷新
                    </button>
                )}

                {sourcePreview && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/55 p-4">
                        <div className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-xl bg-white p-4 shadow-2xl">
                            <div className="mb-3 flex items-center justify-between gap-2">
                                <h3 className="text-lg font-semibold text-slate-900">原题与原图</h3>
                                <button
                                    className="rounded bg-slate-800 px-2 py-1 text-xs text-white"
                                    onClick={() => setSourcePreview(null)}
                                >
                                    关闭
                                </button>
                            </div>

                            <div className="space-y-3 text-sm text-slate-700">
                                <div>
                                    <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">题目文本</div>
                                    <div className="rounded border border-slate-200 bg-slate-50 p-3 leading-7">{sourcePreview.topic_title || '暂无题目文本'}</div>
                                </div>

                                <div>
                                    <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">原图链接</div>
                                    {sourcePreview.topic_image_url ? (
                                        <div className="space-y-2">
                                            <a
                                                href={sourcePreview.topic_image_url}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="inline-block rounded bg-sky-600 px-3 py-1.5 text-xs font-medium text-white"
                                            >
                                                新窗口打开原图
                                            </a>
                                            <img
                                                src={sourcePreview.topic_image_url}
                                                alt="原题图片"
                                                className="max-h-[55vh] w-full rounded border border-slate-200 object-contain"
                                            />
                                        </div>
                                    ) : (
                                        <div className="rounded border border-amber-200 bg-amber-50 p-2 text-amber-700">当前任务未抓取到题图链接</div>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}

export default App
