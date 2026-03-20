import sys
import os
import asyncio

# 将当前目录添加到 PYTHONPATH 以便找到 app 模块
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.database import engine, Base

def setup_db():
    print("🔧 正在初始化数据库表结构...")
    Base.metadata.create_all(bind=engine)
    print("✅ 数据库表结构初始化完成！")

def test_step1_data_layer():
    """
    测试基础设施与数据层搭建 (Step 1)
    1. 通过 API 成功向数据库插入一条 Task 记录
    2. 通过 API 成功查询出该记录
    """
    setup_db()
    
    # 使用 TestClient
    with TestClient(app) as client:
        print("🚀 [TEST] 正在启动测试: Step 1 基础设施与数据层搭建...")
        
        # 1. 测试创建任务
        test_image_url = "https://example.com/test_image.jpg"
        print(f"   -> 发起 POST /api/tasks 请求，数据: {{'image_url': '{test_image_url}'}}")
        response = client.post("/api/tasks", json={"image_url": test_image_url})
        
        if response.status_code == 201:
            data = response.json()
            task_id = data.get("task_id")
            status = data.get("status")
            print(f"   ✅ 创建成功! task_id: {task_id}, status: {status}")
        else:
            print(f"   ❌ 创建失败! 状态码: {response.status_code}, 响应: {response.text}")
            return

        # 2. 测试查询任务
        print(f"   -> 发起 GET /api/tasks/{task_id} 请求...")
        response = client.get(f"/api/tasks/{task_id}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ 查询成功! 任务详情:")
            for k, v in data.items():
                if v is not None:
                    print(f"      - {k}: {v}")
        else:
            print(f"   ❌ 查询失败! 状态码: {response.status_code}, 响应: {response.text}")
            return
            
        print("🎉 [TEST] Step 1 测试全部通过！")

if __name__ == "__main__":
    test_step1_data_layer()
