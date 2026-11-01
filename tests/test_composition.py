"""Organization-owned contracts exercised against the real Wred router."""

import asyncio
import inspect
from importlib.metadata import version
from itertools import permutations

import httpx
import pytest
from jinja2 import DictLoader
from pydantic import BaseModel, ConfigDict
from wred import App, Request
from wred_auth import Auth, AuthError, Principal, principal
from wred_cache import Cache
from wred_cors import cors_application
from wred_errors import handle_errors
from wred_jinja2 import Templates
from wred_lifespan import lifespan_application
from wred_pagination import paginate
from wred_pydantic import OpenAPI, operation, path, query, response
from wred_ratelimit import Limiter


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: int


class Filters(BaseModel):
    label: str


@pytest.mark.parametrize(
    "order", list(permutations(("path", "query", "page", "cache")))
)
async def test_injected_arguments_compose_in_any_order(order):
    app = App()
    async with Cache() as cache:
        decorators = {
            "path": path(Route),
            "query": query(Filters),
            "page": paginate(),
            "cache": cache.cache(),
        }

        async def handler(request, item_id, path, query, pagination):
            return {
                "raw_id": item_id,
                "id": path.item_id,
                "label": query.label,
                "limit": pagination.limit,
            }

        for name in reversed(order):
            handler = decorators[name](handler)
        app.route("/items/{item_id}")(handler)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            for _ in range(2):
                result = await client.get("/items/7?label=hello&limit=2")
                assert result.status_code == 200
                assert result.json() == {
                    "raw_id": "7",
                    "id": 7,
                    "label": "hello",
                    "limit": 2,
                }
            assert (await client.get("/items/no?label=hello")).status_code == 422


async def verify(token):
    if token not in ("alice", "bob"):
        raise AuthError()
    return Principal(token)


@pytest.mark.skipif(version("wred") != "0.15", reason="Wred 0.15 decoding contract")
async def test_query_decoding_preserves_validation_and_raw_cache_identity():
    calls = 0
    async with Cache() as cache:

        @cache.cache()
        @paginate()
        @query(Filters)
        async def handler(request, pagination, query):
            nonlocal calls
            calls += 1
            return {"label": query.label, "revision": calls}

        async def invoke(raw):
            request = Request(
                {"path": "/", "method": "GET", "query_string": raw}, None, None
            )
            return await handler(request)

        # Both decode to replacement characters, but raw cache keys stay distinct.
        for raw, revision in ((b"label=\xff", 1), (b"label=%EF%BF%BD", 2)):
            for _ in range(2):
                assert await invoke(raw) == {"label": "\ufffd", "revision": revision}
        assert await invoke(b"label=first&label=last+value&empty=") == {
            "label": "last value",
            "revision": 3,
        }
        assert (await invoke(b"label=ok&limit=\xff"))[2] == 422
        assert calls == 3


async def test_guards_run_on_cache_hits_and_metadata_reaches_openapi():
    api = App()
    auth = Auth(verifier=verify)
    calls = 0
    async with Cache() as cache, Limiter(strategy="fixed") as limiter:

        @api.route("/items/{item_id}")
        @auth.require_auth()
        @limiter.limit("2/hour", key=lambda r: principal(r).id)
        @cache.cache(private=True, key=lambda r: (principal(r).id, r.path))
        @handle_errors({LookupError: lambda exc, r: ({"detail": "missing"}, [], 404)})
        @paginate()
        @path(Route)
        @response(dict)
        async def handler(request, item_id, pagination, path):
            """Read one user's items."""
            nonlocal calls
            calls += 1
            return {"id": path.item_id, "user": principal(request).id}

        assert inspect.signature(handler) == inspect.signature(inspect.unwrap(handler))
        assert handler.__name__ == "handler"
        assert handler.__doc__ == "Read one user's items."
        app = cors_application(
            lifespan_application(api, on_shutdown=[cache.aclose, limiter.aclose]),
            allow_origins=["https://app.example"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"origin": "https://app.example"},
        ) as client:
            for _ in range(2):
                result = await client.get(
                    "/items/7", headers={"authorization": "Bearer alice"}
                )
                assert result.json() == {"id": 7, "user": "alice"}
            assert calls == 1
            unauthorized = await client.get("/items/7")
            assert unauthorized.status_code == 401
            assert (
                unauthorized.headers["access-control-allow-origin"]
                == "https://app.example"
            )
            bob = await client.get("/items/7", headers={"authorization": "Bearer bob"})
            assert bob.json() == {"id": 7, "user": "bob"}
            denied = await client.get(
                "/items/7", headers={"authorization": "Bearer alice"}
            )
            assert denied.status_code == 429 and "retry-after" in denied.headers

        document = OpenAPI(
            api, security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}}
        ).document()
        operation = document["paths"]["/items/{item_id}"]["get"]
        assert operation["security"] == [{"bearerAuth": []}]
        assert {(p["in"], p["name"]) for p in operation["parameters"]} == {
            ("path", "item_id"),
            ("query", "limit"),
            ("query", "offset"),
        }
        assert "200" in operation["responses"]


@pytest.mark.parametrize("kind", ["json", "bytes", "tuple", "asgi", "error"])
async def test_native_responses_survive_composed_decorators(kind):
    app = App()
    calls = 0
    async with Cache() as cache, Limiter(strategy="fixed") as limiter:

        @app.route("/items/{item_id}")
        @limiter.limit("10/hour")
        @cache.cache()
        @handle_errors({LookupError: lambda exc, r: (b"missing", [], 404)})
        @paginate()
        async def handler(request, item_id, pagination):
            nonlocal calls
            calls += 1
            assert item_id == "7" and pagination.limit == 25
            if kind == "error":
                raise LookupError()
            if kind == "json":
                return {"id": item_id}
            if kind == "bytes":
                return b"raw"
            if kind == "tuple":
                return b"created", [("x-value", "one"), ("x-value", "two")], 201

            async def stream(scope, receive, send):
                await send(
                    {"type": "http.response.start", "status": 202, "headers": []}
                )
                await send({"type": "http.response.body", "body": b"stream"})

            return stream

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            for _ in range(2):
                result = await client.get("/items/7")
                status, content = {
                    "json": (200, b'{"id":"7"}'),
                    "bytes": (200, b"raw"),
                    "tuple": (201, b"created"),
                    "asgi": (202, b"stream"),
                    "error": (404, b"missing"),
                }[kind]
                assert (result.status_code, result.content) == (status, content)
                if kind == "tuple":
                    assert result.headers.get_list("x-value") == ["one", "two"]
            assert calls == (2 if kind in ("asgi", "error") else 1)


@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
async def test_argument_context_is_released_when_the_stack_unwinds(failure):
    request = Request({"path": "/7", "method": "GET"}, None, None)

    @paginate()
    @path(Route)
    async def handler(request, item_id, pagination, path):
        if failure:
            raise failure()
        return path.item_id

    if failure:
        with pytest.raises(failure):
            await handler(request, item_id="7")
    else:
        assert await handler(request, item_id="7") == 7
        assert await handler(request, item_id="8") == 8
    assert "wred.extensions.route_kwargs" not in request.scope


async def test_html_templates_compose_with_guards_cache_inputs_and_openapi():
    app = App()
    templates = Templates(
        loader=DictLoader(
            {"page": "<h1>{{ label }}</h1>{{ user }}:{{ item_id }}:{{ limit }}"}
        )
    )
    auth = Auth(verifier=verify)
    calls = 0
    async with Cache() as cache:

        @app.route("/items/{item_id}")
        @auth.require_auth()
        @cache.cache(private=True, key=lambda r: (principal(r).id, r.path, r.query))
        @templates.template("page")
        @paginate()
        @query(Filters)
        @path(Route)
        @operation(
            responses={
                200: {
                    "description": "HTML page",
                    "content": {"text/html": {"schema": {"type": "string"}}},
                }
            }
        )
        async def handler(request, item_id, pagination, query, path):
            nonlocal calls
            calls += 1
            return {
                "label": query.label,
                "user": principal(request).id,
                "item_id": path.item_id,
                "limit": pagination.limit,
            }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            for token in ("alice", "bob"):
                for _ in range(2):
                    result = await client.get(
                        "/items/7",
                        params={"label": "<tag>", "limit": "2"},
                        headers={"authorization": "Bearer " + token},
                    )
                    assert result.text == f"<h1>&lt;tag&gt;</h1>{token}:7:2"
                    assert result.headers["content-type"] == "text/html; charset=utf-8"
            assert calls == 2
            assert (await client.get("/items/7?label=x")).status_code == 401
            invalid = await client.get(
                "/items/no?label=x", headers={"authorization": "Bearer alice"}
            )
            assert invalid.status_code == 422
            assert invalid.headers["content-type"] == "application/json"

    document = OpenAPI(
        app, security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}}
    ).document()
    operation_schema = document["paths"]["/items/{item_id}"]["get"]
    assert operation_schema["responses"]["200"]["content"] == {
        "text/html": {"schema": {"type": "string"}}
    }
    assert operation_schema["security"] == [{"bearerAuth": []}]


async def test_http_hooks_compose_with_cached_html_auth_cors_and_metadata():
    from wred_middleware import hooks, middleware_application

    app = App()
    auth = Auth(verifier=verify)
    templates = Templates(loader=DictLoader({"page": "<p>{{ user }}</p>"}))
    cleanups = []
    calls = 0
    counter = 0

    async def identify(scope):
        nonlocal counter
        counter += 1
        scope["example.id"] = str(counter)

    async def headers(scope, head):
        head.headers.append(("X-Request-ID", scope["example.id"]))

    async def cleanup(scope, error):
        cleanups.append(error)

    async def context(request, result):
        return {**result, "user": principal(request).id}

    async with Cache() as cache:

        @app.route("/items/{item_id}")
        @auth.require_auth()
        @cache.cache(private=True, key=lambda r: (principal(r).id, r.path))
        @templates.template("page")
        @hooks(after=context)
        @path(Route)
        async def endpoint(request, item_id, path):
            nonlocal calls
            calls += 1
            return {"item": path.item_id}

        wrapped = cors_application(
            middleware_application(
                app, before=identify, after=headers, cleanup=cleanup
            ),
            allow_origins=["https://client.example"],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(wrapped), base_url="http://test"
        ) as client:
            for token in ["alice", "alice", "bob"]:
                result = await client.get(
                    "/items/7",
                    headers={
                        "authorization": "Bearer " + token,
                        "origin": "https://client.example",
                    },
                )
                assert result.text == f"<p>{token}</p>"
                assert result.headers["x-request-id"] == str(counter)
                assert (
                    result.headers["access-control-allow-origin"]
                    == "https://client.example"
                )
            denied = await client.get("/items/7")
            assert denied.status_code == 401 and denied.headers["x-request-id"] == "4"
        assert calls == 2 and cleanups == [None] * 4
        assert (
            endpoint.__wred_openapi__["path"]["properties"]["item_id"]["type"]
            == "integer"
        )


async def test_websockets_pass_through_http_policy_and_single_lifespan_owner():
    from wred_middleware import middleware_application
    from wred_websockets import WebSockets

    app = App()
    sockets = WebSockets(app, allowed_origins=["https://client.example"])
    lifecycle = []

    async def start():
        lifecycle.append("start")

    async def stop():
        lifecycle.append("stop")

    async def http_only(scope):
        assert scope["type"] == "http"

    @sockets.route("/room/{room}")
    async def echo(websocket, room):
        await websocket.accept()
        await websocket.send_text(room)

    @app.route("/health")
    async def health(request):
        return {"ok": True}

    wrapped = lifespan_application(
        cors_application(
            middleware_application(sockets, before=http_only),
            allow_origins=["https://client.example"],
        ),
        on_start=start,
        on_shutdown=stop,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(wrapped), base_url="http://test"
    ) as client:
        assert (await client.get("/health")).json() == {"ok": True}
    sent = []

    async def send(message):
        sent.append(message)

    async def connect():
        return {"type": "websocket.connect"}

    await wrapped(
        {
            "type": "websocket",
            "path": "/room/general",
            "headers": [(b"origin", b"https://client.example")],
        },
        connect,
        send,
    )
    assert [m["type"] for m in sent] == [
        "websocket.accept",
        "websocket.send",
        "websocket.close",
    ]
    assert sent[1]["text"] == "general"
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive():
        return next(messages)

    sent.clear()
    await wrapped({"type": "lifespan"}, receive, send)
    assert lifecycle == ["start", "stop"]
    assert [m["type"] for m in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
