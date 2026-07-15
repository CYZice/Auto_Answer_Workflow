from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from app.agent.state import AgentState
from app.agent.nodes.solver import solve_node
from app.agent.nodes.reviewer import review_node
from app.agent.nodes.formatter import format_node


def route_after_solver(state: AgentState) -> Literal["reviewer", "formatter", "end"]:
    """
    条件路由：Solver 后根据状态决定是否进入 Reviewer。
    - failed/cancelled: 直接终止，保留失败上下文
    - 其他状态（正常应为 reviewing）: 进入 Reviewer
    """
    solver_status = state.get("status")
    if solver_status in ["failed", "cancelled"]:
        print(f"  [Router] Solver ended with {solver_status} -> end")
        return "end"
    if solver_status == "reviewing":
        target_nodes = state.get("target_nodes") or []
        if target_nodes and "reviewer" not in target_nodes:
            if "formatter" in target_nodes:
                return "formatter"
            print("  [Router] Reviewer skipped and formatter not selected -> end")
            return "end"
        return "reviewer"
    print(f"  [Router] Solver produced unexpected status {solver_status} -> end")
    return "end"


def route_after_review(
    state: AgentState,
) -> Literal["formatter", "solver", "failed", "end"]:
    """
    条件路由：根据 Reviewer 的裁决决定下一步。
    对应 PRD：允许 1 次重做（即最多 2 次审查）；第二次审查仍 FAIL -> failed
    """
    # 优先检查是否因为异常或外部熔断已经标记为 failed 或 cancelled
    if state.get("status") in ["failed", "cancelled"]:
        print(f"  [Router] State is {state.get('status')} -> failed")
        return "failed"

    decision = state.get("review_decision")
    current_retry = state.get("retry_count", 0)

    if decision == "PASS":
        target_nodes = state.get("target_nodes") or []
        if target_nodes and "formatter" not in target_nodes:
            print(f"  [Router] PASS but formatter not selected -> end")
            return "end"
        print(f"  [Router] PASS -> formatting")
        return "formatter"

    if decision == "FAIL":
        if current_retry < 1:  # 允许重试 1 次 (0 -> 1)
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
        "status": "solving",
    }


def build_graph(
    entry_point: Literal["solver", "reviewer", "formatter"] = "solver",
    nodes: dict | None = None,
) -> StateGraph:
    """
    构建并返回 LangGraph 核心拓扑图
    """
    workflow = StateGraph(AgentState)

    # 1. 注册节点
    selected_nodes = nodes or {
        "solver": solve_node,
        "reviewer": review_node,
        "formatter": format_node,
    }
    workflow.add_node("solver", selected_nodes["solver"])
    workflow.add_node("reviewer", selected_nodes["reviewer"])
    workflow.add_node("formatter", selected_nodes["formatter"])

    # 一个专门用于增加重试次数的辅助节点
    workflow.add_node("increment_retry", increment_retry)

    # 2. 定义边 (Edges)
    # 起点可按需指定，支持从失败节点恢复
    workflow.set_entry_point(entry_point)

    # solver 根据状态流向 reviewer 或直接结束
    workflow.add_conditional_edges(
        "solver",
        route_after_solver,
        {
            "reviewer": "reviewer",
            "formatter": "formatter",
            "end": END,
        },
    )

    # reviewer 根据条件流向
    workflow.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "formatter": "formatter",
            "solver": "increment_retry",  # 先过递增节点
            "failed": END,
            "end": END,
        },
    )

    # 递增节点必须流向 solver
    workflow.add_edge("increment_retry", "solver")

    # formatter 必然结束
    workflow.add_edge("formatter", END)

    # 编译图
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


def build_errata_graph(
    entry_point: Literal[
        "solver", "reviewer", "formatter", "errata_adjudication", "word_composition"
    ] = "solver",
    nodes: dict | None = None,
) -> StateGraph:
    """构建勘误专用图。

    前三步复用普通题的解题、复核和排版节点；随后再进行勘误裁决与 Word 组合。
    """
    if not nodes:
        raise ValueError("Errata graph requires dedicated node implementations.")

    workflow = StateGraph(AgentState)
    workflow.add_node("solver", nodes["solver"])
    workflow.add_node("reviewer", nodes["reviewer"])
    workflow.add_node("formatter", nodes["formatter"])
    workflow.add_node("errata_adjudication", nodes["errata_adjudication"])
    workflow.add_node("word_composition", nodes["word_composition"])
    workflow.add_node("increment_retry", increment_retry)
    workflow.set_entry_point(entry_point)

    def after_formatter(state: AgentState) -> str:
        if state.get("status") in {"failed", "cancelled"}:
            return "end"
        selected = state.get("target_nodes") or []
        if selected and "errata_adjudication" not in selected:
            return "end"
        return "errata_adjudication"

    def after_adjudication(state: AgentState) -> str:
        if state.get("status") in {"failed", "cancelled", "manual"}:
            return "end"
        selected = state.get("target_nodes") or []
        return "word_composition" if not selected or "word_composition" in selected else "end"

    workflow.add_conditional_edges(
        "solver",
        route_after_solver,
        {
            "reviewer": "reviewer",
            "formatter": "formatter",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "formatter": "formatter",
            "solver": "increment_retry",
            "failed": END,
            "end": END,
        },
    )
    workflow.add_edge("increment_retry", "solver")
    workflow.add_conditional_edges(
        "formatter",
        after_formatter,
        {"errata_adjudication": "errata_adjudication", "end": END},
    )
    workflow.add_conditional_edges(
        "errata_adjudication",
        after_adjudication,
        {"word_composition": "word_composition", "end": END},
    )
    workflow.add_edge("word_composition", END)
    return workflow.compile(checkpointer=MemorySaver())
