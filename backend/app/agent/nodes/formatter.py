from app.agent.state import AgentState
from app.agent.nodes.llm_client import format_solution
from app.core.database import SessionLocal
from app.models.domain import Task

def format_node(state: AgentState) -> AgentState:
    """
    Node C: Formatter (自动排版)
    """
    print(f"[Node] Formatter: Formatting task {state['task_id']}")
    
    # 检查是否被外部干预熔断，并更新当前状态
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == state['task_id']).first()
        if task:
            if task.state == "cancelled":
                print(f"  [Formatter] Task {state['task_id']} was cancelled by external intervention.")
                return {
                    **state,
                    "status": "cancelled",
                    "error_msg": "Task was manually cancelled."
                }
            task.state = "formatting"
            db.commit()

    # 防御性编程
    draft_solution = state.get("draft_solution")
    if not draft_solution:
        return {
            **state,
            "status": "failed",
            "error_msg": "Cannot format empty draft."
        }
        
    formatter_config = state.get("agent_configs", {}).get("formatter", {})
    
    try:
        # 调用大模型进行排版润色
        print(f"  -> Calling LLM (Formatter)...")
        result = format_solution(draft_solution, formatter_config)
        
        return {
            **state,
            "status": "completed",
            "final_result": result["formatted_result"],
            "total_tokens": state.get("total_tokens", 0) + result["tokens"]
        }
    except Exception as e:
        print(f"  [Formatter] API Error: {e}")
        return {
            **state,
            "status": "failed",
            "error_msg": f"LLM API Error in Formatter: {str(e)}"
        }
