from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class AutomationTask(Base):
    __tablename__ = "automation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, unique=True, index=True, nullable=False)
    run_id = Column(String, index=True, nullable=False)
    school_name = Column(String, index=True, nullable=False)
    topic_title = Column(String, nullable=False, default="")
    topic_image_url = Column(Text, nullable=True)  # 图片 URL（JSON 数组格式，支持多图）
    topic_text = Column(Text, nullable=True)  # 网站 OCR 识别后的题目文字（带 LaTeX 公式）
    status = Column(String, index=True, nullable=False, default="discovered")

    final_markdown = Column(Text, nullable=True)
    analysis_markdown = Column(Text, nullable=True)
    extension_text = Column(Text, nullable=True)
    analysis_edited = Column(Text, nullable=True)
    extension_edited = Column(Text, nullable=True)

    retry_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    selected_by_user = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AutomationLog(Base):
    __tablename__ = "automation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, index=True, nullable=False)
    task_id = Column(String, index=True, nullable=True)
    school_name = Column(String, nullable=True)
    step = Column(String, nullable=False)
    level = Column(String, nullable=False, default="INFO")
    message = Column(Text, nullable=False)
    payload_summary = Column(Text, nullable=True)
    screenshot_path = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
