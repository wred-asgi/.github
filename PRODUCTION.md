# Production readiness — Wred 0.15

This is the organization's operational baseline and release checklist, not a
universal throughput guarantee. Run the service-backed suite and representative
load on the deployment's hardware, topology, data size, and dependency lock before
rollout. Wred remains a small HTTP core; extensions do not require a core registry.

## Supported release candidates

| Package | Candidate | Operational boundary |
|---|---|---|
| wred-auth | 0.2.0 | Transactional accounts; bounded Argon2 work; 64 KiB account request bodies; shared quotas required across workers |
| wred-cache | 0.2.0 | 1 MiB encoded entries; 64 MiB retained memory values; bounded metadata/locks; Redis deadlines and lease-checked publication |
| wred-cors | 0.2.0 | Exact-origin lookup; streaming and duplicate-header preservation; Starlette >=1.3.1,<2 |
| wred-errors | 0.1.1 | Explicit exception mapping; cancellation propagates; returned streaming responses execute outside the handler mapper |
| wred-lifespan | 0.1.1 | Per-hook deadlines; cleanup after partial startup and cancellation; cooperative asyncio required |
| wred-pagination | 0.2.0 | Default 100-item maximum and 10,000 offset cap; signed, bounded cursors; indexed deterministic ordering |
| wred-pydantic | 0.2.1 | Streaming body limits; bounded input validation; preserved native responses; locally bundled Swagger UI 5.34.1 loaded at mount |
| wred-ratelimit | 0.1.1 | Atomic Redis quotas; bounded pools/deadlines; fail closed by default; trusted peer identity |

All candidates admit Wred 0.15 and Python >=3.11. The shared contract is
[revision 1.1](EXTENSIONS.md); its revision is independent of the Wred version.
CORS/lifespan require Wred >=0.14; other extensions also declare 0.13 support.
Organization CI covers 0.14 and 0.15. The Pydantic tox matrix additionally covers
0.13. Test older per-package combinations before selecting them for deployment.

## Upgrade notes

- Upgrade the extension set together in a lock file. Resolve all desired extras in
  one transaction, run `pip check`, and test the actual wheels. Auth requires
  PyJWT >=2.15.1, Authlib >=1.9.1, cryptography >=50, and optional PyMongo >=4.18.3.
  Cache requires orjson >=3.11.6. These floors exclude known affected versions;
  they do not replace ongoing advisory scanning. Lock orjson >=3.11.6 even when
  deploying Wred without caching: the core's broad dependency range permits older
  versions. Never reuse an old environment without resolving its dependencies.
- Cache backend waits now default to 0.5 seconds; oversize responses bypass storage.
  Account bodies above 64 KiB now return 413. Pagination offsets above 10,000 now
  return 422; use indexed cursors, a measured custom cap, or explicitly opt into
  `maximum_offset=None` for legacy behavior.
- Custom cache stores must generate new unique versions for missing metadata.
  Expiring-lock stores must support lease-checked writes. Use a new namespace on
  upgrade and drain old workers before relying on the new guarantees: old workers
  do not fence expired fills. Redis Cluster is not supported by this cache adapter.
- Query values use Wred 0.15's UTF-8 replacement semantics. Invalid numbers still
  fail validation. Cache identity retains raw query bytes; explicit application
  keys must account for any canonicalization they perform.
- Swagger is bundled and checksum-pinned; review its upstream security history
  independently. Python advisory tools cannot audit dependencies compiled into a
  JavaScript bundle. Authlib's supported 1.x JOSE API currently emits deprecation
  warnings; migrate and revalidate provider flows before any Authlib 2 upgrade.

## Deployment requirements

1. Create clients once per worker, after process creation. Use lifespan hooks for
   startup and shutdown, with idempotent cleanup after partial initialization.
   Apply SQL migrations once per release, outside competing application workers.
   MongoDB account transactions require a replica set and majority writes.
2. Use Redis for shared quotas and invalidation. Memory backends are process-local
   and unsuitable for organization-wide quotas. Set Redis `maxmemory`, eviction
   policy, connection limits, TLS/authentication, and a namespace per application
   and environment. Never share eviction-sensitive quota state with disposable
   cache data unless the resulting quota resets are acceptable.
3. Bound ingress headers, query length, body size, request duration, and admitted
   concurrency. Apply backpressure before expensive JSON/Pydantic/Argon2 work.
   Library byte budgets exclude temporary encoding and Python object overhead.
   Cached bodies buffered by outer middleware already consumed memory.
4. Size worker count against Argon2 memory/CPU and database/Redis connection budgets:
   per-worker limits multiply by worker count. Auth overload returns 503; do not
   blindly retry credential operations. Set downstream transaction and network
   deadlines below the request budget. Test cancellation and shutdown budgets.
5. Authenticate and authorize before cache lookup. Include tenant, permissions,
   representation, filters, and identity in private keys; invalidate after durable
   mutations. Invalidation failures propagate and need a durable retry/outbox or
   a temporary cache bypass. A TTL is the remaining stale-data bound if recovery
   fails. Distributed cache fills can duplicate work and must be read-only.
6. Place CORS outside HTTP authentication/errors and caches. Configure exact trusted
   origins. CORS is browser sharing policy, not authentication or CSRF protection.
   Trust forwarded IPs only after the server validates the proxy chain.
7. Use stable managed keys across workers. Plan rotation/revocation and recovery
   for signing, MFA encryption, and cursor secrets. Keep credentials and token
   contents out of logs, cache keys, metrics labels, and build artifacts.
8. Observe p95/p99 latency, event-loop lag, RSS, active requests, pool exhaustion,
   cache hit/miss/bypass and invalidation errors, quota 429/503, Argon2 503,
   transaction conflicts/timeouts, and startup/shutdown failures. Use bounded
   metric labels and log sampling so an outage cannot flood telemetry.
9. Exercise backup restoration, rolling upgrades, provider/email outages, and
   database/Redis failover in staging. Canary each release and retain rollback
   artifacts, schema compatibility, and a cache namespace rollback policy.

## Reproducible gates

The [validation script](scripts/validate.py) resolves all eight source checkouts,
checks dependencies, builds wheels and sdists, rebuilds every sdist, installs the
wheels, and runs copied package/composition tests away from the source directories.
It requires service URLs instead of silently skipping all service coverage. It
then audits the resolved third-party Python dependencies. Unpublished extension
source and vendored browser assets are reviewed separately.

```sh
# Inside a fresh virtualenv; sibling directories are independent repo checkouts.
export WRED_TEST_REDIS=redis://127.0.0.1:6379/15
export WRED_TEST_POSTGRES=postgresql+asyncpg://user:password@127.0.0.1/test_database
export WRED_TEST_MONGODB='mongodb://127.0.0.1:27017/?replicaSet=test'
python .github/scripts/validate.py --extensions . --wred 0.15
```

Use disposable services: tests migrate/drop SQL tables and create/drop MongoDB
collections. Install a local `redis-server` binary too: the outage/restart tests
launch their own isolated instance. The [reusable workflow](.github/workflows/extensions.yml) runs this
process on Python 3.11–3.14, including Wred 0.14 and the common runtime dependency floors in
[constraints-minimum.txt](constraints-minimum.txt). Every
extension calls it for pushes and pull requests; the organization also runs weekly
to catch dependency drift. Cross-repository changes need a coordinated green run
once all candidates are available. Select these checks as required branch checks
in GitHub before enforcing a release policy; adding workflow files alone does not
configure branch protection.

Before publication: review API changes and version bumps; confirm clean candidate
commits, passing CI, wheel licenses/assets/migrations, and advisory scans; publish
in dependency order; install from the registry into a fresh environment; rerun the
composition and service gates. Keep the exact dependency lock and benchmark
results with deployment artifacts. Publication is a distinct step from source
readiness.

## Evidence and limits

See [the dated audit results](AUDIT-2026-11-01.md). Service correctness and bounded
load establish specific behavior; they do not establish regional failover,
production SLOs, maximum throughput, live Google/GitHub tenant configuration,
email deliverability, or a formal security audit. Production admission requires
those application-specific checks and sustained load/soak testing.
