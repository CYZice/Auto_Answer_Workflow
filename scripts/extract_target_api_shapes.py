"""从 AUTOTEST 历史抓包只提取端点和字段类型；绝不输出请求值、Header 或响应正文。"""

import json
import subprocess
from collections import defaultdict
from urllib.parse import urlsplit


CAPTURES = (
    "backend/captured_traffic/captured_20260410_231013.jsonl",
    "backend/captured_traffic/full_capture_20260410_231325.jsonl",
    "backend/captured_traffic/full_capture_20260410_232657.jsonl",
)


def main() -> None:
    endpoints: dict[str, dict] = defaultdict(lambda: {"methods": set(), "fields": {}, "statuses": set(), "sources": set()})
    for capture in CAPTURES:
        raw = subprocess.check_output(["git", "show", f"AUTOTEST:{capture}"], text=True, encoding="utf-8", errors="replace")
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            path = urlsplit(str(event.get("url") or "")).path
            if not path.startswith("/admin/"):
                continue
            entry = endpoints[path]
            entry["methods"].add(str(event.get("method") or "UNKNOWN"))
            if isinstance(event.get("response_status"), int):
                entry["statuses"].add(event["response_status"])
            entry["sources"].add(capture)
            post_data = event.get("post_data")
            if isinstance(post_data, str):
                try:
                    payload = json.loads(post_data)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    for key, value in payload.items():
                        entry["fields"][key] = type(value).__name__
    safe = []
    for path, entry in sorted(endpoints.items()):
        safe.append({"path": path, "methods": sorted(entry["methods"]), "request_field_types": entry["fields"], "observed_http_status": sorted(entry["statuses"]), "evidence_files": sorted(entry["sources"])})
    print(json.dumps({"endpoints": safe}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
