from typing import TypedDict, Optional, Literal

class AgentState(TypedDict):
    """
    LangGraph 图引擎流转的核心状态。
    每一次节点执行都会更新此状态，并持久化到 SQLite 中。
    """
    task_id: str
    image_url: str
    
    # 当前流程的内部状态
    status: Literal["queued", "solving", "reviewing", "formatting", "manual", "completed", "failed"]
    
    # 节点流转数据
    draft_solution: Optional[str]      # Solver 输出的草稿 / 人工编辑后的草稿
    review_decision: Optional[str]     # PASS / FAIL
    review_feedback: Optional[str]     # Reviewer 给出的综合反馈（用于重试）
    final_result: Optional[str]        # 最终交付的 Markdown
    
    # 策略控制数据
    retry_count: int                   # 当前重试次数（第 2 次审查仍失败即 failed）
    error_msg: Optional[str]           # 系统错误信息
    total_tokens: int                  # 累计消耗的 Token
