import os
from urllib.parse import urlparse
import json
import asyncio
import openai
from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Callable, Any
from langchain_openai import ChatOpenAI


YUANXUAI_RESPONSES_HEADERS = {
    "User-Agent": "Yo/JS 4.91.1",
    "x-stainless-lang": "js",
    "x-stainless-package-version": "4.91.1",
    "x-stainless-os": "Windows",
    "x-stainless-arch": "x64",
    "x-stainless-runtime": "node",
    "x-stainless-runtime-version": "v22.22.0",
    "x-stainless-retry-count": "0",
    "x-stainless-timeout": "60000",
}
from langchain_core.messages import HumanMessage, SystemMessage
from app.core.database import SessionLocal
from app.core.events import task_events
from app.models.domain import AgentLog
from app.models.domain import Task
from app.services.runtime_config import (
    get_prompt_bundle,
    read_runtime_settings,
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


def extract_response_text(content: Any) -> str:
    """兼容 Chat Completions 字符串和 Responses API 的结构化内容。"""
    if isinstance(content, str):
        return content.strip()

    fragments: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("type") in {"output_text", "text"}:
                text = value.get("text")
                if isinstance(text, str) and text.strip():
                    fragments.append(text.strip())
            else:
                visit(value.get("content"))

    visit(content)
    return "\n".join(fragments)


def log_agent_interaction(
    task_id: str,
    node_name: str,
    request_payload: list,
    response_payload: Any,
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
            response_str = (
                response_payload
                if isinstance(response_payload, str)
                else json.dumps(response_payload, ensure_ascii=False, default=str)
            )
            log_entry = AgentLog(
                task_id=task_id,
                node_name=node_name,
                request_payload=req_str,
                response_payload=response_str,
                cost_tokens=coerce_token_count(cost_tokens, 0),
            )
            db.add(log_entry)
            db.commit()
    except Exception as e:
        print(f"Failed to log agent interaction: {e}")


def is_task_cancelled_sync(task_id: str) -> bool:
    if not task_id:
        return False
    try:
        with SessionLocal() as db:
            task = db.query(Task).filter(Task.task_id == task_id).first()
            return bool(task and task.state == "cancelled")
    except Exception:
        return False


async def run_with_task_cancellation(
    task_id: Optional[str],
    awaitable,
    poll_interval: float = 0.3,
):
    """
    轮询 DB 中的任务状态；一旦外部标记 cancelled，立即取消当前 awaitable。
    """
    running_task = asyncio.create_task(awaitable)
    try:
        if not task_id:
            return await running_task

        while not running_task.done():
            if await asyncio.to_thread(is_task_cancelled_sync, task_id):
                running_task.cancel()
                try:
                    await running_task
                except asyncio.CancelledError:
                    pass
                raise asyncio.CancelledError("Task was manually cancelled.")
            await asyncio.sleep(poll_interval)

        return await running_task
    except asyncio.CancelledError:
        running_task.cancel()
        raise


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

    use_responses_api = bool(config.get("use_responses_api", True))
    llm_kwargs = {
        "model": model_name,
        "api_key": api_key,
        "base_url": base_url,
        "streaming": config.get("streaming", True),
        "temperature": config.get("temperature", 0.5),
        "max_tokens": config.get("max_tokens", 4096),
        "use_responses_api": use_responses_api,
    }
    if use_responses_api:
        llm_kwargs["store"] = bool(config.get("store", False))
        if urlparse(base_url).hostname == "token.yuanxuai.xyz":
            llm_kwargs["default_headers"] = YUANXUAI_RESPONSES_HEADERS
    else:
        llm_kwargs["model_kwargs"] = {
            "max_completion_tokens": config.get(
                "max_tokens", 4096
            ),  # 兼容某些服务商强制要求的 max_completion_tokens
            "frequency_penalty": config.get("frequency_penalty", 0.5),
        }
    reasoning_effort = config.get("reasoning_effort")
    if use_responses_api and reasoning_effort:
        llm_kwargs["reasoning"] = {"effort": reasoning_effort}

    return ChatOpenAI(
        **llm_kwargs,
    )


async def call_with_retry_and_fallback(
    create_llm_func: Callable[[dict], Any],
    messages: list,
    model_config: dict,
    fallback_models: Optional[List[str]] = None,
    timeout: float = 300.0,
    max_retries: int = 2,
    task_id: Optional[str] = None,
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

    async def invoke_llm_with_timeout(llm_obj):
        use_stream_timeout = hasattr(llm_obj, "astream")
        if use_stream_timeout:
            stream = llm_obj.astream(messages)
            first_chunk = await asyncio.wait_for(stream.__anext__(), timeout=timeout)
            merged_chunk = first_chunk
            async for chunk in stream:
                merged_chunk = merged_chunk + chunk
            if hasattr(merged_chunk, "to_message"):
                return merged_chunk.to_message()
            return merged_chunk
        return await llm_obj.ainvoke(messages)

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
                if task_id:
                    task_events.publish(
                        task_id,
                        json.dumps({
                            "event": "model_request_start",
                            "model_name": model_name,
                            "timeout": timeout,
                            "attempt": attempt + 1,
                            "max_retries": max_retries
                        }, ensure_ascii=False)
                    )

                response = await run_with_task_cancellation(
                    task_id,
                    invoke_llm_with_timeout(llm),
                )
                return response

            except asyncio.CancelledError:
                print(
                    f"  [Retry Wrapper] Task {task_id} cancelled during LLM call on {model_name}."
                )
                raise

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


def get_runtime_request_settings() -> tuple[float, int]:
    try:
        settings = read_runtime_settings()
    except Exception:
        settings = {}
    timeout_value = coerce_token_count(settings.get("request_timeout_seconds"), 300)
    if timeout_value < 1:
        timeout_value = 300
    max_retries = coerce_token_count(settings.get("max_retries"), 2)
    if max_retries < 0:
        max_retries = 2
    return float(timeout_value), max_retries


async def solve_image(
    image_urls: List[str],
    review_feedback: Optional[str] = None,
    model_config: Optional[dict] = None,
    workflow_template_id: Optional[str] = None,
    task_id: str = None,
    question_text: Optional[str] = None,
) -> dict:
    """
    封装调用模型进行解题的逻辑

    Args:
        image_urls: 题目图片列表
        review_feedback: 审查反馈（重试时使用）
        model_config: 模型配置
        workflow_template_id: 工作流模板 ID
        task_id: 任务 ID
        question_text: MinerU 解析的题目文字（可选）
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

    # 如果有题目文字，附加到提示词中
    print(f"[DEBUG solve_image] question_text={question_text}")
    if question_text:
        text_prompt = f"题目内容（来自 OCR 识别）：\n{question_text}\n\n{text_prompt}"

    human_content = [{"type": "text", "text": text_prompt}]
    for image_url in image_urls:
        human_content.append({"type": "image_url", "image_url": {"url": image_url}})

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=human_content),
    ]

    # 调用模型
    fallback_models = resolve_fallback_models("solver", model_config or {})
    timeout_seconds, max_retries = get_runtime_request_settings()
    response = await call_with_retry_and_fallback(
        create_llm_func=get_llm,
        messages=messages,
        model_config=model_config or {},
        fallback_models=fallback_models,
        timeout=timeout_seconds,
        max_retries=max_retries,
        task_id=task_id,
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
    return {"draft": extract_response_text(response.content), "tokens": tokens}


async def format_solution(
    draft_solution: str,
    image_urls: Optional[List[str]] = None,
    model_config: Optional[dict] = None,
    workflow_template_id: Optional[str] = None,
    task_id: str = None,
    question_text: Optional[str] = None,
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

    # 如果有题目文字，附加到提示词中
    if question_text:
        user_prompt = f"题目内容（来自 OCR 识别）：\n{question_text}\n\n{user_prompt}"

    if image_urls:
        human_content = [{"type": "text", "text": user_prompt}]
        for image_url in image_urls:
            human_content.append({"type": "image_url", "image_url": {"url": image_url}})
    else:
        human_content = user_prompt

    messages = [SystemMessage(content=sys_prompt), HumanMessage(content=human_content)]

    # 调用模型
    fallback_models = resolve_fallback_models("formatter", model_config or {})
    timeout_seconds, max_retries = get_runtime_request_settings()
    response = await call_with_retry_and_fallback(
        create_llm_func=get_llm,
        messages=messages,
        model_config=model_config or {},
        fallback_models=fallback_models,
        timeout=timeout_seconds,
        max_retries=max_retries,
        task_id=task_id,
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
    return {"formatted_result": extract_response_text(response.content), "tokens": tokens}
