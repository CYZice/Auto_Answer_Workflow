import os
import json
from pydantic import BaseModel, Field
from typing import Optional, Literal
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from app.core.database import SessionLocal
from app.models.domain import AgentLog

def log_agent_interaction(task_id: str, node_name: str, request_payload: list, response_payload: str, cost_tokens: int):
    if not task_id:
        return
    try:
        with SessionLocal() as db:
            # 简单序列化 messages
            req_str = json.dumps([{"role": m.type, "content": m.content} for m in request_payload], ensure_ascii=False)
            log_entry = AgentLog(
                task_id=task_id,
                node_name=node_name,
                request_payload=req_str,
                response_payload=response_payload,
                cost_tokens=cost_tokens
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
    if not api_key: missing_configs.append("API Key")
    if not base_url: missing_configs.append("Base URL")
    if not model_name: missing_configs.append("Model Name")
    
    if missing_configs:
        raise ValueError(f"缺少大模型配置: {', '.join(missing_configs)}。请在前端页面设置中填写，或在后端 .env 文件中配置。")
        
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        streaming=config.get("streaming", True),
        temperature=config.get("temperature", 0.5),
        max_tokens=config.get("max_tokens", 4096),
        model_kwargs={
            "max_completion_tokens": config.get("max_tokens", 4096), # 兼容某些服务商强制要求的 max_completion_tokens
            "frequency_penalty": config.get("frequency_penalty", 0.5)
        }   
    )

async def solve_image(image_url: str, review_feedback: Optional[str] = None, model_config: Optional[dict] = None, task_id: str = None) -> dict:
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
    response = await llm.ainvoke(messages)
    
    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or {}
    tokens = token_usage.get("total_tokens", 0)
    if task_id:
        # DB操作放进异步线程避免阻塞
        import asyncio
        asyncio.create_task(asyncio.to_thread(log_agent_interaction, task_id, "solver", messages, response.content, tokens))

    # 返回草稿和消耗的 token
    return {
        "draft": response.content,
        "tokens": tokens
    }

async def format_solution(draft_solution: str, model_config: Optional[dict] = None, task_id: str = None) -> dict:
    """
    封装调用模型进行最终排版润色的逻辑
    """
    llm = get_llm(model_config)
    
    # 构造专门用于排版的系统提示词
    sys_prompt = """# Role
你是一位【电路分析排版专家】，擅长使用 LaTeX 语法进行科学排版。你的任务是将不规范的电路解析重构为逻辑严密、排版精美的文档。

# Workflow
1. 接收解析内容并提取核心逻辑。
2. 使用 LaTeX 行内公式对所有物理量、方程和计算过程进行重排。
3. 匹配《考点延伸参考知识库》中的对应专题。

# Formatting Constraints
- 标题要求：解析内容必须以 `【解析】` 开头并紧接换行。
- 公式要求：尽量使用行内公式（如 `$U=IR$`），避免使用独立块状公式。
- 序列要求：禁止使用数字序号（如 1. 2. 3.）或明显的条目列表，请通过逻辑连接词（如“首先”、“由此”、“代入可得”）使正文成段落化。
- 末尾要求：在解析结束后，空一行，添加 `【考点延伸】` 部分。

# 《考点延伸参考知识库》
- 专题 1 电路基本定理：题型 1 功率计算 $P=UI$；题型 2 基尔霍夫定律；题型 3 含受控源电路。
- 专题 2 等效电路法：题型 1 戴维南及诺顿等效；题型 2 入端电阻 $Rin$。
- 专题 3 电路方程法：题型 1 支路电流法；题型 2 回路电流法；题型 3 节点电压法。
- 专题 4 电路定理法：题型 1 叠加定理；题型 2 等效电源定理；题型 3 特勒根与互易定理。
- 专题 5 正弦稳态：题型 1 相量法；题型 2 正弦功率。
- 专题 6 谐振与互感：题型 1 谐振电路；题型 2 互感/变压器。
- 专题 7 三相与非正弦：题型 1 三相电路；题型 2 非正弦。
- 专题 8 暂态分析：题型 1 一阶电路；题型 2 初始态突变。
- 专题 9 双口网络：题型 1 参数求解。    

# Output Format Example
【解析】
根据基尔霍夫电流定律可知，流入节点 $a$ 的电流 $I_1$ 等于...由此推导出...

【考点延伸】
专题 X 题型 Y 专题名称 """
    
    user_prompt = f"请对以下解题草稿进行最终排版：\n\n{draft_solution}"
        
    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_prompt)
    ]
    
    # 调用模型
    response = await llm.ainvoke(messages)
    
    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or {}
    tokens = token_usage.get("total_tokens", 0)
    if task_id:
        import asyncio
        asyncio.create_task(asyncio.to_thread(log_agent_interaction, task_id, "formatter", messages, response.content, tokens))
    
    # 返回最终结果和消耗的 token
    return {
        "formatted_result": response.content,
        "tokens": tokens
    }
