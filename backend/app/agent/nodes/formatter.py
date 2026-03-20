from app.agent.state import AgentState

def format_node(state: AgentState) -> AgentState:
    """
    Node C: Formatter (自动排版)
    """
    print(f"[Node] Formatter: Formatting task {state['task_id']}")
    
    # 防御性编程
    if not state.get("draft_solution"):
        return {
            **state,
            "status": "failed",
            "error_msg": "Cannot format empty draft."
        }
        
    final_md = f"## Final Result\n\n{state['draft_solution']}\n\n**Processed by Auto-Pipeline**"
    
    return {
        **state,
        "status": "completed",
        "final_result": final_md,
        "total_tokens": state.get("total_tokens", 0) + 30
    }
