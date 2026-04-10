from typing import TypedDict, Optional, Literal


class AgentState(TypedDict):
    """
    LangGraph 图引擎流转的核心状态。
    每一次节点执行都会更新此状态，并持久化到 SQLite 中。
    """

    task_id: str
    image_url: str
    image_urls: list[str]

    # 当前流程的内部状态
    status: Literal[
        "queued",
        "solving",
        "reviewing",
        "formatting",
        "manual",
        "completed",
        "failed",
        "cancelled",
    ]

    # 节点流转数据
    draft_solution: Optional[str]  # Solver 输出的草稿 / 人工编辑后的草稿
    review_decision: Optional[str]  # PASS / FAIL
    review_feedback: Optional[str]  # Reviewer 给出的综合反馈（用于重试）
    final_result: Optional[str]  # 最终交付的 Markdown

    # 策略控制数据
    retry_count: int  # 当前重试次数（第 2 次审查仍失败即 failed）
    error_msg: Optional[str]  # 系统错误信息
    failed_node: Optional[
        Literal["solver", "reviewer", "formatter"]
    ]  # 失败节点，用于 resume 定位恢复入口
    total_tokens: int  # 累计消耗的 Token
    target_nodes: Optional[list[str]]  # 本次执行允许的节点集合（用于自定义工作流截断）

    # 动态模型配置
    agent_configs: Optional[
        dict
    ]  # 包含各个节点的模型配置，如 {"solver": {...}, "reviewer": {...}}
    workflow_template_id: Optional[str]  # 当前任务使用的提示词模板 ID
    question_text: Optional[str]  # MinerU 解析的题目文字（可选）
