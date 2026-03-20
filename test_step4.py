import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

# 修复 SQLAlchemy 重复注册问题，只从 main 中引入，不单独引入 domain
from backend.app.main import app
from backend.app.core.database import engine, Base, SessionLocal
from backend.app.models.domain import Task

from fastapi.testclient import TestClient

def setup_db():
    print("🔧 正在初始化数据库表结构...")
    Base.metadata.create_all(bind=engine)
    print("✅ 数据库表结构初始化完成！")

def test_step4_api_binding():
    """
    测试 Step 4: API 与图引擎绑定 & 并发控制
    """
    setup_db()
    
    with TestClient(app) as client:
        print("🚀 [TEST] 正在启动测试: Step 4 API 绑定与图引擎异步执行...")
        
        # 1. 创建任务并触发后台图引擎
        test_image_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Pythagorean_theorem_abc.svg/800px-Pythagorean_theorem_abc.svg.png"
        print(f"   -> 发起 POST /api/tasks 请求，提交图片...")
        response = client.post("/api/tasks", json={"image_url": test_image_url})
        
        if response.status_code != 201:
            print(f"   ❌ 创建失败! 状态码: {response.status_code}, 响应: {response.text}")
            return
            
        task_id = response.json().get("task_id")
        print(f"   ✅ 创建成功! task_id: {task_id} (已进入后台处理)")
        
        # 2. 轮询获取任务状态 (等待图引擎执行完成)
        max_wait = 30 # 最多等30秒
        wait_time = 0
        final_state = "queued"
        
        print(f"   -> 正在轮询 GET /api/tasks/{task_id} ...")
        while wait_time < max_wait:
            res = client.get(f"/api/tasks/{task_id}")
            if res.status_code == 200:
                data = res.json()
                final_state = data.get("state")
                print(f"      [{wait_time}s] 当前状态: {final_state}")
                if final_state in ["completed", "failed"]:
                    break
            time.sleep(2)
            wait_time += 2
            
        # 3. 验证最终落库的数据
        res = client.get(f"/api/tasks/{task_id}")
        data = res.json()
        print("\n   [落库最终数据]:")
        for k, v in data.items():
            if k in ["history", "final_result"] and v:
                print(f"      - {k}: \n{v[:100]}...\n(Truncated)")
            else:
                print(f"      - {k}: {v}")
                
        assert final_state in ["completed", "failed"], "超时或未达终态"
        
        # 4. 测试人工干预接口 (将任务强行打为 fail)
        print("\n   -> 测试 POST /api/tasks/{task_id}/manual (强行 fail)")
        # 为了测试 manual 接口，我们先把状态改在允许的范围里（模拟）
        db = SessionLocal()
        task = db.query(Task).filter(Task.task_id == task_id).first()
        task.state = "manual"
        db.commit()
        db.close()
        
        res = client.post(f"/api/tasks/{task_id}/manual", json={"action": "fail"})
        assert res.status_code == 200
        print(f"   ✅ manual 接口调用成功: {res.json()}")
        
        print("\n🎉 [TEST] Step 4 API 绑定测试全部通过！")

if __name__ == "__main__":
    test_step4_api_binding()
