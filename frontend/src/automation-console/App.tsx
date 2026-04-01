import { useEffect, useMemo, useState } from 'react'

import {
    confirmSubmit,
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

    const hasRun = runId.length > 0

    const refresh = async () => {
        if (!runId) return
        const [statusResp, taskResp, logResp] = await Promise.all([
            getRunStatus(runId),
            listTasks(runId),
            listLogs(runId),
        ])
        setRunState(statusResp.state)
        setTasks(taskResp.items)
        setLogs(logResp)
        if (activeReview) {
            const latest = taskResp.items.find((item) => item.task_id === activeReview.task_id) || null
            setActiveReview(latest)
        }
    }

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
                        const session = await startSession(username, password, mode)
                        setRunId(session.run_id)
                        setRunState(session.state)
                        await refresh()
                    }}
                    onScan={async () => {
                        await startScan(runId)
                    }}
                    onSelect={async () => {
                        await selectTasks(runId, selectedIds)
                        await refresh()
                    }}
                    onGrab={async () => {
                        await startGrab(runId)
                        await refresh()
                    }}
                    onSolve={async () => {
                        await startSolve(runId)
                        await refresh()
                    }}
                    onPause={async () => {
                        await pauseRun(runId)
                        await refresh()
                    }}
                    onResume={async () => {
                        await resumeRun(runId)
                        await refresh()
                    }}
                    onStop={async () => {
                        await stopRun(runId)
                        await refresh()
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
            </div>
        </div>
    )
}

export default App
