---
name: exam-paper-auto-solver
description: Use when the user wants to turn a PDF, image set, or exam paper document into split questions, solve each question with the existing agent workflow, and export a formatted paper or answer sheet. Covers MinerU parsing, question review and override, LangGraph solve/review/format execution, and DOCX export.
---

# Exam Paper Auto Solver

Use this skill for “整份试卷” processing, not single-question ad hoc solving.

## Preconditions

- Backend is this repo’s FastAPI service.
- `backend/.env` contains valid `MINERU_API_TOKEN`, `LLM_API_KEY`, and related model config.
- `pandoc` is installed if DOCX export is required.

## Default workflow

1. Start or verify the backend service.
2. Run `scripts/run_local_paper_workflow.py` with the source file.
3. Inspect generated `questions.json` if splitting quality matters.
4. If parsing is wrong, fetch/edit question entries and resubmit with `questions_override`.
5. Wait for all child tasks to finish, then export DOCX if needed.

## Preferred command

```bash
python3 skills/exam-paper-auto-solver/scripts/run_local_paper_workflow.py \
  --file /path/to/paper.pdf \
  --paper-title "2026 期末试卷" \
  --paper-subject "数学" \
  --export-docx \
  --output-dir /tmp/paper-run
```

## What the script does

- Uploads the file to `/api/mineru/parse/file`
- Waits on `/api/mineru/parse/{task_id}/wait`
- Saves parsed markdown and split questions locally
- Starts solving via `/api/mineru/paper/{task_id}/solve`
- Polls `/api/mineru/paper/{task_id}/status`
- Optionally downloads `/api/mineru/paper/{task_id}/export/docx`

## When manual override is required

Read `references/api-flow.md` and use `questions_override` when:

- MinerU merged two questions into one
- 题型标题识别错误
- 图片关联错位
- 题号顺序与原卷不一致

Override should preserve:

- `number`
- `type`
- `content`
- `images`

## Output expectations

- `parsed_markdown.md`: MinerU 原始解析文本
- `questions.json`: 拆题结果
- `paper_status.json`: 每题解题状态与最终结果
- `paper_answers.docx`: 可选导出结果

## Notes

- The current backend already contains the core “拆题 → 解题 → 审查 → 排版” capabilities. This skill is mainly an operational wrapper.
- For bulk or unstable inputs, prefer reviewing the parsed question list before starting solve.
