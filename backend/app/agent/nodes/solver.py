from app.agent.state import AgentState
from app.agent.nodes.llm_client import solve_image
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
    is_cancelled = await asyncio.to_thread(solve_node_sync, state['task_id'])
    if is_cancelled:
        print(f"  [Solver] Task {state['task_id']} was cancelled by external intervention.")
        return {
            **state,
            "status": "cancelled",
            "error_msg": "Task was manually cancelled."
        }
    
    # 获取输入参数
    image_url = state.get("image_url")
    review_feedback = state.get("review_feedback")
    agent_configs = state.get("agent_configs") or {}
    solver_config = agent_configs.get("solver") or {}
    
    # 防御性编程：如果没有图片地址，直接失败
    if not image_url:
        return {
            **state,
            "status": "failed",
            "failed_node": "solver",
            "error_msg": "Missing image_url in state."
        }
        
    try:
        # 调用大模型解题
        print(f"  -> Calling LLM (Solver)...")
        result = await solve_image(image_url, review_feedback, solver_config, state['task_id'])
        
        return {
            **state,
            "status": "reviewing",
            "draft_solution": result["draft"],
            "total_tokens": state.get("total_tokens", 0) + result["tokens"]
        }
    except Exception as e:
        # 捕获网络/API错误，按照 PRD 应进入 error 状态或直接重试，此处为简化演示返回 failed
        print(f"  [Solver] API Error: {e}")
        return {
            **state,
            "status": "failed",
            "failed_node": "solver",
            "error_msg": f"LLM API Error: {str(e)}"
        }
