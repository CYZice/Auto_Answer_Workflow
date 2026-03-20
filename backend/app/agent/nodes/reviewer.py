from app.agent.state import AgentState
from app.models.schemas import ReviewDecision
from app.agent.nodes.llm_client import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
import json
from app.core.database import SessionLocal
from app.models.domain import Task

def review_node_sync(task_id: str):
    # 检查是否被外部干预熔断，并更新当前状态
    with SessionLocal() as db:
        task = db.query(Task).filter(Task.task_id == task_id).first()
        if task:
            if task.state == "cancelled":
                return True
            task.state = "reviewing"
            db.commit()
    return False

async def review_node(state: AgentState) -> AgentState:
    """
    Node B: Reviewer (自动审查)
    """
    print(f"[Node] Reviewer: Reviewing task {state['task_id']}")
    
    import asyncio
    is_cancelled = await asyncio.to_thread(review_node_sync, state['task_id'])
    if is_cancelled:
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
        decision_obj: ReviewDecision = await structured_llm.ainvoke(messages)
        
        decision = "PASS" if decision_obj.is_pass else "FAIL"
        feedback = decision_obj.feedback if not decision_obj.is_pass else None
            
        print(f"  [Reviewer Result] Decision: {decision}, Feedback: {feedback}")
        
        from app.agent.nodes.llm_client import log_agent_interaction
        import json
        import asyncio
        asyncio.create_task(asyncio.to_thread(
            log_agent_interaction,
            state['task_id'], 
            "reviewer", 
            messages, 
            json.dumps({"is_pass": decision_obj.is_pass, "feedback": decision_obj.feedback}, ensure_ascii=False),
            200
        ))

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
