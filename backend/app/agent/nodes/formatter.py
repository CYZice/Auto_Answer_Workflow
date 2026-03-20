from app.agent.state import AgentState
from app.agent.nodes.llm_client import format_solution
from app.core.database import SessionLocal
from app.models.domain import Task


def format_node_sync(task_id: str):
    # 检查是否被外部干预熔断，并更新当前状态
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == task_id).first()
        if task:
            if task.state == "cancelled":
                return True
            task.state = "formatting"
            db.commit()
    return False


async def format_node(state: AgentState) -> AgentState:
    """
    Node C: Formatter (自动排版)
    """
    print(f"[Node] Formatter: Formatting task {state['task_id']}")

    import asyncio

    is_cancelled = await asyncio.to_thread(format_node_sync, state["task_id"])
    if is_cancelled:
        print(
            f"  [Formatter] Task {state['task_id']} was cancelled by external intervention."
        )
        return {
            **state,
            "status": "cancelled",
            "error_msg": "Task was manually cancelled.",
        }

    # 防御性编程
    draft_solution = state.get("draft_solution")
    if not draft_solution:
        return {
            **state,
            "status": "failed",
            "failed_node": "formatter",
            "error_msg": "Cannot format empty draft.",
        }

    agent_configs = state.get("agent_configs") or {}
    formatter_config = agent_configs.get("formatter") or {}
    image_url = state.get("image_url")

    try:
        # 调用大模型进行排版润色
        print(f"  -> Calling LLM (Formatter)...")
        result = await format_solution(
            draft_solution,
            image_url=image_url,
            model_config=formatter_config,
            task_id=state.get("task_id"),
        )

        return {
            **state,
            "status": "completed",
            "final_result": result["formatted_result"],
            "total_tokens": state.get("total_tokens", 0) + result["tokens"],
        }
    except Exception as e:
        print(f"  [Formatter] API Error: {e}")
        return {
            **state,
            "status": "failed",
            "failed_node": "formatter",
            "error_msg": f"LLM API Error in Formatter: {str(e)}",
        }
