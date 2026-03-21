import os
import json
import asyncio
import openai
from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Callable, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.core.database import SessionLocal
from app.models.domain import AgentLog
from app.services.runtime_config import (
    get_prompt_bundle,
    render_user_prompt,
    resolve_fallback_models,
)


def coerce_token_count(value: Any, default: int = 0) -> int:
    """Best-effort token normalization; never raises and always returns int."""
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def log_agent_interaction(
    task_id: str,
    node_name: str,
    request_payload: list,
    response_payload: str,
    cost_tokens: Any,
):
    if not task_id:
        return
    try:
        with SessionLocal() as db:
            # 简单序列化 messages
            req_str = json.dumps(
                [{"role": m.type, "content": m.content} for m in request_payload],
                ensure_ascii=False,
            )
            log_entry = AgentLog(
                task_id=task_id,
                node_name=node_name,
                request_payload=req_str,
                response_payload=response_payload,
                cost_tokens=coerce_token_count(cost_tokens, 0),
            )
            db.add(log_entry)
            db.commit()
    except Exception as e:
        print(f"Failed to log agent interaction: {e}")


# 初始化模型实例，强制要求配置 api_key, base_url, model_name
def get_llm(model_config: Optional[dict] = None):
    config = model_config or {}

    # 依次从配置字典或环境变量获取
    model_name = config.get("model_name") or os.getenv("LLM_MODEL_NAME")
    api_key = config.get("api_key") or os.getenv("LLM_API_KEY")
    base_url = config.get("base_url") or os.getenv("LLM_BASE_URL")

    # 严格校验，缺失则抛出异常
    missing_configs = []
    if not api_key:
        missing_configs.append("API Key")
    if not base_url:
        missing_configs.append("Base URL")
    if not model_name:
        missing_configs.append("Model Name")

    if missing_configs:
        raise ValueError(
            f"缺少大模型配置: {', '.join(missing_configs)}。请在前端页面设置中填写，或在后端 .env 文件中配置。"
        )

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        streaming=config.get("streaming", True),
        temperature=config.get("temperature", 0.5),
        max_tokens=config.get("max_tokens", 4096),
        model_kwargs={
            "max_completion_tokens": config.get(
                "max_tokens", 4096
            ),  # 兼容某些服务商强制要求的 max_completion_tokens
            "frequency_penalty": config.get("frequency_penalty", 0.5),
        },
    )


async def call_with_retry_and_fallback(
    create_llm_func: Callable[[dict], Any],
    messages: list,
    model_config: dict,
    fallback_models: Optional[List[str]] = None,
    timeout: float = 300.0,
    max_retries: int = 2,
) -> Any:
    """
    1. 超时切断并重试 (asyncio.wait_for 切断连接)
    2. 特定异常特定重试 (RateLimit 退避, 50x重试, 40x直接报错)
    3. Fallback 模型列表兜底
    """
    primary_model = model_config.get("model_name")

    # 构造将尝试的模型列表
    models_to_try = []
    if primary_model:
        models_to_try.append(primary_model)
    if fallback_models:
        for fm in fallback_models:
            if fm not in models_to_try:
                models_to_try.append(fm)

    if not models_to_try:
        raise ValueError("No model specified for retry mechanism.")

    last_exception = None

    for model_name in models_to_try:
        current_config = dict(model_config) if model_config else {}
        current_config["model_name"] = model_name

        try:
            llm = create_llm_func(current_config)
        except Exception as e:
            print(f"  [Retry Wrapper] Failed to initialize model {model_name}: {e}")
            last_exception = e
            continue

        for attempt in range(max_retries + 1):
            try:
                print(
                    f"  [Retry Wrapper] Calling LLM {model_name} (Attempt {attempt+1}/{max_retries+1})..."
                )
                use_stream_timeout = hasattr(llm, "astream")
                if use_stream_timeout:
                    stream = llm.astream(messages)
                    first_chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=timeout
                    )
                    merged_chunk = first_chunk
                    async for chunk in stream:
                        merged_chunk = merged_chunk + chunk
                    if hasattr(merged_chunk, "to_message"):
                        response = merged_chunk.to_message()
                    else:
                        response = merged_chunk
                else:
                    response = await asyncio.wait_for(
                        llm.ainvoke(messages), timeout=timeout
                    )
                return response

            except asyncio.TimeoutError as e:
                print(
                    f"  [Retry Wrapper] Timeout ({timeout}s) on {model_name}. Connection aborted."
                )
                last_exception = e
                # Timeout, 继续同模型重试

            except openai.RateLimitError as e:
                print(f"  [Retry Wrapper] Rate limit on {model_name}. Backing off.")
                last_exception = e
                await asyncio.sleep(2**attempt)

            except openai.APIConnectionError as e:
                print(f"  [Retry Wrapper] Connection error on {model_name}.")
                last_exception = e
                await asyncio.sleep(1)

            except openai.APIStatusError as e:
                status_code = getattr(e, "status_code", 500)
                if status_code in (401, 403, 404):
                    print(
                        f"  [Retry Wrapper] Fatal auth/config error ({status_code}) on {model_name}. Not retrying this model."
                    )
                    last_exception = e
                    break  # Stop retrying THIS model, switch to fallback
                elif status_code >= 500:
                    print(
                        f"  [Retry Wrapper] Server error ({status_code}) on {model_name}."
                    )
                    last_exception = e
                    await asyncio.sleep(2)
                else:
                    print(
                        f"  [Retry Wrapper] Other API error ({status_code}) on {model_name}."
                    )
                    last_exception = e
                    break  # Stop retrying THIS model

            except Exception as e:
                print(
                    f"  [Retry Wrapper] Unknown error on {model_name}: {type(e).__name__} - {e}"
                )
                last_exception = e
                break  # Stop retrying THIS model on unknown error

        print(
            f"  [Retry Wrapper] Exhausted retries for {model_name}, switching to fallback if available..."
        )

    raise RuntimeError(
        f"All fallback models and retries failed. Last error: {last_exception}"
    )


async def solve_image(
    image_url: str,
    review_feedback: Optional[str] = None,
    model_config: Optional[dict] = None,
    workflow_template_id: Optional[str] = None,
    task_id: str = None,
) -> dict:
    """
    封装调用模型进行解题的逻辑
    """

    prompt_bundle = get_prompt_bundle("solver", workflow_template_id)
    sys_prompt = (
        prompt_bundle.get("system")
        or "你是一位专业解题助手，请给出完整且严谨的推理过程。"
    )

    # 构造用户输入，采用标准的 Vision 格式，防止 Base64 被当成文本切分
    text_prompt = render_user_prompt(
        prompt_bundle.get("user") or "请解析以下图片中的题目。",
        {"review_feedback": review_feedback or ""},
    )
    if review_feedback:
        text_prompt += (
            f"\n\n【注意】之前的解答有以下问题，请在此次解答中修复：\n{review_feedback}"
        )

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(
            content=[
                {"type": "text", "text": text_prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        ),
    ]

    # 调用模型
    fallback_models = resolve_fallback_models("solver", model_config or {})
    response = await call_with_retry_and_fallback(
        create_llm_func=get_llm,
        messages=messages,
        model_config=model_config or {},
        fallback_models=fallback_models,
        timeout=300.0,
        max_retries=2,
    )

    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or {}
    tokens = coerce_token_count(token_usage.get("total_tokens"), 0)
    if task_id:
        # DB操作放进异步线程避免阻塞
        import asyncio

        asyncio.create_task(
            asyncio.to_thread(
                log_agent_interaction,
                task_id,
                "solver",
                messages,
                response.content,
                tokens,
            )
        )

    # 返回草稿和消耗的 token
    return {"draft": response.content, "tokens": tokens}


async def format_solution(
    draft_solution: str,
    image_url: Optional[str] = None,
    model_config: Optional[dict] = None,
    workflow_template_id: Optional[str] = None,
    task_id: str = None,
) -> dict:
    """
    封装调用模型进行最终排版润色的逻辑
    """

    prompt_bundle = get_prompt_bundle("formatter", workflow_template_id)
    sys_prompt = (
        prompt_bundle.get("system") or "你是排版助手，请将草稿整理成清晰的 Markdown。"
    )

    user_prompt = render_user_prompt(
        prompt_bundle.get("user")
        or "请对以下解题草稿进行最终排版：\n\n{draft_solution}",
        {"draft_solution": draft_solution},
    )

    if image_url:
        human_content = [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    else:
        human_content = user_prompt

    messages = [SystemMessage(content=sys_prompt), HumanMessage(content=human_content)]

    # 调用模型
    fallback_models = resolve_fallback_models("formatter", model_config or {})
    response = await call_with_retry_and_fallback(
        create_llm_func=get_llm,
        messages=messages,
        model_config=model_config or {},
        fallback_models=fallback_models,
        timeout=300.0,
        max_retries=2,
    )

    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or {}
    tokens = coerce_token_count(token_usage.get("total_tokens"), 0)
    if task_id:
        import asyncio

        asyncio.create_task(
            asyncio.to_thread(
                log_agent_interaction,
                task_id,
                "formatter",
                messages,
                response.content,
                tokens,
            )
        )

    # 返回最终结果和消耗的 token
    return {"formatted_result": response.content, "tokens": tokens}
