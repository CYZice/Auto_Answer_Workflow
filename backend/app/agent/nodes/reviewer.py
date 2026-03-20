from app.agent.state import AgentState
from app.models.schemas import ReviewDecision
from app.agent.nodes.llm_client import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
import json
from app.core.database import SessionLocal
from app.models.domain import Task

def review_node(state: AgentState) -> AgentState:
    """
    Node B: Reviewer (自动审查)
    """
    print(f"[Node] Reviewer: Reviewing task {state['task_id']}")
    
    # 检查是否被外部干预熔断
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == state['task_id']).first()
        if task and task.state == "cancelled":
            print(f"  [Reviewer] Task {state['task_id']} was cancelled by external intervention.")
            return {
                **state,
                "status": "cancelled",
                "error_msg": "Task was manually cancelled."
            }

    # 防御性编程
    draft = state.get("draft_solution")
    if not draft:
        return {
            **state,
            "status": "failed",
            "error_msg": "Missing draft_solution in state."
        }

    try:
        print(f"  -> Calling LLM (Reviewer with Structured Output)...")
        reviewer_config = state.get("agent_configs", {}).get("reviewer", {})
        llm = get_llm(reviewer_config)
        
        # 使用 LangChain 的 with_structured_output 绑定 Pydantic 模型
        # 这将强制模型输出完全符合 ReviewDecision 的 JSON 结构
        structured_llm = llm.with_structured_output(ReviewDecision)
        
        sys_prompt = "你是一个严格的数学解题审查员。请检查以下解题步骤是否严密、答案是否正确。如果发现错误，请指出具体问题（如：计算错误、步骤跳跃等）并给出反馈。"
        text_prompt = f"待审查的解题草稿：\n{draft}"
        
        messages = [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=[
                {"type": "text", "text": text_prompt},
                {"type": "image_url", "image_url": {"url": state.get('image_url')}}
            ])
        ]
        
        # 调用模型获取强类型结构化结果
        # 注意：with_structured_output 目前可能不返回 token_usage (因为内部封装了工具调用)，
        # 生产环境中可以通过 Callback 或拦截器获取，此处暂且 mock 一个预估值
        decision_obj: ReviewDecision = structured_llm.invoke(messages)
        
        decision = decision_obj.decision
        feedback = decision_obj.reason if decision == "FAIL" else None
        
        # 如果有具体 issues，追加到 feedback 中
        if decision == "FAIL" and decision_obj.issues:
            issues_str = "\n".join([f"- [{i.type}] {i.detail}" for i in decision_obj.issues])
            feedback = f"{feedback}\n具体问题：\n{issues_str}"
            
        print(f"  [Reviewer Result] Decision: {decision}, Reason: {feedback}")

        return {
            **state,
            "review_decision": decision,
            "review_feedback": feedback,
            "total_tokens": state.get("total_tokens", 0) + 200 # 假设一次审查消耗200token
        }
        
    except Exception as e:
        print(f"  [Reviewer] API Error: {e}")
        return {
            **state,
            "status": "failed",
            "error_msg": f"LLM API Error in Reviewer: {str(e)}"
        }
