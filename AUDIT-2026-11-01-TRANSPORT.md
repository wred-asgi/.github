# Middleware and WebSocket extension validation — 2026-11-01

New source candidates: `wred-middleware` 0.1.0 and `wred-websockets` 0.1.0.
No changes to Wred's core. Contract revision 1.2 adds HTTP hook and WebSocket
ownership rules while retaining Wred 0.14–0.15 compatibility. All eleven packages
participate in the shared CI resolver, distribution builds, and composition suite.

## Evidence

- Python 3.14.4 / Wred 0.15, latest resolved dependencies: **335 passed, 1 skipped**
  in the installed-wheel organization suite.
- Python 3.11 / Wred 0.14 / minimum runtime constraints, including Starlette 1.3.1:
  **334 passed, 2 skipped** in the installed-wheel organization suite.
- Each run built wheels and sdists, rebuilt the sdists, installed the wheels,
  copied tests outside the source checkouts, and passed `pip check` and a full
  third-party dependency advisory scan (no known vulnerabilities).
- Service-backed tests used disposable local Redis, PostgreSQL 18 and MongoDB
  8.2.9 replica-set processes. Skips are the existing SQLite single-writer
  limitation, plus the Wred 0.15 malformed-query contract on Wred 0.14.
- The new packages own **72 focused tests**. Real transport coverage uses one and
  two Uvicorn workers, exercising HTTP delegation, origin/auth denial, subprotocols,
  text/UTF-8 echo, and both extension-side and server-side message limits.
- The organization adds cached-HTML/auth/CORS/hook composition and independent
  HTTP/WebSocket/lifespan ownership coverage. Existing package tests still own
  their algorithms and service behavior.
- Ruff checks and Git whitespace checks pass for the changed code.

## Failure boundaries reviewed

Application HTTP hooks preserve streaming/backpressure and repeated headers, do
not read request bodies, and observe late stream errors in cleanup. Handler hooks
preserve metadata, forwarded arguments, native responses, and the original Request.
Both enforce hook ordering, early return behavior, cleanup on error/cancellation,
and cooperative deadlines; original failures win over cleanup failures.

WebSocket tests cover per-worker admission and slot release, UTF-8 byte limits in
both directions, handshake/receive/send deadlines, cancellation, handler failures,
subprotocol negotiation, ASGI version restrictions, and concurrent I/O rejection.
A regression test first reproduced TaskGroup-wrapped normal disconnects being
misclassified as application errors; the repaired boundary treats groups containing
only disconnect/policy exceptions as connection endings, while preserving mixed
application failures. Receive/send operations retain backpressure with no extension
message queue. Configuration and routes are validated before serving.

## Reproducible local load smoke

Run from an installed organization environment:

```sh
python scripts/transport_load.py --workers 2 --connections 100 --messages 100 --requests 2000
```

The [recorded run](evidence/transport-load-2026-11-01.json) completed 10,000 WebSocket
round trips over 100 connections and 2,000 concurrent-workload HTTP requests in
1.503 seconds. Every echo and route parameter matched; request IDs stayed unique.
WebSocket round-trip median was 6.196 ms and p95 16.662 ms on this local host.
The JSON records exact package/server versions. The script uses temporary apps,
loopback sockets, and bounded server message queues, and shuts its workers down.

This is a short correctness/load smoke, not a soak, memory ceiling, fleet capacity
measurement, or proxy/broker-failure certification. Configure ingress/server frame
and queue limits, worker admission, authentication/revocation, broker fanout and
slow-consumer policy, reconnect jitter and rolling-shutdown behavior for the real
deployment. Neither package introduces a distributed connection/room registry.
