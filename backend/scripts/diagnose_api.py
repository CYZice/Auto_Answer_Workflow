#!/usr/bin/env python3
"""诊断 API 能力：是否可以用 API 完全替代浏览器抓题"""
import asyncio
import httpx

BASE = "https://yy.xuejie.cn"

async def main():
    # 1. 登录
    r = await httpx.AsyncClient(timeout=30).post(
        f"{BASE}/admin/login",
        json={"username": "13320115908", "password": "2011590xue"},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    data = r.json()
    token = data["data"]["token"]
    dev_id = data["data"]["info"]["developer_id"]
    headers = {"Authorization": token, "Content-Type": "application/json", "Accept": "application/json"}
    print(f"[登录] dev_id={dev_id}")

    # 2. 获取待解题列表，检查返回字段
    r = await httpx.AsyncClient(timeout=30).post(
        f"{BASE}/admin/research/aiTopicList",
        json={"school_id": 0, "status": 0, "contain_img": 1, "developer_id": dev_id, "page": 1, "pagesize": 5},
        headers=headers,
    )
    tasks = r.json()["data"].get("list", [])
    print(f"\n[待解题列表] count={len(tasks)}")
    if tasks:
        t = tasks[0]
        print(f"  字段: {list(t.keys())}")
        print(f"  样本: id={t.get('id')} title={str(t.get('title',''))[:60]}")
        print(f"  topic_img: {t.get('topic_img', '无此字段')}")
        print(f"  img_url: {t.get('img_url', '无此字段')}")
        print(f"  image_url: {t.get('image_url', '无此字段')}")
        print(f"  picture: {t.get('picture', '无此字段')}")
        print(f"  所有字段值: {t}")
    else:
        print("  无待解题，尝试其他 status...")

    # 3. 检查已提交的题目详情
    r = await httpx.AsyncClient(timeout=30).post(
        f"{BASE}/admin/research/aiTopicList",
        json={"status": 6, "developer_id": dev_id, "page": 1, "pagesize": 3},
        headers=headers,
    )
    completed = r.json()["data"].get("list", [])
    print(f"\n[已提交列表] count={len(completed)}")
    if completed:
        t = completed[0]
        print(f"  字段: {list(t.keys())}")
        print(f"  样本: id={t.get('id')} title={str(t.get('title',''))[:60]}")
        print(f"  所有字段值: {t}")

        # 4. 获取详情
        tid = t.get("id")
        r = await httpx.AsyncClient(timeout=30).post(
            f"{BASE}/admin/research/aiTopicInfo",
            json={"id": tid},
            headers=headers,
        )
        detail = r.json().get("data", {})
        print(f"\n[任务详情] id={tid}")
        print(f"  字段: {list(detail.keys())}")
        print(f"  topic[:200]={str(detail.get('topic',''))[:200]}")
        print(f"  answer[:200]={str(detail.get('answer',''))[:200]}")
        print(f"  exam_point={detail.get('exam_point','')}")

asyncio.run(main())
