export type TaskStatus =
    | 'discovered'
    | 'selected'
    | 'grabbed'
    | 'solving'
    | 'solve_failed'
    | 'filled'
    | 'review_pending'
    | 'ready_to_submit'
    | 'submitting'
    | 'submitted'
    | 'failed_submit'
    | 'skipped'
    | 'paused'
    | 'stopped'

export interface TaskItem {
    task_id: string
    run_id: string
    school_name: string
    topic_title: string
    topic_image_url?: string | null
    status: TaskStatus
    final_markdown?: string | null
    analysis_markdown?: string | null
    extension_text?: string | null
    analysis_edited?: string | null
    extension_edited?: string | null
    error_message?: string | null
}

export interface TaskListResp {
    total: number
    page: number
    page_size: number
    items: TaskItem[]
}

export interface LogItem {
    id: number
    run_id: string
    task_id?: string | null
    school_name?: string | null
    step: string
    level: string
    message: string
    created_at?: string | null
}

export interface RunStatus {
    run_id: string
    mode: 'headed' | 'headless'
    state: 'idle' | 'running' | 'paused' | 'stopped'
    current_task_id?: string | null
}
