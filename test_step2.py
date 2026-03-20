import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from backend.app.agent.graph import build_graph

def test_step2_graph():
    """
    测试 Step 2: 图状态机与节点流转逻辑。
    模拟一次 PRD 要求的流转：Solver -> Reviewer (FAIL) -> Solver (Retry) -> Reviewer (PASS) -> Formatter
    """
    print("🚀 [TEST] 正在启动测试: Step 2 图状态机连线逻辑...")
    
    # 编译图
    app = build_graph()
    
    # 初始化状态
    initial_state = {
        "task_id": "test_task_001",
        "image_url": "https://example.com/test.png",
        "status": "queued",
        "retry_count": 0,
        "total_tokens": 0
    }
    
    print("\n[Input State]:")
    print(initial_state)
    print("\n--- 流程开始 ---")
    
    # 运行图
    final_state = app.invoke(initial_state)
    
    print("\n--- 流程结束 ---")
    print("\n[Final State]:")
    for k, v in final_state.items():
        print(f"  {k}: {v}")
        
    # 断言验证 PRD 规则
    assert final_state["status"] == "completed", "Status 应该是 completed"
    assert final_state["retry_count"] == 1, "应该经历了一次重试"
    assert final_state["total_tokens"] == 100*2 + 50*2 + 30, "Token 消耗应为两次Solver+两次Reviewer+一次Formatter"
    assert "Final Result" in final_state["final_result"], "必须包含排版结果"
    
    print("\n🎉 [TEST] Step 2 图状态机逻辑测试全部通过！完全符合 PRD 要求的流转！")

if __name__ == "__main__":
    test_step2_graph()
