import os
from pydantic import BaseModel, Field
from typing import Optional, Literal
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# 初始化模型实例 (按你的要求配置了 base_url 和 api_key)
# 这里使用 gpt-4o-mini 进行测试，生产环境可换为 gpt-4o 或 claude
def get_llm():
    return ChatOpenAI(
        model="gpt-4o-mini",
        api_key="sk-y9ELdtTFNbNWry1jLlU3v34D4R4DpAMWiMNtIVDNizdlyM2h",
        base_url="https://yunwu.ai/v1",
        temperature=0.1 # 降低温度以获得稳定的格式输出
    )

def solve_image(image_url: str, review_feedback: Optional[str] = None) -> dict:
    """
    封装调用模型进行解题的逻辑
    """
    llm = get_llm()
    
    # 构造系统提示词
    sys_prompt = "你是一个专业的数学解题专家。请详细给出解题步骤和最终答案。请使用 Markdown 和 LaTeX 格式输出数学公式。"
    
    # 构造用户输入
    user_prompt = f"请解析以下图片中的题目。\n图片地址: {image_url}"
    if review_feedback:
        user_prompt += f"\n\n【注意】之前的解答有以下问题，请在此次解答中修复：\n{review_feedback}"
        
    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]
    
    # 调用模型
    response = llm.invoke(messages)
    
    # 返回草稿和消耗的 token
    return {
        "draft": response.content,
        "tokens": response.response_metadata.get("token_usage", {}).get("total_tokens", 0)
    }
