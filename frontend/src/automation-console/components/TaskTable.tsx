import type { TaskItem } from '../types'

type Props = {
    tasks: TaskItem[]
    selected: Set<string>
    onToggle: (taskId: string) => void
    onPickReview: (task: TaskItem) => void
}

export function TaskTable({ tasks, selected, onToggle, onPickReview }: Props) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-3 text-lg font-semibold text-slate-800">任务列表</h3>
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
                                        disabled={task.status !== 'discovered' && task.status !== 'selected'}
                                    />
                                </td>
                                <td className="p-2">{task.school_name}</td>
                                <td className="p-2">{task.topic_title}</td>
                                <td className="p-2">{task.status}</td>
                                <td className="p-2">
                                    <button
                                        className="rounded bg-slate-800 px-2 py-1 text-xs text-white disabled:opacity-40"
                                        onClick={() => onPickReview(task)}
                                        disabled={task.status !== 'review_pending' && task.status !== 'ready_to_submit'}
                                    >
                                        复核
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}
