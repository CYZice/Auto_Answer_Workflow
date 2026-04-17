type Props = {
    hasRun: boolean
    runId: string
    runState?: 'idle' | 'running' | 'paused' | 'stopped'
    username: string
    password: string
    mode: 'headed' | 'headless'
    schools: { school_id: number; school_name: string }[]
    selectedSchoolId: number | undefined
    onUsernameChange: (value: string) => void
    onPasswordChange: (value: string) => void
    onModeChange: (value: 'headed' | 'headless') => void
    onSchoolChange: (value: number | undefined) => void
    onStart: () => Promise<void>
    onScan: () => Promise<void>
    onSelect: () => Promise<void>
    onGrab: () => Promise<void>
    onSolve: () => Promise<void>
    onPause: () => Promise<void>
    onResume: () => Promise<void>
    onStop: () => Promise<void>
}

export function RunControls({ hasRun, runId, runState, username, password, mode, schools, selectedSchoolId, onUsernameChange, onPasswordChange, onModeChange, onSchoolChange, onStart, onScan, onSelect, onGrab, onSolve, onPause, onResume, onStop }: Props) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-2 text-lg font-semibold text-slate-800">运行控制</h3>
            <p className="mb-3 text-xs text-slate-500">run_id: {runId || '未启动'}</p>
            <p className="mb-3 text-xs text-slate-500">state: {runState || 'idle'}</p>
            <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-4">
                <input
                    className="rounded border border-slate-300 px-2 py-2 text-sm"
                    placeholder="账号"
                    value={username}
                    readOnly
                    disabled
                    onChange={(e) => onUsernameChange(e.target.value)}
                />
                <input
                    className="rounded border border-slate-300 px-2 py-2 text-sm"
                    placeholder="密码"
                    type="password"
                    value={password}
                    readOnly
                    disabled
                    onChange={(e) => onPasswordChange(e.target.value)}
                />
                <select
                    className="rounded border border-slate-300 px-2 py-2 text-sm"
                    value={mode}
                    onChange={(e) => onModeChange(e.target.value as 'headed' | 'headless')}
                >
                    <option value="headed">headed</option>
                    <option value="headless">headless</option>
                </select>
                <select
                    className="rounded border border-slate-300 px-2 py-2 text-sm"
                    value={selectedSchoolId ?? ''}
                    onChange={(e) => onSchoolChange(e.target.value ? Number(e.target.value) : undefined)}
                >
                    <option value="">全部学校</option>
                    {schools.map((s) => (
                        <option key={s.school_id} value={s.school_id}>
                            {s.school_name}
                        </option>
                    ))}
                </select>
            </div>
            <div className="flex flex-wrap gap-2">
                <button className="rounded bg-indigo-600 px-3 py-2 text-sm text-white disabled:opacity-40" onClick={onStart} disabled={!username || !password}>启动会话</button>
                <button className="rounded bg-slate-700 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onScan}>扫描</button>
                <button className="rounded bg-slate-700 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onSelect}>确认勾选</button>
                <button className="rounded bg-slate-700 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onGrab}>接单</button>
                <button className="rounded bg-slate-700 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onSolve}>解题</button>
                <button className="rounded bg-yellow-600 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onPause}>暂停</button>
                <button className="rounded bg-green-700 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onResume}>继续</button>
                <button className="rounded bg-rose-700 px-3 py-2 text-sm text-white disabled:opacity-40" disabled={!hasRun} onClick={onStop}>硬停止</button>
            </div>
        </div>
    )
}
