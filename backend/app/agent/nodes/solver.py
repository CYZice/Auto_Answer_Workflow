from app.agent.state import AgentState
from app.agent.nodes.llm_client import solve_image
from app.agent.nodes.llm_client import coerce_token_count
from app.core.database import SessionLocal
from app.models.domain import Task


def solve_node_sync(task_id: str):
    # 检查是否被外部干预熔断，并更新当前状态
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == task_id).first()
        if task:
            if task.state == "cancelled":
                return True
            task.state = "solving"
            db.commit()
    return False


async def solve_node(state: AgentState) -> AgentState:
    """
    Node A: Solver (识图解题)
    """
    print(f"[Node] Solver: Processing task {state['task_id']}")

    import asyncio

    is_cancelled = await asyncio.to_thread(solve_node_sync, state["task_id"])
    if is_cancelled:
        print(
            f"  [Solver] Task {state['task_id']} was cancelled by external intervention."
        )
        return {
            **state,
            "status": "cancelled",
            "error_msg": "Task was manually cancelled.",
        }

    # 获取输入参数
    image_urls = state.get("image_urls") or []
    if not image_urls and state.get("image_url"):
        image_urls = [state.get("image_url")]
    review_feedback = state.get("review_feedback")
    agent_configs = state.get("agent_configs") or {}
    solver_config = agent_configs.get("solver") or {}
    workflow_template_id = state.get("workflow_template_id")
    question_text = state.get("question_text")  # MinerU 解析的题目文字

    # 防御性编程：支持“仅题干文本”解题。
    # 只有图片与题干文本都缺失时才判定失败。
    if not image_urls and not (
        isinstance(question_text, str) and question_text.strip()
    ):
        return {
            **state,
            "status": "failed",
            "failed_node": "solver",
            "error_msg": "Missing both image_urls and question_text in state.",
        }

    try:
        # 调用大模型解题
        print(f"  -> Calling LLM (Solver)...")
        result = await solve_image(
            image_urls,
            review_feedback,
            solver_config,
            workflow_template_id,
            state["task_id"],
            question_text,
        )
        safe_total_tokens = coerce_token_count(
            state.get("total_tokens"), 0
        ) + coerce_token_count(result.get("tokens"), 0)

        return {
            **state,
            "status": "reviewing",
            "draft_solution": result["draft"],
            "total_tokens": safe_total_tokens,
        }
    except asyncio.CancelledError:
        print(f"  [Solver] LLM call cancelled for task {state['task_id']}.")
        return {
            **state,
            "status": "cancelled",
            "error_msg": "Task was manually cancelled.",
        }
    except Exception as e:
        # 捕获网络/API错误，按照 PRD 应进入 error 状态或直接重试，此处为简化演示返回 failed
        print(f"  [Solver] API Error: {e}")
        return {
            **state,
            "status": "failed",
            "failed_node": "solver",
            "error_msg": f"LLM API Error: {str(e)}",
        }
