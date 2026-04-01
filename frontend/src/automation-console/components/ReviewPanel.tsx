import { useEffect, useState } from 'react'
import type { TaskItem } from '../types'

type Props = {
    task: TaskItem | null
    onSave: (taskId: string, analysis: string, extension: string) => Promise<void>
    onSubmit: (taskId: string) => Promise<void>
}

export function ReviewPanel({ task, onSave, onSubmit }: Props) {
    const [analysis, setAnalysis] = useState('')
    const [extension, setExtension] = useState('')

    useEffect(() => {
        if (!task) {
            setAnalysis('')
            setExtension('')
            return
        }
        setAnalysis(task.analysis_edited || task.analysis_markdown || '')
        setExtension(task.extension_edited || task.extension_text || '')
    }, [task])

    if (!task) {
        return <div className="rounded-xl border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">请选择 review_pending 任务进行复核</div>
    }

    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-3 text-lg font-semibold text-slate-800">提交前复核</h3>
            <div className="space-y-3">
                <textarea
                    className="h-36 w-full rounded border border-slate-300 p-2"
                    value={analysis}
                    onChange={(e) => setAnalysis(e.target.value)}
                    placeholder="答案及解析"
                />
                <textarea
                    className="h-24 w-full rounded border border-slate-300 p-2"
                    value={extension}
                    onChange={(e) => setExtension(e.target.value)}
                    placeholder="考点衍生"
                />
            </div>
            <div className="mt-3 flex gap-2">
                <button className="rounded bg-amber-600 px-3 py-2 text-sm text-white" onClick={() => onSave(task.task_id, analysis, extension)}>
                    保存复核
                </button>
                <button className="rounded bg-emerald-600 px-3 py-2 text-sm text-white" onClick={() => onSubmit(task.task_id)}>
                    确认提交
                </button>
            </div>
        </div>
    )
}
