import type { TaskItem } from '../types'

type Props = {
    tasks: TaskItem[]
    selected: Set<string>
    onToggle: (taskId: string) => void
    onPickReview: (task: TaskItem) => void
    onViewOriginal: (task: TaskItem) => void
    onBatchDelete: (taskIds: string[]) => void
}

export function TaskTable({ tasks, selected, onToggle, onPickReview, onViewOriginal, onBatchDelete }: Props) {
    const selectedIds = Array.from(selected)

    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
                <h3 className="text-lg font-semibold text-slate-800">任务列表</h3>
                <button
                    className="rounded bg-rose-600 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={selectedIds.length === 0}
                    onClick={() => onBatchDelete(selectedIds)}
                >
                    批量删除（{selectedIds.length}）
                </button>
            </div>
            <div className="max-h-80 overflow-auto">
                <table className="w-full text-left text-sm">
                    <thead className="sticky top-0 bg-slate-50">
                        <tr>
                            <th className="p-2">选中</th>
                            <th className="p-2">学校</th>
                            <th className="p-2">标题</th>
                            <th className="p-2">状态</th>
                            <th className="p-2">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {tasks.map((task) => (
                            <tr key={task.task_id} className="border-b border-slate-100">
                                <td className="p-2">
                                    <input
                                        type="checkbox"
                                        checked={selected.has(task.task_id)}
                                        onChange={() => onToggle(task.task_id)}
                                    />
                                </td>
                                <td className="p-2">{task.school_name}</td>
                                <td className="p-2">{task.topic_title}</td>
                                <td className="p-2">{task.status}</td>
                                <td className="p-2">
                                    <div className="flex flex-wrap gap-2">
                                        <button
                                            className="rounded bg-slate-800 px-2 py-1 text-xs text-white"
                                            onClick={() => onViewOriginal(task)}
                                        >
                                            查看原题
                                        </button>
                                        <button
                                            className="rounded bg-indigo-700 px-2 py-1 text-xs text-white disabled:opacity-40"
                                            onClick={() => onPickReview(task)}
                                            disabled={task.status !== 'review_pending' && task.status !== 'ready_to_submit'}
                                        >
                                            复核
                                        </button>
                                    </div>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}
