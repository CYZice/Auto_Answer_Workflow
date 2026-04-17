#!/usr/bin/env python3
"""测试 XuejieApiClient"""
import asyncio
import sys
sys.path.insert(0, ".")
from app.automation.api_client import XuejieApiClient

async def main():
    client = XuejieApiClient()

    print("[1] 登录...")
    result = await client.login("13320115908", "2011590xue")
    print(f"    Token: {result.token[:20]}... | dev_id={result.developer_id}")

    print("\n[2] 学校列表...")
    schools = await client.list_schools()
    print(f"    数量: {len(schools)}")
    if schools:
        print(f"    前2个: {[(s.get('school_id'), s.get('school_name','')) for s in schools[:2]]}")

    print("\n[3] 待解题列表...")
    tasks = await client.list_pending_tasks(pagesize=5)
    print(f"    数量: {len(tasks)}")
    for t in tasks[:3]:
        print(f"    - id={t.get('id')} title={str(t.get('title',''))[:50]}")

    if tasks:
        task_id = tasks[0].get("id")
        print(f"\n[4] 任务详情 (id={task_id})...")
        d = await client.get_task_detail(task_id)
        print(f"    topic[:80]={str(d.get('topic',''))[:80]}")

        print(f"\n[5] 抢单 (id={task_id})...")
        r = await client.grab_task(task_id)
        print(f"    抢单响应: {r}")

        print(f"\n[6] 保存答案...")
        r = await client.save_answer(
            task_id=task_id,
            topic="<p>测试题目</p>",
            answer="<p>【正解】A</p><p>【解析】测试</p>",
            topic_text="测试题目",
            exam_point="测试考点",
            status=6,
            is_dev_submit=1,
        )
        print(f"    保存响应: {r}")
    else:
        print("\n[skip] 无待解题任务")

    print("\n[+] 测试完成!")
    await client.close()

asyncio.run(main())
