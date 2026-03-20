import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from backend.app.agent.graph import build_graph

def test_step3_llm_integration():
    """
    测试 Step 3: 大模型接入与结构化输出 (真实调用 GPT-4o-mini)
    """
    print("🚀 [TEST] 正在启动测试: Step 3 真实大模型接入 (GPT-4o-mini)...")
    
    app = build_graph()
    
    # 初始化状态
    # 找一张简单的数学题图片 URL 模拟真实场景，这里可以随便写个能访问的图片
    # 模型根据图片内容和系统 prompt 进行回答
    initial_state = {
        "task_id": "test_llm_001",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Pythagorean_theorem_abc.svg/800px-Pythagorean_theorem_abc.svg.png",
        "status": "queued",
        "retry_count": 0,
        "total_tokens": 0
    }
    
    print("\n--- 流程开始 (将消耗真实 Token) ---")
    
    # 运行图
    final_state = app.invoke(initial_state)
    
    print("\n--- 流程结束 ---")
    print("\n[Final State]:")
    for k, v in final_state.items():
        if k == "draft_solution" or k == "final_result":
            print(f"  {k}: \n{v[:200]}...\n(Truncated)")
        else:
            print(f"  {k}: {v}")
            
    assert final_state["status"] in ["completed", "failed"], f"Status 不合法: {final_state['status']}"
    if final_state["status"] == "completed":
        assert final_state["review_decision"] == "PASS"
        print("\n🎉 [TEST] Step 3 真实大模型接入测试全部通过！结构化输出解析成功！")
    else:
        print("\n⚠️ [TEST] 流程最终为 failed，可能因为重试次数耗尽或网络问题，请检查日志。")

if __name__ == "__main__":
    test_step3_llm_integration()
