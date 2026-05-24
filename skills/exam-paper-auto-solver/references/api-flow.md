# API Flow

## End-to-end sequence

1. `POST /api/mineru/parse/file`
2. `POST /api/mineru/parse/{mineru_task_id}/wait`
3. `GET /api/mineru/paper/{mineru_task_id}/questions`
4. `POST /api/mineru/paper/{mineru_task_id}/solve`
5. `GET /api/mineru/paper/{mineru_task_id}/status`
6. `GET /api/mineru/paper/{mineru_task_id}/export/docx`

## `questions_override` payload shape

```json
{
  "paper_title": "2026 期末试卷",
  "paper_subject": "数学",
  "questions_override": [
    {
      "number": 1,
      "type": "选择题",
      "content": "题干文本",
      "images": ["data:image/png;base64,..."]
    }
  ]
}
```

## Practical rules

- Prefer `GET /questions` before override so you start from the backend’s parse result.
- Keep numbering stable; downstream status sorting depends on `number`.
- Preserve `data:` image URLs when they exist; they are already normalized for the solver.
- If only a subset needs correction, resubmit the full corrected list, not partial patches.
