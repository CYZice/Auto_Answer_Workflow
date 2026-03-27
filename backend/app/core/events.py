import asyncio
from collections import defaultdict
import json

class TaskEventBus:
    def __init__(self):
        self.queues = defaultdict(list)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        self.queues[task_id].append(q)
        return q

    def publish(self, task_id: str, event_data: str):
        if task_id in self.queues:
            for q in self.queues[task_id]:
                q.put_nowait(event_data)

    def close(self, task_id: str):
        if task_id in self.queues:
            for q in self.queues[task_id]:
                q.put_nowait(None)
            del self.queues[task_id]

task_events = TaskEventBus()
