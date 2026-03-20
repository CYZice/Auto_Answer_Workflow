from app.agent.state import AgentState
from app.models.schemas import ReviewDecision
from app.agent.nodes.llm_client import get_llm
from langchain_core.messages import SystemMessage, HumanMessage
import json
import re
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
            "failed_node": "reviewer",
            "error_msg": "Missing draft_solution in state."
        }

    llm = None
    messages = []
    raw_response_text = None
    review_tokens = 200
    def normalize_text(value):
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
        else:
            if isinstance(value, list):
                parts = []
                for item in value:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        if item.get("type") == "text" and isinstance(item.get("text"), str):
                            parts.append(item["text"])
                        else:
                            try:
                                parts.append(json.dumps(item, ensure_ascii=False))
                            except Exception:
                                parts.append(str(item))
                    else:
                        parts.append(str(item))
                text = "".join(parts).strip()
            else:
                text = json.dumps(value, ensure_ascii=False).strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        return text.strip()

    def extract_json_object(text: str):
        if not text:
            return None
        start = text.find("{")
        while start != -1:
            in_string = False
            escaped = False
            depth = 0
            for idx in range(start, len(text)):
                ch = text[idx]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == "\"":
                        in_string = False
                    continue
                if ch == "\"":
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:idx + 1]
            start = text.find("{", start + 1)
        return None

    def recover_decision_from_text(value):
        text = normalize_text(value)
        if not text:
            return None
        candidates = [text]
        extracted = extract_json_object(text)
        if extracted and extracted != text:
            candidates.insert(0, extracted)
        for candidate in candidates:
            try:
                parsed_json = json.loads(candidate)
                if isinstance(parsed_json, dict):
                    return ReviewDecision.model_validate(parsed_json)
            except Exception:
                continue
        match = re.search(r'"?is_pass"?\s*[:=]\s*(true|false|"true"|"false"|1|0)', text, flags=re.IGNORECASE)
        if not match and "is_pass" not in text:
            return None
        if match:
            token = match.group(1).strip('"').lower()
            is_pass = token in {"true", "1"}
        else:
            lowered = text.lower()
            if "is_pass" in lowered and "false" in lowered:
                is_pass = False
            else:
                is_pass = True
        feedback_match = re.search(r'"?feedback"?\s*[:=]\s*"((?:[^"\\]|\\.)*)"', text, flags=re.IGNORECASE)
        if feedback_match:
            feedback = bytes(feedback_match.group(1), "utf-8").decode("unicode_escape")
        else:
            feedback = ""
        return ReviewDecision(is_pass=is_pass, feedback=feedback)
    try:
        print(f"  -> Calling LLM (Reviewer with Structured Output)...")
        agent_configs = state.get("agent_configs") or {}
        reviewer_config = agent_configs.get("reviewer") or {}
        reviewer_runtime_config = dict(reviewer_config)
        reviewer_runtime_config["streaming"] = False
        
        from app.agent.nodes.llm_client import get_llm, call_with_retry_and_fallback
        
        def create_structured_llm(config):
            llm = get_llm(config)
            try:
                return llm.with_structured_output(ReviewDecision, include_raw=True)
            except TypeError:
                return llm.with_structured_output(ReviewDecision)
        
        sys_prompt = """# Role
你是一位极度精准的【电路分析专家】，专门负责电路题目的逻辑审查。你拥有深厚的电类专业功底，做事严谨，杜绝废话。

# Workflow
1. 静默解题：接收题目后，先在后台独立推导出结果，不要受用户提供的答案干扰。
2. 答案判定：
   - 若用户答案正确：判定通过。
   - 若用户答案错误：判定不通过，并给出反馈。

# Constraints
- 严禁输出任何客套话（如“你好”、“解析如下”）。
- 严禁输出“考点延伸”内容。
- 仅关注逻辑的正确性与结果的精准度。

# Structured Output Contract
- 你必须严格输出为结构化字段：
  - is_pass: 若判定通过则为 true，否则为 false。
  - feedback:
    - 若 is_pass=true，填写空字符串。
    - 若 is_pass=false，必须以 `【结果错误】` 开头，并简要指出错误原因及正确计算过程。"""
        text_prompt = f"题目：见图片\n答案：\n{draft}"
        
        messages = [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=[
                {"type": "text", "text": text_prompt},
                {"type": "image_url", "image_url": {"url": state.get('image_url')}}
            ])
        ]
        
        fallback_models = reviewer_runtime_config.get("fallback_models", ["gpt-4o", "claude-3-5-sonnet", "gpt-4o-mini"])
        structured_result = await call_with_retry_and_fallback(
            create_llm_func=create_structured_llm,
            messages=messages,
            model_config=reviewer_runtime_config,
            fallback_models=fallback_models,
            timeout=300.0,
            max_retries=2
        )
        
        decision_obj = None
        if isinstance(structured_result, dict):
            parsed_result = structured_result.get("parsed")
            raw_message = structured_result.get("raw")
            parsing_error = structured_result.get("parsing_error")
            if raw_message is not None:
                raw_content = raw_message.content
                if isinstance(raw_content, str):
                    raw_response_text = raw_content
                else:
                    raw_response_text = json.dumps(raw_content, ensure_ascii=False)
                raw_metadata = getattr(raw_message, "response_metadata", None)
                if not isinstance(raw_metadata, dict):
                    raw_metadata = {}
                token_usage = raw_metadata.get("token_usage")
                if not isinstance(token_usage, dict):
                    token_usage = {}
                review_tokens = token_usage.get("total_tokens", review_tokens)
            if parsed_result is None:
                recovered = recover_decision_from_text(raw_response_text)
                if recovered is not None:
                    decision_obj = recovered
                elif parsing_error:
                    raise parsing_error
                else:
                    raise ValueError("Reviewer structured output parsing failed.")
            if isinstance(parsed_result, ReviewDecision):
                if decision_obj is None:
                    decision_obj = parsed_result
            elif decision_obj is None:
                decision_obj = ReviewDecision.model_validate(parsed_result)
        elif isinstance(structured_result, ReviewDecision):
            decision_obj = structured_result
        else:
            try:
                decision_obj = ReviewDecision.model_validate(structured_result)
            except Exception:
                recovered = recover_decision_from_text(structured_result)
                if recovered is None:
                    raise
                decision_obj = recovered
        
        decision = "PASS" if decision_obj.is_pass else "FAIL"
        feedback = decision_obj.feedback if not decision_obj.is_pass else None
            
        print(f"  [Reviewer Result] Decision: {decision}, Feedback: {feedback}")
        
        from app.agent.nodes.llm_client import log_agent_interaction
        import asyncio
        if raw_response_text is None:
            raw_response_text = json.dumps({"is_pass": decision_obj.is_pass, "feedback": decision_obj.feedback}, ensure_ascii=False)
        asyncio.create_task(asyncio.to_thread(
            log_agent_interaction,
            state['task_id'], 
            "reviewer", 
            messages, 
            json.dumps({
                "is_pass": decision_obj.is_pass,
                "feedback": decision_obj.feedback,
                "raw_response": raw_response_text
            }, ensure_ascii=False),
            review_tokens
        ))

        return {
            **state,
            "review_decision": decision,
            "review_feedback": feedback,
            "total_tokens": state.get("total_tokens", 0) + review_tokens
        }
        
    except Exception as e:
        from app.agent.nodes.llm_client import log_agent_interaction
        import asyncio
        if raw_response_text is None and llm is not None:
            try:
                fallback_response = await llm.ainvoke(messages)
                fallback_content = fallback_response.content
                if isinstance(fallback_content, str):
                    raw_response_text = fallback_content
                else:
                    raw_response_text = json.dumps(fallback_content, ensure_ascii=False)
                fallback_metadata = getattr(fallback_response, "response_metadata", None)
                if not isinstance(fallback_metadata, dict):
                    fallback_metadata = {}
                fallback_token_usage = fallback_metadata.get("token_usage")
                if not isinstance(fallback_token_usage, dict):
                    fallback_token_usage = {}
                review_tokens = fallback_token_usage.get("total_tokens", review_tokens)
            except Exception as fallback_error:
                raw_response_text = f"__RAW_RESPONSE_UNAVAILABLE__: {fallback_error}"
        if raw_response_text is None:
            raw_response_text = "__RAW_RESPONSE_UNAVAILABLE__"
        asyncio.create_task(asyncio.to_thread(
            log_agent_interaction,
            state['task_id'],
            "reviewer",
            messages,
            json.dumps({
                "error": str(e),
                "raw_response": raw_response_text
            }, ensure_ascii=False),
            review_tokens
        ))
        print(f"  [Reviewer] API Error: {e}")
        return {
            **state,
            "status": "failed",
            "failed_node": "reviewer",
            "error_msg": f"LLM API Error in Reviewer: {str(e)}"
        }
