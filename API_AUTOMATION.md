# Automation API 文档（MVP）

## Base Path
- `/api/automation`

## 1. 启动会话
- `POST /session/start`
- 请求:
```json
{
  "username": "your_account",
  "password": "your_password",
  "mode": "headed"
}
```
- 响应:
```json
{
  "run_id": "run_xxxxx",
  "mode": "headed",
  "state": "idle"
}
```

## 2. 启动扫描
- `POST /scan/start`
- 请求:
```json
{ "run_id": "run_xxxxx" }
```

## 3. 列出任务
- `GET /tasks?run_id=run_xxxxx&status=discovered&page=1&page_size=20`

## 4. 批量勾选任务
- `POST /tasks/select`
- 请求:
```json
{
  "run_id": "run_xxxxx",
  "task_ids": ["auto_1", "auto_2"]
}
```

## 5. 启动批量接单
- `POST /grab/start`
- 请求:
```json
{ "run_id": "run_xxxxx", "limit": 0 }
```

## 6. 启动批量解题
- `POST /solve/start`
- 请求:
```json
{ "run_id": "run_xxxxx", "limit": 0 }
```

## 7. 保存复核
- `POST /task/{task_id}/review/save`
- 请求:
```json
{
  "analysis_text": "修订后的解析",
  "extension_text": "修订后的考点衍生"
}
```

## 8. 确认提交
- `POST /task/{task_id}/confirm-submit`

## 9. 运行态控制
- `POST /run/pause`
- `POST /run/resume`
- `POST /run/stop`

请求示例:
```json
{ "run_id": "run_xxxxx" }
```

## 10. 查询日志
- `GET /logs?run_id=run_xxxxx&limit=200`

## 备注
- `stop` 为硬中断，会取消当前作业并关闭 run 对应浏览器会话。
- `review_pending` 默认 10 分钟自动转为 `skipped`。
- 默认 mock 模式，真实模式需设置:
  - `AUTOMATION_USE_MOCK=0`
  - `AUTOMATION_TARGET_URL=https://your-target-site`
