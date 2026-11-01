# Wred extension contract v1.1 — Wred 0.15

Extensions add capabilities through ordinary Python callables. Wred does not
discover plugins, maintain an extension registry, or depend on extension packages.
This revision targets Wred 0.15 on Python 3.11+. Organization compatibility CI
also tests Wred 0.14; individual packages may support 0.13 where their tests prove
compatibility. Contract revisions and framework versions are independent.
Revision 1.1 preserves the v1 callable interface and adds the 0.15 query semantics
and explicit resource/failure requirements below.

## Route decorators

A decorator accepts an async handler and returns an async handler:

```python
from functools import wraps

def extension(*, option=None):
    def decorate(handler):
        @wraps(handler)
        async def wrapped(request, *args, **kwargs):
            return await handler(request, *args, **kwargs)
        return wrapped
    return decorate
```

- Accept the original `wred.Request` first. Forward remaining positional and
  keyword arguments unchanged. Wred supplies path parameters as string kwargs.
- Use `functools.wraps` with default attribute copying. Preserve `__wrapped__`,
  names, annotations, docstrings, and extension metadata for introspection.
- Await the inner handler at most once. A documented short circuit (auth failure,
  validation error, quota denial, cache hit) may skip it. Never retry application
  execution as recovery from a backend outage.
- Preserve JSON-serializable values, bytes, `(body, headers, status)` tuples, and
  callable ASGI responses. Tuple headers are ordered **string pairs**; preserve
  duplicates and status codes. Do not consume or serialize callable responses.
  Wred invokes them after the decorator stack has returned.
- Transform only documented response forms. Pydantic normalizes successful bodies
  but passes through callable responses, non-2xx tuples, and 204/205 tuples. Caching
  bypasses callable, private, unsuccessful, and nonserializable responses.
- Propagate unhandled exceptions and cancellation. Error mapping covers handler
  invocation, not later execution of a returned ASGI response.
- Validate configuration early. Document backend failures and fail-open/closed
  behavior. Keep dependencies in the extension that needs them.

## Argument injection and request state

Use a configurable named keyword (`body`, `query`, `path`, or `pagination` by
default). Reject names that are empty, not identifiers, or `request`. Raise
`TypeError` on collision with incoming kwargs instead of overwriting route
parameters or another extension's values. Applications must use distinct names.

Injectors share **`request.scope["wred.extensions.route_kwargs"]`**. The first
injector snapshots incoming kwargs before adding values; nested injectors reuse
that snapshot. Path validation reads it, so injected objects never become path
parameters. Treat the snapshot as read-only. The creator removes it in `finally`,
including on exceptions and cancellation. Do not create it for a short circuit.

```python
owns_context = "wred.extensions.route_kwargs" not in request.scope
if owns_context:
    request.scope["wred.extensions.route_kwargs"] = dict(kwargs)
try:
    return await handler(request, *args, **kwargs, **{argument: value})
finally:
    if owns_context:
        del request.scope["wred.extensions.route_kwargs"]
```

Each request owns its scope. Do not concurrently dispatch the same `Request`
through unrelated stacks. Sequential calls restore context correctly. Other state
must use package-owned scope keys, never global request state. `wred.auth` belongs
to authentication; read it through `wred_auth.principal(request)`. Read bodies
through Wred's cached `body()`/`json()` methods. Restore temporary receive wrappers
in `finally`.

## Query decoding in Wred 0.15

Use `request.query` for decoded application values. Wred decodes raw ASGI query
bytes as UTF-8 with replacement, then applies `parse_qsl(..., keep_blank_values=True)`
and returns a dictionary. Percent escapes are decoded, `+` becomes space, blank
values survive, repeated names use their last value, and malformed UTF-8 becomes
U+FFFD. Extensions must not reinterpret replacement characters as valid numbers or
silently discard them to make validation succeed. Malformed numeric pagination
values return 422; string validators may accept the replacement character.

Different byte sequences can decode to the same string. Signature verification,
canonicalization, duplicate-sensitive inputs, and default cache identity must use
`scope["query_string"]` where exact bytes matter. Do not assume this dictionary is
a lossless multimap. Wred 0.14 could raise on malformed raw UTF-8; extensions may
retain their older compatibility handling without changing 0.15 semantics.

## Metadata

`__wred_openapi__` is a dictionary consumed by `wred_pydantic.OpenAPI`. Copy the
dictionary and any nested collections before changing them; never mutate an
inner handler's metadata. Preserve unknown entries. Merge independent entries;
reject incompatible explicit definitions instead of silently discarding them.

| Entry | Meaning |
|---|---|
| `body`, `query`, `path` | Pydantic input JSON schemas |
| `responses` | Status strings mapped to OpenAPI response definitions |
| `parameters` | OpenAPI parameter list, including pagination |
| `security` | OpenAPI security requirements, including authentication |
| `operationId`, `summary`, `description`, `tags` | Explicit operation metadata |

Auth supplies security defaults only when no explicit requirement exists.
Configure the matching security scheme on `OpenAPI`. Metadata does not enforce
runtime behavior.

## Ordering

Python applies decorators bottom to top. Requests enter top to bottom; responses
unwind in reverse. **Put `@app.route(...)` outermost** so Wred registers the fully
wrapped handler. A wrapper outside `@app.route` does not replace the registration.

Typical order, adjusted deliberately for application policy:

```python
@api.route("/items/{item_id}")
@handle_errors(error_handlers)
@auth.require_auth()
@limiter.limit("60/minute", key=lambda r: principal(r).id)
@cache.cache(private=True, key=user_and_request_key)
@paginate()
@path(ItemPath)
@response(ItemPage)
async def items(request, item_id, pagination, path):
    ...
```

Authentication/authorization must precede cache lookup. Put quotas outside caching
to charge hits too. Identity-based quotas run after auth; IP quotas can precede
auth to limit unauthenticated traffic. Error mapping sees only inner exceptions.

Input decorators compose in any order with distinct argument names. Caching
outside them avoids repeated validation. Inside them, nonserializable injected
values safely bypass caching. An explicit cache key must include every input
affecting the response. Private keys also need identity, tenant, path, query, and
relevant permissions/representation state; invalidate when that state changes.

## ASGI wrappers and lifecycle

App-wide features accept and return an ASGI callable with `(scope, receive, send)`.
Register routes on the inner `App`, then serve the wrapper. Forward unowned
protocols unchanged. Preserve duplicate headers, streaming chunks, backpressure,
and cancellation; do not buffer responses merely to add headers.

`cors_application` handles preflights before routing. Put it outside HTTP auth/error
middleware and handler caches so it adds policy headers to each response.
`lifespan_application` owns startup/shutdown and forwards HTTP unchanged. Wrap
lifespan once and close caches, limiters, and database resources at shutdown.

## Independent evolution and production checks

No shared runtime package is required. Add conventions here before packages depend
on them. Breaking changes need a new contract revision, migration guidance, and
coordinated compatibility tests. Use maintained libraries for complex protocols.

The organization-owned [composition suite](tests/test_composition.py) exercises
real routes, decorator order, cache-hit guards, native responses, metadata, and
context cleanup. Package tests own their algorithms, storage, and protocols.
Before widening supported versions, resolve all packages together and run both
suites against supported versions.

Memory caches/quotas are per process. Use Redis when workers need shared state,
with explicit namespaces, bounded pools, timeouts, and an outage policy. Set
ingress size limits, worker concurrency, and shutdown budgets. Run service tests
and workload-specific benchmarks before rollout; in-process throughput is not a
production capacity guarantee.

## Resource and failure contract

- Bound retained cache values, invalidation metadata, fill-lock cardinality, and
  external connection pools. Bound backend waits separately from handler time.
  Evicted or expired invalidation metadata must create a fresh generation. A fill
  whose Redis lease expired must not overwrite a newer fill.
- Do not cache `Set-Cookie`, `Vary: *`, or `private`, `no-store`, or `no-cache`
  responses. Honor request `no-store` and `no-cache`. Explicit keys transfer
  responsibility for representation and authorization isolation to the caller.
- Check body limits while receiving chunks, even without `Content-Length`. Check
  bodies already buffered by an outer wrapper too. Ingress limits are still
  required to bound allocations made before the extension runs.
- Bound pagination limits and offsets by default. Prefer indexed keyset pagination
  for deep traversal. Validate finite positive deadlines and TTLs at configuration.
- Cache outages bypass storage; failed invalidations propagate. Quotas fail closed
  by default; auth storage outages must not become successful authentication.
  Preserve handler errors and cancellation even if cleanup fails.
- Initialize shared clients and static assets once per worker. Own and close
  resources explicitly. Libraries cannot forcibly stop blocking application code.

See [production readiness and rollout](PRODUCTION.md) for defaults, migration,
repeatable checks, and the limits of the current evidence.
