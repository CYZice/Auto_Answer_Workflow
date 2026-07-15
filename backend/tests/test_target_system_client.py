import asyncio

from app.services.target_system_client import TargetSystemClient


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeClient:
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/admin/login"):
            return FakeResponse({"code": 200, "data": {"token": "session-token", "uuid": "session-uuid", "info": {"developer_id": 754}}})
        return FakeResponse({"code": 200, "data": {}})


def test_claim_keeps_login_cookie(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr("app.services.target_system_client.httpx.AsyncClient", FakeClient)
    client = TargetSystemClient({"base_url": "https://target.example", "username": "user", "password": "password"})

    asyncio.run(client.claim("89611"))

    assert FakeClient.calls[1] == (
        "https://target.example/admin/research/startDevAiTopic",
        {
            "json": {"id": 89611},
            "headers": {"Authorization": "session-token", "Content-Type": "application/json"},
            "cookies": {"token": "session-token", "uuid": "session-uuid"},
        },
    )
