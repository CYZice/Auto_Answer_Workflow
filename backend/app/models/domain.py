from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.sql import func
from app.core.database import Base
import json


class Task(Base):
    """
    持久化 Task 状态、历史、Token。
    字段定义完全遵循 PRD Schema。
    """

    __tablename__ = "tasks"
    __table_args__ = {"extend_existing": True}

    task_id = Column(String, primary_key=True, index=True, doc="唯一主键")
    thread_id = Column(
        String, index=True, nullable=False, doc="任务线程标识（用于串联多轮尝试）"
    )
    # 把 image_url 的类型改为 Text 以支持极长的 Base64 字符串
    image_url = Column(
        Text, nullable=False, doc="图片存储位置（本地路径或对象存储 URL 或 Base64）"
    )
    state = Column(
        String,
        nullable=False,
        default="queued",
        doc="queued/solving/reviewing/formatting/manual/completed/failed",
    )
    retry_count = Column(Integer, nullable=False, default=0, doc="当前已重试次数")

    # 复杂结构采用 JSON 字符串化存储
    history = Column(
        Text,
        nullable=True,
        doc="JSON：按时间记录每节点输入/输出摘要、审查结论、错误信息",
    )
    final_result = Column(Text, nullable=True, doc="最终交付 Markdown")
    question_preview = Column(Text, nullable=True, doc="按【正解】切分后的题目部分")
    answer_preview = Column(Text, nullable=True, doc="按【正解】切分后的答案部分")
    token_usage = Column(
        Text, nullable=True, doc="JSON：按轮次/节点聚合的输入/输出 token"
    )
    error_code = Column(
        String,
        nullable=True,
        doc="失败/异常分类（如 unrecognizable/timeout/provider_error 等）",
    )

    # 审计与时间戳
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), doc="创建时间"
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), doc="更新时间")
    manual_operator = Column(String, nullable=True, doc="人工处理人（如有）")
    manual_updated_at = Column(
        DateTime(timezone=True), nullable=True, doc="人工提交时间（如有）"
    )

    @property
    def image_urls(self) -> list[str]:
        urls: list[str] = []
        try:
            history_data = json.loads(self.history) if self.history else {}
            raw_urls = (
                history_data.get("image_urls") if isinstance(history_data, dict) else []
            )
            if isinstance(raw_urls, list):
                for raw_url in raw_urls:
                    if isinstance(raw_url, str):
                        cleaned = raw_url.strip()
                        if cleaned and cleaned not in urls:
                            urls.append(cleaned)
        except Exception:
            pass

        fallback = (self.image_url or "").strip()
        if not urls and fallback:
            urls.append(fallback)
        return urls


class AgentLog(Base):
    __tablename__ = "agent_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, index=True)
    node_name = Column(String)  # solver / reviewer / formatter
    request_payload = Column(Text)  # JSON string
    response_payload = Column(Text)  # JSON string
    cost_tokens = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
