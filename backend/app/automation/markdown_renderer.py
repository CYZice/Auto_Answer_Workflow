from __future__ import annotations

import asyncio
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
            core_text = text
            extension = ""
        else:
            core_text = text[:idx].strip()
            extension = text[idx + len(anchor) :].strip()

        # 业务规则：优先识别“正解”，没有“正解”则识别“解析”。
        answer_marker = "【正解】"
        analysis_marker = "【解析】"

        answer_idx = core_text.find(answer_marker)
        analysis_idx = core_text.find(analysis_marker)

        if answer_idx >= 0:
            analysis = core_text[answer_idx + len(answer_marker) :].strip()
        elif analysis_idx >= 0:
            analysis = core_text[analysis_idx + len(analysis_marker) :].strip()
        else:
            analysis = core_text

        return analysis, extension

    def render_analysis_to_html(self, analysis_markdown: str) -> str:
        body = self._md.render(analysis_markdown or "")
        return f"""
<!doctype html>
<html>
    <head>
        <meta charset="utf-8" />
        <style>
            body {{
                margin: 0;
                padding: 28px;
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
                color: #0f172a;
                background: #ffffff;
                line-height: 1.65;
            }}
            h1, h2, h3, h4, h5 {{ margin: 0.5em 0 0.35em; }}
            p {{ margin: 0.4em 0; }}
            pre {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 12px;
                overflow-x: auto;
            }}
            code {{
                background: #f1f5f9;
                border-radius: 6px;
                padding: 2px 6px;
            }}
            blockquote {{
                margin: 0.8em 0;
                padding: 8px 14px;
                border-left: 4px solid #94a3b8;
                background: #f8fafc;
            }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #cbd5e1; padding: 6px 8px; }}
        </style>
    </head>
    <body>
        {body}
    </body>
</html>
""".strip()

    def save_analysis_snapshot(self, analysis_markdown: str) -> str:
        markdown_name = f"analysis_{uuid.uuid4().hex[:12]}.md"
        markdown_path = Path(self.output_dir) / markdown_name
        markdown_path.write_text(analysis_markdown or "", encoding="utf-8")

        png_name = f"analysis_{uuid.uuid4().hex[:12]}.png"
        png_path = Path(self.output_dir) / png_name

        try:
            html = self.render_analysis_to_html(analysis_markdown)
            asyncio.run(self._render_html_to_png(html, str(png_path)))
            return str(png_path)
        except Exception:
            # 渲染失败时回退为 markdown 文件，保障流程可继续。
            return str(markdown_path)

    async def _render_html_to_png(self, html: str, output_path: str) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 720})
                await page.set_content(html, wait_until="networkidle")
                height = await page.evaluate("Math.ceil(document.body.scrollHeight)")
                await page.set_viewport_size(
                    {"width": 1280, "height": max(720, int(height) + 24)}
                )
                await page.screenshot(path=output_path, full_page=True)
            finally:
                await browser.close()
