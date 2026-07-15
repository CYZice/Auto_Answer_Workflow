from sqlalchemy import Boolean, Column, String, Integer, DateTime, Text, ForeignKey
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
    question_text = Column(Text, nullable=True, doc="当前题干文字")
    image_urls_json = Column(Text, nullable=True, doc="JSON：当前输入图片列表")
    input_revision = Column(Integer, nullable=False, default=1, doc="输入版本")
    current_node = Column(String, nullable=True, doc="最近执行节点")
    workflow_type = Column(
        String, nullable=False, default="standard", index=True, doc="standard/errata/full_paper/automation"
    )
    source_kind = Column(String, nullable=True, index=True, doc="业务来源类型")
    source_id = Column(String, nullable=True, index=True, doc="来源项目 ID")
    source_item_id = Column(String, nullable=True, index=True, doc="来源题目 ID")

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
            explicit_urls = json.loads(self.image_urls_json) if self.image_urls_json else []
            if isinstance(explicit_urls, list):
                urls.extend(
                    cleaned
                    for value in explicit_urls
                    if isinstance(value, str) and (cleaned := value.strip())
                )
        except Exception:
            pass
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


class TaskArtifact(Base):
    """每个工作流节点的不可变产物，用于断点恢复和版本追踪。"""

    __tablename__ = "task_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, index=True, nullable=False)
    node_name = Column(String, index=True, nullable=False)
    input_revision = Column(Integer, nullable=False, default=1)
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MineruJob(Base):
    """本地文件对应的一次 MinerU v4 解析任务。"""

    __tablename__ = "mineru_jobs"

    job_id = Column(String, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=True)
    source_path = Column(Text, nullable=False)
    source_sha256 = Column(String, index=True, nullable=False)
    batch_id = Column(String, unique=True, index=True, nullable=True)
    data_id = Column(String, index=True, nullable=False)
    status = Column(String, index=True, nullable=False, default="uploading")
    options_json = Column(Text, nullable=False, default="{}")
    progress_json = Column(Text, nullable=True)
    result_dir = Column(Text, nullable=True)
    markdown_path = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    next_poll_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class TargetSystemTask(Base):
    """远端接题与本地工作流的一对一映射；不保存远端凭据。"""

    __tablename__ = "target_system_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    remote_task_id = Column(String, unique=True, index=True, nullable=False)
    title = Column(Text, nullable=True)
    source_json = Column(Text, nullable=True)
    image_paths_json = Column(Text, nullable=True)
    workflow_task_id = Column(String, index=True, nullable=True)
    status = Column(String, index=True, nullable=False, default="discovered")
    exam_point = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    delivery_order = Column(Integer, index=True, nullable=True)
    delivery_locked_at = Column(DateTime(timezone=True), nullable=True)
    filled_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    browser_screenshot_path = Column(Text, nullable=True)
    rendered_answer_path = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class TargetSystemDeliveryLock(Base):
    """全局串行交付锁；同一时刻只允许一个题目等待网页提交。"""

    __tablename__ = "target_system_delivery_locks"

    id = Column(Integer, primary_key=True, default=1)
    target_task_id = Column(Integer, unique=True, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ErrataJob(Base):
    __tablename__ = "errata_jobs"

    job_id = Column(String, primary_key=True, index=True)
    original_filename = Column(String, nullable=False)
    source_path = Column(Text, nullable=False)
    output_path = Column(Text, nullable=True)
    state = Column(String, nullable=False, default="extracting")
    error_msg = Column(Text, nullable=True)
    mineru_status = Column(String, nullable=False, default="not_requested")
    mineru_markdown = Column(Text, nullable=True)
    custom_anchors = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ErrataItem(Base):
    __tablename__ = "errata_items"

    item_id = Column(String, primary_key=True, index=True)
    job_id = Column(String, index=True, nullable=False)
    item_index = Column(Integer, nullable=False)
    task_id = Column(
        String,
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=True,
    )
    source_ref = Column(Text, nullable=True)
    question_text = Column(Text, nullable=True)
    original_answer = Column(Text, nullable=True)
    correction_opinion = Column(Text, nullable=True)
    existing_content = Column(Text, nullable=True)
    existing_paragraph_count = Column(Integer, nullable=False, default=0)
    evidence_json = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending")
    result_type = Column(String, nullable=True)
    final_text_markup = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    mineru_text = Column(Text, nullable=True)
    review_status = Column(String, nullable=False, default="pending")
    review_feedback = Column(Text, nullable=True)
    replace_existing = Column(Boolean, nullable=False, default=False)
    # 勘误工作流的唯一输入来源：题块原始材料包，而非下方的旧字段拆分结果。
    source_start_index = Column(Integer, nullable=True)
    source_end_index = Column(Integer, nullable=True)
    material_docx_path = Column(Text, nullable=True)
    material_paths_json = Column(Text, nullable=True)
    material_text = Column(Text, nullable=True)
    material_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PaperProject(Base):
    __tablename__ = "paper_projects"

    paper_id = Column(String, primary_key=True, index=True)
    original_filename = Column(String, nullable=False)
    source_path = Column(Text, nullable=False)
    output_path = Column(Text, nullable=True)
    state = Column(String, nullable=False, default="ready")
    error_msg = Column(Text, nullable=True)
    mineru_status = Column(String, nullable=False, default="not_requested")
    mineru_markdown = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PaperQuestion(Base):
    __tablename__ = "paper_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String, index=True, nullable=False)
    item_index = Column(Integer, nullable=False)
    stable_key = Column(String, nullable=False)
    group_name = Column(String, nullable=False, default="未分类")
    question_number = Column(String, nullable=False)
    question_text = Column(Text, nullable=False)
    image_paths_json = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    task_id = Column(String, index=True, nullable=True)
    state = Column(String, nullable=False, default="ready")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
