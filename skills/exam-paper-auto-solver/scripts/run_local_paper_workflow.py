#!/usr/bin/env python3
import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def build_multipart_form_data(field_name: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----CodexSkill{uuid.uuid4().hex}"
    filename = file_path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()

    body = []
    body.append(f"--{boundary}\r\n".encode("utf-8"))
    body.append(
        (
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\n'
        ).encode("utf-8")
    )
    body.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.append(file_bytes)
    body.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(body), boundary


def request_json(
    url: str,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict | None = None,
) -> dict:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


def upload_file(base_url: str, file_path: Path) -> dict:
    body, boundary = build_multipart_form_data("file", file_path)
    req = urllib.request.Request(
        f"{base_url}/api/mineru/parse/file",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"upload failed: HTTP {exc.code} {detail}") from exc


def download_binary(url: str) -> bytes:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=600) as response:
        return response.read()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exam-paper workflow against local backend.")
    parser.add_argument("--file", required=True, help="Path to input PDF/image/document")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--paper-title", default="", help="Paper title")
    parser.add_argument("--paper-subject", default="", help="Paper subject")
    parser.add_argument("--workflow-template-id", default="", help="Optional template id")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling seconds")
    parser.add_argument("--max-wait", type=float, default=1800.0, help="Max seconds for each wait phase")
    parser.add_argument("--output-dir", default="./paper_run_output", help="Directory for outputs")
    parser.add_argument("--export-docx", action="store_true", help="Download DOCX when finished")
    return parser.parse_args()


def wait_for_parse(base_url: str, mineru_task_id: str, poll_interval: float, max_wait: float) -> dict:
    query = urllib.parse.urlencode({"poll_interval": poll_interval, "max_wait": max_wait})
    return request_json(
        f"{base_url}/api/mineru/parse/{mineru_task_id}/wait?{query}",
        method="POST",
    )


def wait_for_solve_completion(
    base_url: str,
    mineru_task_id: str,
    poll_interval: float,
    max_wait: float,
) -> dict:
    deadline = time.time() + max_wait
    last_status = None
    while time.time() < deadline:
        status = request_json(f"{base_url}/api/mineru/paper/{mineru_task_id}/status")
        total = status.get("total", 0)
        completed = status.get("completed", 0)
        results = status.get("results", [])
        terminal = sum(
            1 for item in results if item.get("status") in {"completed", "failed", "manual", "cancelled"}
        )
        current_snapshot = (completed, total, terminal)
        if current_snapshot != last_status:
            print(f"[solve] completed={completed}/{total}, terminal={terminal}/{total}")
            last_status = current_snapshot
        if total > 0 and terminal == total:
            return status
        time.sleep(poll_interval)
    raise TimeoutError(f"solve phase did not finish within {max_wait} seconds")


def main() -> int:
    args = parse_args()
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"input file not found: {file_path}")

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[upload] {file_path}")
    upload_result = upload_file(args.base_url.rstrip("/"), file_path)
    mineru_task_id = upload_result["mineru_task_id"]
    print(f"[upload] mineru_task_id={mineru_task_id}")

    parse_result = wait_for_parse(
        args.base_url.rstrip("/"),
        mineru_task_id,
        args.poll_interval,
        args.max_wait,
    )
    if parse_result.get("status") != "done":
        raise RuntimeError(f"parse did not complete successfully: {parse_result}")

    markdown_content = parse_result.get("markdown_content") or ""
    (output_dir / "parsed_markdown.md").write_text(markdown_content, encoding="utf-8")

    questions = request_json(f"{args.base_url.rstrip('/')}/api/mineru/paper/{mineru_task_id}/questions")
    (output_dir / "questions.json").write_text(
        json.dumps(questions, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[parse] saved markdown and questions to {output_dir}")

    solve_payload = {
        "paper_title": args.paper_title,
        "paper_subject": args.paper_subject,
    }
    if args.workflow_template_id:
        solve_payload["workflow_template_id"] = args.workflow_template_id

    solve_result = request_json(
        f"{args.base_url.rstrip('/')}/api/mineru/paper/{mineru_task_id}/solve",
        method="POST",
        payload=solve_payload,
    )
    (output_dir / "solve_start.json").write_text(
        json.dumps(solve_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"[solve] started {solve_result.get('question_count', 0)} questions, "
        f"thread_id={solve_result.get('thread_id', '')}"
    )

    final_status = wait_for_solve_completion(
        args.base_url.rstrip("/"),
        mineru_task_id,
        args.poll_interval,
        args.max_wait,
    )
    (output_dir / "paper_status.json").write_text(
        json.dumps(final_status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.export_docx:
        query = urllib.parse.urlencode(
            {
                "paper_title": args.paper_title,
                "paper_subject": args.paper_subject,
            }
        )
        docx_bytes = download_binary(
            f"{args.base_url.rstrip('/')}/api/mineru/paper/{mineru_task_id}/export/docx?{query}"
        )
        docx_path = output_dir / "paper_answers.docx"
        docx_path.write_bytes(docx_bytes)
        print(f"[export] saved {docx_path}")

    print(f"[done] output_dir={output_dir}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
