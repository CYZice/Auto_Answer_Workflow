import asyncio

from app.api import target_system_routes as routes


def test_open_ai_research_returns_same_origin_proxy_url():
    result = routes.open_ai_research_browser()

    assert result["state"] == "redirect"
    assert result["access_url"] == "/api/target-system/xuejie/#/xueba/ai_research"


class FakeResponse:
    status_code = 200
    content = b'{"code":200,"data":{"token":"proxy-token"}}'

    class Headers(dict):
        def get_list(self, _name):
            return []

    headers = Headers({"content-type": "application/json"})

    def json(self):
        return {"code": 200, "data": {"token": "proxy-token"}}


class FakeAsyncClient:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def request(self, *args, **kwargs):
        return FakeResponse()

    @property
    def is_closed(self):
        return False


def test_proxy_login_sets_same_origin_token_cookie(monkeypatch):
    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(routes, "xuejie_http_client", None)
    request = type(
        "RequestStub",
        (),
        {
            "method": "POST",
            "url": type("Url", (), {"query": ""})(),
            "headers": {},
            "cookies": {},
            "body": lambda self: asyncio.sleep(0, result=b"{}"),
        },
    )()

    result = asyncio.run(routes.proxy_xuejie(request, "admin/login"))

    assert result.status_code == 200
    assert "xuejie_proxy_token=proxy-token" in result.headers.get("set-cookie", "")


def test_proxy_root_auto_logs_in_with_configured_target_credentials(monkeypatch):
    class FakeLoginClient:
        token = "auto-token"

        async def login(self):
            return None

    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(routes, "TargetSystemClient", FakeLoginClient)
    monkeypatch.setattr(routes, "xuejie_http_client", None)
    request = type(
        "RequestStub",
        (),
        {
            "method": "GET",
            "url": type("Url", (), {"query": ""})(),
            "headers": {},
            "cookies": {},
            "body": lambda self: asyncio.sleep(0, result=b""),
        },
    )()

    result = asyncio.run(routes.proxy_xuejie(request, ""))

    assert result.status_code == 200
    assert "xuejie_proxy_token=auto-token" in result.headers.get("set-cookie", "")
