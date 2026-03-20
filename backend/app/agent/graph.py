from typing import Literal
from langgraph.graph import StateGraph, END
from app.agent.state import AgentState
from app.agent.nodes.solver import solve_node
from app.agent.nodes.reviewer import review_node
from app.agent.nodes.formatter import format_node

def route_after_review(state: AgentState) -> Literal["formatter", "solver", "failed"]:
    """
    条件路由：根据 Reviewer 的裁决决定下一步。
    对应 PRD：允许 1 次重做（即最多 2 次审查）；第二次审查仍 FAIL -> failed
    """
    decision = state.get("review_decision")
    current_retry = state.get("retry_count", 0)
    
    if decision == "PASS":
        print(f"  [Router] PASS -> formatting")
        return "formatter"
    
    if decision == "FAIL":
        if current_retry < 1: # 允许重试 1 次 (0 -> 1)
            print(f"  [Router] FAIL (retry {current_retry}) -> solver")
            return "solver"
        else:
            print(f"  [Router] FAIL (max retries reached) -> failed")
            return "failed"
            
    # 防御性 fallback
    print(f"  [Router] Unknown decision: {decision} -> failed")
    return "failed"

def increment_retry(state: AgentState) -> AgentState:
    """
    在流转回 Solver 前递增重试计数器
    """
    return {
        **state,
        "retry_count": state.get("retry_count", 0) + 1,
        "status": "solving"
    }

def build_graph() -> StateGraph:
    """
    构建并返回 LangGraph 核心拓扑图
    """
    workflow = StateGraph(AgentState)
    
    # 1. 注册节点
    workflow.add_node("solver", solve_node)
    workflow.add_node("reviewer", review_node)
    workflow.add_node("formatter", format_node)
    
    # 一个专门用于增加重试次数的辅助节点
    workflow.add_node("increment_retry", increment_retry)
    
    # 2. 定义边 (Edges)
    # 起点始终是 solver
    workflow.set_entry_point("solver")
    
    # solver 必然流向 reviewer
    workflow.add_edge("solver", "reviewer")
    
    # reviewer 根据条件流向
    workflow.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "formatter": "formatter",
            "solver": "increment_retry",  # 先过递增节点
            "failed": END
        }
    )
    
    # 递增节点必须流向 solver
    workflow.add_edge("increment_retry", "solver")
    
    # formatter 必然结束
    workflow.add_edge("formatter", END)
    
    # 编译图
    return workflow.compile()
