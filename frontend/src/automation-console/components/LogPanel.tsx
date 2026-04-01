import type { LogItem } from '../types'

type Props = {
    logs: LogItem[]
}

export function LogPanel({ logs }: Props) {
    return (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="mb-3 text-lg font-semibold text-slate-800">运行日志</h3>
            <div className="max-h-64 overflow-auto space-y-2 text-xs">
                {logs.map((item) => (
                    <div key={item.id} className="rounded border border-slate-100 bg-slate-50 p-2">
                        <div className="font-semibold text-slate-700">[{item.level}] {item.step}</div>
                        <div className="text-slate-600">{item.message}</div>
                    </div>
                ))}
            </div>
        </div>
    )
}
