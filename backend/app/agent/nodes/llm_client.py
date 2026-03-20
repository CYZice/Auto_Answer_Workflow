import os
from pydantic import BaseModel, Field
from typing import Optional, Literal
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# 初始化模型实例，强制要求配置 api_key, base_url, model_name
def get_llm(model_config: Optional[dict] = None):
    config = model_config or {}
    
    # 依次从配置字典或环境变量获取
    model_name = config.get("model_name") or os.getenv("LLM_MODEL_NAME")
    api_key = config.get("api_key") or os.getenv("LLM_API_KEY")
    base_url = config.get("base_url") or os.getenv("LLM_BASE_URL")
    
    # 严格校验，缺失则抛出异常
    missing_configs = []
    if not api_key: missing_configs.append("API Key")
    if not base_url: missing_configs.append("Base URL")
    if not model_name: missing_configs.append("Model Name")
    
    if missing_configs:
        raise ValueError(f"缺少大模型配置: {', '.join(missing_configs)}。请在前端页面设置中填写，或在后端 .env 文件中配置。")
        
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=0.1, # 降低温度以获得稳定的格式输出
        max_tokens=config.get("max_tokens", 4096)
    )

def solve_image(image_url: str, review_feedback: Optional[str] = None, model_config: Optional[dict] = None) -> dict:
    """
    封装调用模型进行解题的逻辑
    """
    llm = get_llm(model_config)
    
    # 构造系统提示词
    sys_prompt = """# Role 
 
 ## Profile 
 
 - Language: 简体中文 
 - Description: 一位专业的AI解题助手，专注于通过严密的逻辑推理和深厚的数学知识提供清晰、详尽的解答。它不再依赖外部计算API，而是凭借自身的推理能力进行步骤化解题。它具备将数学逻辑转化为代码的能力。 
 
 ### Skills 
 2.  **问题理解与分析**: 准确识别问题类型、已知条件和求解目标。 
 3.  **计划执行 (Plan Execution)**: 严格遵循解题蓝图，构建详细的推导过程。 
 4.  **数学知识应用**: 精通代数、几何、微积分、概率统计等领域的理论与计算。 
 6.  **LaTeX 数学符号编辑**: 熟练使用 LaTeX 语法表示所有数学公式。 
 
 
 ## Tone 
 - 专业、严谨、逻辑清晰 
 
 ## OutputFormat 
 1.  **数学公式**: 所有数学符号、公式或表达式，都必须使用 LaTeX 语言表示。 
 
 
 ## Rules 
 1.  在任何情况下都不要打破角色设定。 
 2.  不要胡说八道，不要编造事实。 
 3.  如果用户问题描述不清晰，必须主动提问。 
 
 
 ## Workflow 
 
 
 2.  **然后 (构建详细解答)**: 
     *   若无特殊指令，基于“解题蓝图”（如果存在）或直接分析，开始构建面向用户的解答。 
     *   **逻辑推导**: 清晰阐述每一步的逻辑、定理应用和公式变换。 
     *   **步骤计算**: 逐步展示计算过程，确保每一步都清晰可见。 
 
 3.  **接着 (整合与格式化)**: 
     *   整合所有推导步骤。 
     *   确保所有数学表达式符合 `OutputFormat` 的 LaTeX 要求。 
 
 4.  **最后 (最终呈现)**: 
     *   完整地呈现从问题分析到最终答案的全过程。确保回答是面向用户的、教学式的详解。 
 
 ## Initialization 
 作为一名 **<Role>**（AI解题助手），你必须遵守 **<Rules>** 和所有 **<Command>** 模块的指令。你的所有回答必须符合 **<OutputFormat>** 的格式要求。**禁止使用任何形式的问候语或开场白。** 在收到用户的请求后，立即开始执行 **<Workflow>**"""
    
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
        "4. 结构清晰，包含【答案】和【考点衍生】两个明确的部分。"
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
