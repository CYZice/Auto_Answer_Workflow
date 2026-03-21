from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any
from enum import Enum
from datetime import datetime


# --- Agent Reviewer 结构化输出契约 ---
class ReviewDecision(BaseModel):
    is_pass: bool = Field(description="审查是否通过。通过为true，发现错误为false")
    feedback: str = Field(
        default="",
        description="如果不通过，请详细说明具体的错误点和原因；如果通过，可以留空或写'无'。",
    )


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
    model_config = {"protected_namespaces": ()}

    model_name: Optional[str] = Field(default=None, description="模型名称")
    api_key: Optional[str] = Field(
        default=None, description="API Key (如未提供则使用系统环境变量)"
    )
    base_url: Optional[str] = Field(default=None, description="API Base URL")
    max_tokens: Optional[int] = Field(default=4096, description="最大生成 Token 数")


class TaskCreateRequest(BaseModel):
    image_url: str = Field(..., description="题目图片本地存储路径或云端链接")
    solver_config: Optional[ModelConfig] = Field(
        default=None, description="Solver(解题)节点的大模型配置"
    )
    reviewer_config: Optional[ModelConfig] = Field(
        default=None, description="Reviewer(审查)节点的大模型配置"
    )
    formatter_config: Optional[ModelConfig] = Field(
        default=None, description="Formatter(排版)节点的大模型配置"
    )
    workflow_template_id: Optional[str] = Field(
        default=None, description="本次任务使用的提示词模板 ID"
    )
    entry_point: Optional[Literal["solver", "reviewer", "formatter"]] = Field(
        default="solver", description="自定义执行入口节点"
    )
    target_nodes: Optional[List[Literal["solver", "reviewer", "formatter"]]] = Field(
        default=None, description="本次执行的节点集合，必须是有序连续子链"
    )
    draft_solution: Optional[str] = Field(
        default=None, description="当跳过Solver时，初始注入的草稿内容"
    )


class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus


class ManualSubmitRequest(BaseModel):
    action: Literal["resume", "skip_review", "fail", "custom_run"] = Field(
        description="resume表示按失败节点恢复，skip_review表示跳过审查直接进入排版，fail表示放弃，custom_run表示按自定义节点执行"
    )
    draft_solution: Optional[str] = Field(None, description="人工修正后的解题内容")
    entry_point: Optional[Literal["solver", "reviewer", "formatter"]] = Field(
        default=None, description="自定义执行入口节点"
    )
    target_nodes: Optional[List[Literal["solver", "reviewer", "formatter"]]] = Field(
        default=None, description="本次执行的节点集合，必须是有序连续子链"
    )
    solver_config: Optional[ModelConfig] = Field(
        default=None, description="重试时使用的 Solver 模型配置"
    )
    reviewer_config: Optional[ModelConfig] = Field(
        default=None, description="重试时使用的 Reviewer 模型配置"
    )
    formatter_config: Optional[ModelConfig] = Field(
        default=None, description="重试时使用的 Formatter 模型配置"
    )
    workflow_template_id: Optional[str] = Field(
        default=None, description="重试时切换的提示词模板 ID"
    )


class FallbackNodesConfig(BaseModel):
    solver: List[str] = Field(default_factory=list)
    reviewer: List[str] = Field(default_factory=list)
    formatter: List[str] = Field(default_factory=list)


class FallbackConfig(BaseModel):
    global_models: List[str] = Field(default_factory=list, alias="global")
    nodes: FallbackNodesConfig = Field(default_factory=FallbackNodesConfig)

    model_config = {"populate_by_name": True}


class RuntimeSettingsResponse(BaseModel):
    active_template_id: str
    fallback: FallbackConfig


class RuntimeSettingsUpdateRequest(BaseModel):
    active_template_id: Optional[str] = None
    fallback: Optional[FallbackConfig] = None


class PromptNodeBundle(BaseModel):
    system: str = ""
    user: str = ""


class PromptTemplatePayload(BaseModel):
    name: str
    description: Optional[str] = ""
    prompts: Dict[str, PromptNodeBundle]


class PromptTemplateItemResponse(BaseModel):
    template_id: str
    name: str
    description: str = ""


class PromptTemplateDetailResponse(PromptTemplateItemResponse):
    prompts: Dict[str, PromptNodeBundle]


class PromptTemplateCreateRequest(BaseModel):
    template_id: str
    name: str
    description: Optional[str] = ""
    source_template_id: Optional[str] = None


class TaskDetailResponse(BaseModel):
    task_id: str
    thread_id: str
    image_url: str
    state: TaskStatus
    retry_count: int
    history: Optional[str] = None
    final_result: Optional[str] = None
    question_preview: Optional[str] = None
    answer_preview: Optional[str] = None
    token_usage: Optional[str] = None
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # 允许直接从 SQLAlchemy 模型读取数据


class AdminTaskListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[TaskDetailResponse]


class AdminTaskUpdateRequest(BaseModel):
    state: Optional[TaskStatus] = None
    history: Optional[str] = None
    final_result: Optional[str] = None
    error_code: Optional[str] = None
    manual_operator: Optional[str] = None


class AdminTaskUpdateResponse(BaseModel):
    message: str
    task: TaskDetailResponse


class ExportGroupItem(BaseModel):
    group_id: Optional[str] = Field(default=None)
    group_name: str = Field(default="未命名题型")
    task_ids: List[str] = Field(default_factory=list)


class AdminExportRequest(BaseModel):
    task_ids: List[str] = Field(default_factory=list)
    groups: List[ExportGroupItem] = Field(default_factory=list)
    paper_subject: Optional[str] = Field(default=None, description="试卷科目标题")
    paper_title: Optional[str] = Field(default=None, description="试卷名称/年份标题")


class PaperBuilderGroupItem(BaseModel):
    group_id: str
    group_name: str = Field(default="未命名题型")
    task_ids: List[str] = Field(default_factory=list)


class PaperBuilderDraftPayload(BaseModel):
    name: str = Field(default="默认排版草稿")
    paper_subject: Optional[str] = Field(default="")
    paper_title: Optional[str] = Field(default="")
    groups: List[PaperBuilderGroupItem] = Field(default_factory=list)


class PaperBuilderDraftResponse(PaperBuilderDraftPayload):
    draft_id: str
    updated_at: Optional[datetime] = None


class PaperBuilderDraftListResponse(BaseModel):
    items: List[PaperBuilderDraftResponse] = Field(default_factory=list)


class AdminLogItemResponse(BaseModel):
    id: int
    task_id: str
    node_name: str
    request_payload: Optional[str] = None
    response_payload: Optional[str] = None
    cost_tokens: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminLogListResponse(BaseModel):
    total: int
    items: List[AdminLogItemResponse]
