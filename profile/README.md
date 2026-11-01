# Wred

Small core. Explicit behavior. Optional capabilities.

[Wred](https://github.com/wred-asgi/wred) is an ultra-minimal Python ASGI framework
for JSON APIs, pronounced “red.” Its core handles routing, requests, and responses.
`orjson` is its only required dependency. Applications choose their own validation,
authentication, storage, and deployment tools.

```python
from wred import App

app = App()

@app.route("/hello/{name}")
async def hello(request, name):
    return {"hello": name}
```

Handlers are async functions returning JSON-serializable values, bytes,
`(body, headers, status)` tuples, or callable ASGI responses for streaming.
Wred handles HTTP; optional ASGI wrappers add lifecycle, browser sharing policy, middleware, and WebSocket routes.

## Philosophy

Keep the core small enough to understand. Make dependencies and execution order
visible. Add capabilities where an application needs them. Extensions use ordinary
decorators and ASGI wrappers without plugin discovery or changes to the core.
Independent packages follow a shared [extension contract](https://github.com/wred-asgi/.github/blob/main/EXTENSIONS.md) so
they can evolve together without becoming one large framework.

## Extensions

| Package | Capability |
|---|---|
| [wred-middleware](https://github.com/wred-asgi/wred-middleware) | Async request/response hooks and streaming-safe HTTP policy |
| [wred-websockets](https://github.com/wred-asgi/wred-websockets) | WebSocket routes, origin policy, bounded connections/messages |
| [wred-jinja2](https://github.com/wred-asgi/wred-jinja2) | Async HTML templates, autoescaping, bounded rendering |
| [wred-pydantic](https://github.com/wred-asgi/wred-pydantic) | Validation, serialization, OpenAPI, bundled Swagger UI |
| [wred-auth](https://github.com/wred-asgi/wred-auth) | Bearer auth, accounts, sessions, roles, social login, MFA |
| [wred-ratelimit](https://github.com/wred-asgi/wred-ratelimit) | Memory/Redis handler quotas |
| [wred-cache](https://github.com/wred-asgi/wred-cache) | Response caching and invalidation with memory/Redis |
| [wred-errors](https://github.com/wred-asgi/wred-errors) | Explicit exception-to-response mapping |
| [wred-pagination](https://github.com/wred-asgi/wred-pagination) | Offset/cursor pagination and SQLAlchemy adapters |
| [wred-lifespan](https://github.com/wred-asgi/wred-lifespan) | Async startup and shutdown hooks |
| [wred-cors](https://github.com/wred-asgi/wred-cors) | Browser origin policy and preflights, powered by Starlette |

OpenAPI lives in
`wred-pydantic`; there is no separate `wred-openapi` package.

## Composition

Register the complete decorator stack with `@app.route` outermost:

```python
from pydantic import BaseModel
from wred import App
from wred_pydantic import path, response
from wred_ratelimit import Limiter

app = App()
limiter = Limiter()

class ItemPath(BaseModel):
    item_id: int

@app.route("/items/{item_id}")
@limiter.limit("60/minute")
@path(ItemPath)
@response(dict)
async def item(request, item_id, path):
    return {"id": path.item_id}
```

Close resources with lifecycle shutdown hooks. Put auth and quotas outside caching
when they must run on hits. Use shared storage for multiworker quotas and cache
invalidation. See the [contract and production guidance](https://github.com/wred-asgi/.github/blob/main/EXTENSIONS.md) for
ordering, request state, metadata, and compatibility expectations.

The current baseline targets **Wred 0.15**. Read the [production and migration guide](https://github.com/wred-asgi/.github/blob/main/PRODUCTION.md)
for per-package limits, shared storage, failure policy, and reproducible release
gates. Candidate source compatibility does not imply every package is published.
