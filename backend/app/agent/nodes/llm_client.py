import os
from pydantic import BaseModel, Field
from typing import Optional, Literal
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# 初始化模型实例 (按你的要求配置了 base_url 和 api_key)
# 这里使用 gpt-4o-mini 进行测试，生产环境可换为 gpt-4o 或 claude
def get_llm(model_config: Optional[dict] = None):
    config = model_config or {}
    return ChatOpenAI(
        model=config.get("model_name", "gpt-4o-mini"),
        api_key=config.get("api_key") or os.getenv("OPENAI_API_KEY", "sk-y9ELdtTFNbNWry1jLlU3v34D4R4DpAMWiMNtIVDNizdlyM2h"),
        base_url=config.get("base_url") or "https://yunwu.ai/v1",
        temperature=0.1, # 降低温度以获得稳定的格式输出
        max_tokens=config.get("max_tokens", 4096)
    )

def solve_image(image_url: str, review_feedback: Optional[str] = None, model_config: Optional[dict] = None) -> dict:
    """
    封装调用模型进行解题的逻辑
    """
    llm = get_llm(model_config)
    
    # 构造系统提示词
    sys_prompt = "你是一个专业的数学解题专家。请详细给出解题步骤和最终答案。请使用 Markdown 和 LaTeX 格式输出数学公式。"
    
    # 构造用户输入，采用标准的 Vision 格式，防止 Base64 被当成文本切分
    text_prompt = "请解析以下图片中的题目。"
    if review_feedback:
        text_prompt += f"\n\n【注意】之前的解答有以下问题，请在此次解答中修复：\n{review_feedback}"
        
    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=[
            {"type": "text", "text": text_prompt},
            {"type": "image_url", "image_url": {"url": image_url}}
        ])
    ]
    
    # 调用模型
    response = llm.invoke(messages)
    
    # 返回草稿和消耗的 token
    return {
        "draft": response.content,
        "tokens": response.response_metadata.get("token_usage", {}).get("total_tokens", 0)
    }

def format_solution(draft_solution: str, model_config: Optional[dict] = None) -> dict:
    """
    封装调用模型进行最终排版润色的逻辑
    """
    llm = get_llm(model_config)
    
    # 构造专门用于排版的系统提示词
    sys_prompt = (
        "你是一个专业的数学教研排版专家。请对用户提供的解题草稿进行精美的排版和润色。\n"
        "要求：\n"
        "1. 使用标准的 Markdown 语法。\n"
        "2. 所有数学公式必须使用 LaTeX 语法（行内公式使用 $...$，独立公式使用 $$...$$）。\n"
        "3. 纠正草稿中可能存在的错别字或语病，但不要改变数学逻辑和最终答案。\n"
        "4. 结构清晰，包含【题目解析】和【最终答案】两个明确的部分。"
    )
    
    user_prompt = f"请对以下解题草稿进行最终排版：\n\n{draft_solution}"
        
    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]
    
    # 调用模型
    response = llm.invoke(messages)
    
    # 返回最终结果和消耗的 token
    return {
        "formatted_result": response.content,
        "tokens": response.response_metadata.get("token_usage", {}).get("total_tokens", 0)
    }
