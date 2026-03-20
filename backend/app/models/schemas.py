from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from enum import Enum
from datetime import datetime

# --- Agent Reviewer 结构化输出契约 ---
class ReviewDecision(BaseModel):
    decision: Literal["PASS", "FAIL"] = Field(description="审查结论")
    reason: Optional[str] = Field(None, description="整体原因概括（FAIL 时必填）")
    issues: Optional[List[dict]] = Field(default_factory=list, description="具体错误点列表，包含 type 和 detail 字段")

# --- API 请求与响应契约 ---
class TaskStatus(str, Enum):
    QUEUED = "queued"
    SOLVING = "solving"
    REVIEWING = "reviewing"
    FORMATTING = "formatting"
    MANUAL = "manual"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class ModelConfig(BaseModel):
    model_name: Optional[str] = Field(default=None, description="模型名称")
    api_key: Optional[str] = Field(default=None, description="API Key (如未提供则使用系统环境变量)")
    base_url: Optional[str] = Field(default=None, description="API Base URL")
    max_tokens: Optional[int] = Field(default=4096, description="最大生成 Token 数")

class TaskCreateRequest(BaseModel):
    image_url: str = Field(..., description="题目图片本地存储路径或云端链接")
    solver_config: Optional[ModelConfig] = Field(default=None, description="Solver(解题)节点的大模型配置")
    reviewer_config: Optional[ModelConfig] = Field(default=None, description="Reviewer(审查)节点的大模型配置")
    formatter_config: Optional[ModelConfig] = Field(default=None, description="Formatter(排版)节点的大模型配置")

class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus

class ManualSubmitRequest(BaseModel):
    action: Literal["resume", "fail"] = Field(description="resume表示继续排版，fail表示放弃")
    draft_solution: Optional[str] = Field(None, description="人工修正后的解题内容")

class TaskDetailResponse(BaseModel):
    task_id: str
    thread_id: str
    image_url: str
    state: TaskStatus
    retry_count: int
    history: Optional[str] = None
    final_result: Optional[str] = None
    token_usage: Optional[str] = None
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # 允许直接从 SQLAlchemy 模型读取数据
