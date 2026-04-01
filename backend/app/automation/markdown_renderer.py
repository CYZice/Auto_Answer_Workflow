from __future__ import annotations

import os
import uuid
from pathlib import Path

from markdown_it import MarkdownIt


class MarkdownRenderer:
    def __init__(self, output_dir: str = "./automation_renders"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self._md = MarkdownIt("commonmark")

    def split_answer(self, final_markdown: str) -> tuple[str, str]:
        text = (final_markdown or "").strip()
        anchor = "【考点延伸】"
        idx = text.find(anchor)
        if idx < 0:
            return text, ""
        analysis = text[:idx].strip()
        extension = text[idx + len(anchor) :].strip()
        if analysis.startswith("【正解】"):
            analysis = analysis[len("【正解】") :].strip()
        return analysis, extension

    def render_analysis_to_html(self, analysis_markdown: str) -> str:
        return self._md.render(analysis_markdown or "")

    def save_analysis_snapshot(self, analysis_markdown: str) -> str:
        file_name = f"analysis_{uuid.uuid4().hex[:12]}.md"
        path = Path(self.output_dir) / file_name
        path.write_text(analysis_markdown or "", encoding="utf-8")
        return str(path)
