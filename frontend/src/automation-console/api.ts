import axios from 'axios';
import type { LogItem, RunStatus, TaskListResp } from './types';

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://127.0.0.1:8080/api/automation';

const client = axios.create({
    baseURL: apiBaseUrl,
})

export async function startSession(username: string, password: string, mode: 'headed' | 'headless') {
    const { data } = await client.post<{ run_id: string; mode: 'headed' | 'headless'; state: RunStatus['state'] }>('/session/start', {
        username,
        password,
        mode,
    })
    return data
}

export async function startScan(run_id: string) {
    await client.post('/scan/start', { run_id })
}

export async function listTasks(run_id: string, status?: string) {
    const { data } = await client.get<TaskListResp>('/tasks', { params: { run_id, status } })
    return data
}

export async function selectTasks(run_id: string, task_ids: string[]) {
    await client.post('/tasks/select', { run_id, task_ids })
}

export async function deleteTasks(run_id: string, task_ids: string[]) {
    await client.post('/tasks/delete', { run_id, task_ids })
}

export async function startGrab(run_id: string) {
    await client.post('/grab/start', { run_id })
}

export async function startSolve(run_id: string) {
    await client.post('/solve/start', { run_id })
}

export async function saveReview(taskId: string, analysis_text: string, extension_text: string) {
    const { data } = await client.post<{ item: TaskListResp['items'][number] }>(`/task/${taskId}/review/save`, {
        analysis_text,
        extension_text,
    })
    return data.item
}

export async function confirmSubmit(taskId: string) {
    const { data } = await client.post<{ item: TaskListResp['items'][number] }>(`/task/${taskId}/confirm-submit`)
    return data.item
}

export async function pauseRun(run_id: string) {
    await client.post('/run/pause', { run_id })
}

export async function resumeRun(run_id: string) {
    await client.post('/run/resume', { run_id })
}

export async function stopRun(run_id: string) {
    await client.post('/run/stop', { run_id })
}

export async function getRunStatus(run_id: string) {
    const { data } = await client.get<RunStatus>('/run/status', { params: { run_id } })
    return data
}

export async function listLogs(run_id: string) {
    const { data } = await client.get<{ items: LogItem[] }>('/logs', { params: { run_id, limit: 200 } })
    return data.items
}
