#!/usr/bin/env python3
"""测试自动化扫描 API 是否能正常返回任务"""

import asyncio
import sys
import os

# 添加 backend 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app.automation.api_client import XuejieApiClient


async def main():
    username = "13320115908"
    password = "2011590xue"

    client = XuejieApiClient()

    print("1. 测试登录...")
    try:
        result = await client.login(username, password)
        print(f"   登录成功: developer_id={result.developer_id}, nickname={result.nickname}")
    except Exception as e:
        print(f"   登录失败: {e}")
        return

    print("\n2. 测试获取待解题任务列表...")
    try:
        tasks = await client.list_pending_tasks(page=1, pagesize=50)
        print(f"   API 返回任务数: {len(tasks)}")

        if tasks:
            print("\n   前3个任务示例:")
            for i, task in enumerate(tasks[:3]):
                print(f"   [{i+1}] id={task.get('id')}, title={task.get('title', '')[:50]}, school={task.get('school_name', '')}")
        else:
            print("   警告: API 返回空列表!")
            print("   可能原因:")
            print("   - 网站确实没有待解题任务")
            print("   - developer_id 没有权限查看任务")
            print("   - API 接口有变化")
    except Exception as e:
        print(f"   获取任务失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n3. 测试获取学校列表...")
    try:
        schools = await client.list_schools()
        print(f"   学校数量: {len(schools)}")
        if schools:
            print(f"   前3个学校: {[s.get('school_name', '') for s in schools[:3]]}")
    except Exception as e:
        print(f"   获取学校列表失败: {e}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())