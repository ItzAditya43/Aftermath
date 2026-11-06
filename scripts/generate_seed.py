"""Generate data/seed_incidents.json and data/demo_alerts.json.

Deterministic: running it twice produces identical files. Edit the INCIDENTS list below and
re-run `python scripts/generate_seed.py` to change the demo history.

Recurring patterns (so memory has something to find), with time-to-resolve dropping each time:
  payments_pool   payments-api HikariCP pool exhausted        INC-1001 (190m) -> 1009 (68m) -> 1017 (24m)
  gateway_502     api-gateway 502s after Friday deploys        INC-1002 (145m) -> 1011 (52m) -> 1020 (18m)
  notif_oom       notification-worker OOMKilled in batch jobs  INC-1005 (175m) -> 1012 (80m) -> 1018 (30m)
  orders_lock     orders-db migration lock contention          INC-1008 (110m) -> 1015 (35m)
  jwks_rotation   auth-service JWKS key rotation               INC-1006 (115m) -> 1016 (22m)
  stale_chunks    checkout-web ChunkLoadError after deploy     INC-1007 (70m)  -> 1019 (15m)
plus one-offs, and INC-1010 (checkout-web) cascading from INC-1009 (payments-api).

Log lines use {t-SECONDS} (ISO-8601) and {pg-SECONDS} (Postgres format) placeholders relative to
the incident's created_at, so timestamps inside logs line up with the incident.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_OUT = ROOT / "data" / "seed_incidents.json"
DEMO_OUT = ROOT / "data" / "demo_alerts.json"


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def pg(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d} UTC"


def render_logs(template: str, created: datetime) -> str:
    def repl(match: re.Match) -> str:
        kind, sign, seconds = match.group(1), match.group(2), float(match.group(3))
        moment = created + timedelta(seconds=seconds if sign == "+" else -seconds)
        return iso(moment) if kind == "t" else pg(moment)

    return re.sub(r"\{(t|pg)([+-])(\d+(?:\.\d+)?)\}", repl, template.strip("\n"))


# ---------------------------------------------------------------------------
# The incidents (chronological; IDs are assigned in this order from INC-1001)
# ---------------------------------------------------------------------------

INCIDENTS: list[dict] = [
    # INC-1001 ----------------------------------------------------------------
    dict(
        pattern="payments_pool",
        title="payments-api: connection pool exhausted, card charges failing",
        service="payments-api",
        severity="SEV1",
        created="2026-05-12T14:05:00",
        ttr=190,
        alert_text=(
            "[FIRING] PaymentsApiHighErrorRate: 5xx rate 41.7% on payments-api (threshold 5%) for 5m. "
            "p99 latency 30.2s. HikariPool-1 active=20/20, pending=187."
        ),
        logs="""
{t-62.4} ERROR [http-nio-8080-exec-17] c.a.payments.api.ChargeController - POST /v1/charges failed (order_id=ord_8f2k1x)
org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:84)
    at org.springframework.jdbc.core.JdbcTemplate.execute(JdbcTemplate.java:376)
    at com.aftermath.payments.repo.ChargeRepository.insert(ChargeRepository.java:52)
    at com.aftermath.payments.service.ChargeService.charge(ChargeService.java:131)
Caused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:181)
{t-58.9} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Pool stats (total=20, active=20, idle=0, waiting=187)
{t-41.0} ERROR [refund-reconciler-3] c.a.payments.refund.RefundService - Refund provider call timed out after 10000ms (refund_id=rf_19bd02), retrying (attempt 3/5)
{t-12.3} ERROR [http-nio-8080-exec-44] c.a.payments.api.ChargeController - POST /v1/charges failed (order_id=ord_8f2k9q): HikariPool-1 - Connection is not available, request timed out after 30000ms.
""",
        triage="On-call acknowledged. payments-api pods healthy but every request waits on the DB pool; payments-db CPU only 18%.",
        failed_attempts=[
            "Rolling restart of payments-api pods (pool was exhausted again within 6 minutes)",
            "Scaled payments-api from 6 to 10 replicas (pushed payments-db to 182/200 connections without helping)",
        ],
        root_cause=(
            "Connection leak in RefundService.reconcile(): when the refund provider call timed out, the retry path "
            "returned early without closing its JDBC connection, so the 20-connection HikariCP pool drained during "
            "the afternoon refund reconciliation run"
        ),
        resolution=(
            "Hotfixed RefundService.reconcile() to use try-with-resources so connections are always returned; "
            "raised HikariCP maximumPoolSize from 20 to 40; enabled leakDetectionThreshold=20000 to log leaked "
            "connections; deployed payments-api v3.18.2"
        ),
        notes="Leak detection would have found this in minutes. Keep it enabled.",
    ),
    # INC-1002 ----------------------------------------------------------------
    dict(
        pattern="gateway_502",
        title="api-gateway: 502 Bad Gateway on /api/* after Friday deploy",
        service="api-gateway",
        severity="SEV2",
        created="2026-05-15T17:20:00",
        ttr=145,
        alert_text=(
            "[FIRING] ApiGateway5xxRate: 502 rate 21.4% on api-gateway (threshold 2%). Started 4 minutes after "
            "deploy of api-gateway release v2.41.0."
        ),
        logs="""
{t-240} INFO  deploy api-gateway release v2.41.0 rolled out (3/3 pods ready) by ci-bot, change: "routes: tune upstream connection settings"
{t-31.2} {"ts":"{t-31.2}","method":"POST","path":"/api/orders","status":502,"upstream":"orders-svc:8080","response_flags":"UC","duration_ms":3,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
{t-30.8} {"ts":"{t-30.8}","method":"GET","path":"/api/cart","status":502,"upstream":"checkout-svc:3000","response_flags":"UC","duration_ms":2,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
{t-29.5} {"ts":"{t-29.5}","method":"GET","path":"/api/orders/ord_71ka2","status":200,"upstream":"orders-svc:8080","duration_ms":48}
{t-22.1} {"ts":"{t-22.1}","method":"POST","path":"/api/checkout/session","status":502,"upstream":"checkout-svc:3000","response_flags":"UC","duration_ms":4,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
{t-15.0} WARN  upstream cluster checkout-svc: 1,284 upstream_cx_destroy_remote_with_active_rq in last 60s
""",
        triage="On-call acknowledged. Upstreams report healthy; 502s are fast (2-4ms) with response flag UC, so the gateway is reusing dead connections.",
        failed_attempts=[
            "Restarted api-gateway pods (502s came back as soon as upstream connection pools refilled)",
            "Scaled orders-svc and checkout-svc up (no effect; the upstreams were healthy)",
        ],
        root_cause=(
            "Config change in release v2.41.0 raised the gateway's upstream idle_timeout to 120s while the Node "
            "upstreams close keep-alive connections after 5s (keepAliveTimeout), so the gateway reused connections "
            "the upstream had already closed and returned 502 UC"
        ),
        resolution=(
            "Rolled back api-gateway to v2.40.3; set upstream idle_timeout to 4s (below the services' 5s "
            "keepAliveTimeout) and redeployed on Monday morning"
        ),
        notes="Deployed at 17:16 on a Friday.",
    ),
    # INC-1003 ----------------------------------------------------------------
    dict(
        pattern="orders_conn",
        title="orders-db: FATAL too many clients, order writes failing",
        service="orders-db",
        severity="SEV2",
        created="2026-05-26T09:12:00",
        ttr=95,
        alert_text=(
            "[FIRING] PostgresConnectionsNearLimit: orders-db 198/200 connections (99%). orders-api error rate "
            "17% (could not connect to server)."
        ),
        logs="""
{pg-75.4} [41822] etl@orders FATAL:  sorry, too many clients already
{pg-75.1} [41823] orders_api@orders FATAL:  remaining connection slots are reserved for non-replication superuser connections
{pg-70.9} [41830] orders_api@orders FATAL:  sorry, too many clients already
{t-64.0} ERROR orders-api psycopg2.OperationalError: connection to server at "orders-db.internal" (10.20.4.11), port 5432 failed: FATAL:  sorry, too many clients already
Traceback (most recent call last):
  File "/app/orders/db.py", line 41, in get_conn
    return pool.getconn()
  File "/usr/local/lib/python3.11/site-packages/psycopg2/pool.py", line 169, in getconn
    return self._getconn(key)
psycopg2.OperationalError: FATAL:  sorry, too many clients already
{t-20.0} INFO  pg_stat_activity: usename=etl count=64 | usename=orders_api count=120 | usename=readonly count=12
""",
        triage="On-call acknowledged. pg_stat_activity shows 64 connections from the etl role, which normally uses 4.",
        failed_attempts=[
            "Tried raising max_connections from 200 to 400 (needs a Postgres restart and the instance lacked memory headroom; abandoned)",
        ],
        root_cause=(
            "New orders-etl analytics job opened one Postgres connection per worker thread (64 threads) without "
            "pooling, pushing orders-db past max_connections=200 together with orders-api's pools"
        ),
        resolution=(
            "Terminated the orders-etl connections with pg_terminate_backend; routed orders-etl through PgBouncer "
            "in transaction mode with a pool of 10; set ALTER ROLE etl CONNECTION LIMIT 15"
        ),
        notes=None,
    ),
    # INC-1004 ----------------------------------------------------------------
    dict(
        pattern="tls_cert",
        title="api-gateway: public TLS certificate expired, all HTTPS traffic failing",
        service="api-gateway",
        severity="SEV1",
        created="2026-05-29T00:02:00",
        ttr=50,
        alert_text=(
            "[FIRING] SyntheticCheckFailed: https://api.aftermath.shop/health failing from 5/5 regions: "
            "x509: certificate has expired or is not yet valid."
        ),
        logs="""
{t-90} ERROR synthetic-monitor region=us-east-1 GET https://api.aftermath.shop/health: x509: certificate has expired or is not yet valid: current time {t-90} is after 2026-05-29T00:00:00Z
{t-88} ERROR synthetic-monitor region=eu-west-1 GET https://api.aftermath.shop/health: x509: certificate has expired or is not yet valid
{t-60} WARN  cert-manager Certificate default/wildcard-aftermath-shop: Issuing certificate as Secret does not exist
{t-59} ERROR cert-manager challenges "wildcard-aftermath-shop-2841" error="DNS-01 present: 403 Forbidden: invalid API token" (retrying since 2026-05-07)
""",
        triage="On-call acknowledged. Certificate *.aftermath.shop expired at 00:00 UTC; cert-manager has been failing renewals.",
        failed_attempts=["Restarted cert-manager (renewal still failed with 403 from the DNS provider API)"],
        root_cause=(
            "The wildcard certificate *.aftermath.shop on the edge load balancer expired because cert-manager "
            "renewals had been failing silently for 3 weeks: the DNS-01 solver's API token had been revoked"
        ),
        resolution=(
            "Manually issued and uploaded a new certificate to the load balancer; rotated the DNS provider API token "
            "used by cert-manager; added an alert for certificates expiring within 14 days"
        ),
        notes=None,
    ),
    # INC-1005 ----------------------------------------------------------------
    dict(
        pattern="notif_oom",
        title="notification-worker: OOMKilled during weekly digest batch, emails delayed",
        service="notification-worker",
        severity="SEV3",
        created="2026-06-09T02:10:00",
        ttr=175,
        alert_text=(
            "[FIRING] KubePodCrashLooping: notification-worker-7c9f8d-xk2lp restarted 6 times in 15m "
            "(last state: OOMKilled). email_queue depth 184,212."
        ),
        logs="""
{t-420} INFO  notification_worker.jobs.weekly_digest: starting batch run_id=wd-2026-06-09
{t-395} INFO  notification_worker.jobs.weekly_digest: loaded 1,240,331 recipients
{t-301} INFO  notification_worker.render: rendered 400000/1240331 digests (rss=498Mi)
$ kubectl -n notifications describe pod notification-worker-7c9f8d-xk2lp
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
      Started:      {t-410}
      Finished:     {t-298}
    Restart Count:  6
    Limits:
      memory:  512Mi
{t-12} WARN  celery.worker: Worker exited prematurely: signal 9 (SIGKILL) Job: 1408. exit code 137
""",
        triage="On-call acknowledged. Pods are killed at the 512Mi limit only while the weekly_digest batch runs.",
        failed_attempts=[
            "Restarted the notification-worker deployment (OOMKilled again as soon as the batch was picked up)",
            "Scaled notification-worker from 2 to 6 replicas (each replica picked up the same batch and was OOMKilled)",
        ],
        root_cause=(
            "The weekly digest batch job loaded all 1.24M recipients and rendered every email in memory at once; "
            "worker memory grew past the 512Mi container limit"
        ),
        resolution=(
            "Raised the notification-worker memory limit from 512Mi to 1Gi as a stopgap; changed weekly_digest to "
            "stream recipients in chunks of 5,000 with a server-side cursor; deployed notification-worker v1.12.0"
        ),
        notes=None,
    ),
    # INC-1006 ----------------------------------------------------------------
    dict(
        pattern="jwks_rotation",
        title="auth-service: 401 spike, tokens rejected after signing key rotation",
        service="auth-service",
        severity="SEV1",
        created="2026-06-17T08:30:00",
        ttr=115,
        alert_text=(
            "[FIRING] AuthFailuresHigh: 401 rate 63% across api services (threshold 5%). "
            "Login succeeds but subsequent API calls are rejected."
        ),
        logs="""
{t-300} INFO  auth-service keyrotation: activated signing key kid="2026-06-k2" (previous kid="2026-03-k1")
{t-118} {"level":"error","ts":"{t-118}","service":"orders-svc","msg":"jwt verification failed","error":"no matching key found in JWKS for kid \\"2026-06-k2\\"","path":"/api/orders"}
{t-117} {"level":"error","ts":"{t-117}","service":"checkout-svc","msg":"jwt verification failed","error":"no matching key found in JWKS for kid \\"2026-06-k2\\"","path":"/api/cart"}
{t-60} {"level":"info","ts":"{t-60}","service":"orders-svc","msg":"jwks cache","keys":["2026-03-k1"],"fetched_at":"2026-06-16T09:14:02Z","ttl":"24h"}
""",
        triage="On-call acknowledged. All rejected tokens carry the new kid 2026-06-k2; consumers' JWKS cache only has the old key.",
        failed_attempts=[
            "Restarted auth-service pods (the stale JWKS cache was in the consuming services, not auth-service)",
            "Rolled back the latest auth-service deploy (unrelated to the key rotation; no effect)",
        ],
        root_cause=(
            "Scheduled signing-key rotation started signing tokens with the new key immediately after publishing it, "
            "but API services cache the JWKS for 24h, so tokens with the new kid were rejected"
        ),
        resolution=(
            "Switched signing back to the previous key (kid 2026-03-k1); forced a JWKS cache refresh on all "
            "services; changed key rotation to publish new keys 48h before using them for signing"
        ),
        notes=None,
    ),
    # INC-1007 ----------------------------------------------------------------
    dict(
        pattern="stale_chunks",
        title="checkout-web: ChunkLoadError after deploy, checkout page blank for some users",
        service="checkout-web",
        severity="SEV2",
        created="2026-06-23T19:10:00",
        ttr=70,
        alert_text=(
            "[FIRING] FrontendErrorSpike: ChunkLoadError 2,310 events/5m on checkout-web (baseline < 10). "
            "Started right after deploy checkout-web release 2026.06.23-2."
        ),
        logs="""
{t-280} INFO  deploy checkout-web release 2026.06.23-2 uploaded to s3://aftermath-web-assets (removed 412 stale files)
{t-140} ERROR [sentry] ChunkLoadError: Loading chunk 812 failed.
(error: https://cdn.aftermath.shop/_next/static/chunks/812.4f1c2ab9e.js)
    at __webpack_require__.f.j (webpack-runtime-3d1c.js:1:2817)
    at /checkout (pages/checkout-9a1e.js:1:118)
{t-139} WARN  cdn-edge GET /_next/static/chunks/812.4f1c2ab9e.js 404 cache=MISS pop=FRA
{t-130} WARN  cdn-edge GET /checkout 200 cache=HIT age=412 pop=FRA
""",
        triage="On-call acknowledged. Cached HTML (age ~7 min) references chunk hashes from the previous build that no longer exist in the bucket.",
        failed_attempts=[
            "Rolled back checkout-web to the previous release (the rollback deleted the new build's chunks, so users on the new HTML broke instead)",
        ],
        root_cause=(
            "checkout-web deploy deleted the previous build's static chunks from the bucket while the CDN still served "
            "cached HTML (10 minute TTL) that referenced them, so clients requested chunks that no longer existed"
        ),
        resolution=(
            "Restored the previous build's /_next/static assets to the bucket; purged the CDN cache for HTML; changed "
            "the deploy to keep the last 3 builds' static assets and set Cache-Control: no-cache on HTML"
        ),
        notes=None,
    ),
    # INC-1008 ----------------------------------------------------------------
    dict(
        pattern="orders_lock",
        title="orders-db: lock timeouts on orders table during migration, order placement failing",
        service="orders-db",
        severity="SEV2",
        created="2026-06-30T13:40:00",
        ttr=110,
        alert_text=(
            "[FIRING] OrdersApiErrorRate: 34% of POST /orders failing with 'canceling statement due to lock timeout'. "
            "Started during deploy of orders-api 4.12.0 (runs migrations)."
        ),
        logs="""
{pg-190.2} [5121] migrator@orders LOG:  statement: ALTER TABLE orders ALTER COLUMN customer_id TYPE bigint
{pg-95.7} [6611] orders_api@orders LOG:  process 6611 still waiting for RowExclusiveLock on relation 16420 of database 16385 after 1000.112 ms
{pg-95.7} [6611] orders_api@orders DETAIL:  Process holding the lock: 5121. Wait queue: 6611, 6612, 6615, 6620.
{pg-94.7} [6611] orders_api@orders ERROR:  canceling statement due to lock timeout
{pg-94.7} [6611] orders_api@orders STATEMENT:  INSERT INTO orders (id, customer_id, status, total_cents) VALUES ($1, $2, 'pending', $3)
{t-40} INFO  pg_locks: pid=5121 mode=AccessExclusiveLock relation=orders granted=t waiting_pids=212
""",
        triage="On-call acknowledged. Migration pid 5121 holds AccessExclusiveLock on orders; a type change rewrites the 38M-row table.",
        failed_attempts=[
            "Waited for the migration to finish (the rewrite was only ~40% done after 30 minutes)",
            "Restarted orders-api pods (new queries still blocked behind the lock)",
        ],
        root_cause=(
            "The deploy ran migration 0142 (ALTER COLUMN customer_id TYPE bigint), which rewrites the 38M-row orders "
            "table while holding an AccessExclusiveLock; all order writes queued behind it and hit lock_timeout"
        ),
        resolution=(
            "Cancelled the migration with pg_cancel_backend(5121); rescheduled it as an online change (new bigint "
            "column, batched backfill, swap); set lock_timeout=5s for the migrator role"
        ),
        notes=None,
    ),
    # INC-1009 ----------------------------------------------------------------
    dict(
        pattern="payments_pool",
        title="payments-api: connection pool exhausted during payout export",
        service="payments-api",
        severity="SEV1",
        created="2026-07-14T11:40:00",
        ttr=68,
        alert_text=(
            "[FIRING] PaymentsApiHighErrorRate: 5xx rate 33.9% on payments-api (threshold 5%) for 5m. "
            "p99 latency 30.1s. HikariPool-1 active=40/40, pending=241."
        ),
        logs="""
{t-80.1} ERROR [http-nio-8080-exec-9] c.a.payments.api.ChargeController - POST /v1/charges failed (order_id=ord_9ak21m)
org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)
    at com.aftermath.payments.service.ChargeService.charge(ChargeService.java:131)
{t-66.0} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.ProxyLeakTask - Connection leak detection triggered for org.postgresql.jdbc.PgConnection@6b1f0e2c on thread payout-export-1, stack trace follows
java.lang.Exception: Apparent connection leak detected
    at com.aftermath.payments.payout.PayoutExportJob.exportBatch(PayoutExportJob.java:77)
{t-60.2} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Pool stats (total=40, active=40, idle=0, waiting=241)
""",
        triage="On-call acknowledged. Leak detection (added after INC-1001) points at PayoutExportJob.exportBatch.",
        failed_attempts=[
            "Raised maximumPoolSize from 40 to 60 without fixing the leak (pool drained again within 20 minutes)",
        ],
        root_cause=(
            "PayoutExportJob held a DB connection and an open transaction while uploading each export file to S3 "
            "(20-40s per file), so concurrent exports pinned most of the 40-connection HikariCP pool"
        ),
        resolution=(
            "Moved the S3 upload in PayoutExportJob outside the DB transaction so the connection is released "
            "immediately; kept maximumPoolSize at 40 and leakDetectionThreshold on; deployed payments-api v3.24.1"
        ),
        notes="Agent recalled INC-1001, which pointed straight at leak detection logs and warned that restarts don't help.",
    ),
    # INC-1010 ----------------------------------------------------------------
    dict(
        pattern="checkout_cascade",
        title="checkout-web: 5xx on payment step, payments-api timing out",
        service="checkout-web",
        severity="SEV1",
        created="2026-07-14T11:46:00",
        ttr=55,
        alert_text=(
            "[FIRING] CheckoutWeb5xxRate: 5xx rate 27% on POST /api/checkout/pay (threshold 2%). "
            "Upstream payments-api p99 30s."
        ),
        logs="""
{t-45.2} ERROR checkout-web [api/checkout/pay] PaymentsClientError: payments-api POST /v1/charges responded 503 after 30012ms: {"error":"CannotGetJdbcConnectionException","message":"HikariPool-1 - Connection is not available, request timed out after 30000ms."}
    at PaymentsClient.charge (/app/.next/server/chunks/payments-client.js:88:15)
    at async handler (/app/.next/server/pages/api/checkout/pay.js:41:22)
{t-44.0} ERROR checkout-web [api/checkout/pay] upstream timed out (110: Connection timed out) while reading response header from upstream payments-api
{t-30.0} WARN  checkout-web event loop lag 2,840ms; 1,912 in-flight requests to payments-api
""",
        triage="On-call acknowledged. Errors all originate from payments-api calls; payments-api has its own open SEV1 (INC-1009).",
        failed_attempts=[
            "Scaled checkout-web from 8 to 16 pods (sent even more concurrent requests to the struggling payments-api)",
        ],
        root_cause=(
            "Downstream failure: payments-api connection pool exhaustion (INC-1009) made /v1/charges hang for 30s; "
            "checkout-web had no timeout or circuit breaker on its payments client, so requests piled up and failed"
        ),
        resolution=(
            "Resolved together with the INC-1009 fix in payments-api; added a 5s timeout and circuit breaker to "
            "checkout-web's payments client; show a 'payment pending, retry' message instead of a 500"
        ),
        notes=None,
        extra_memories=[("INC-1001", 0.74)],
    ),
    # INC-1011 ----------------------------------------------------------------
    dict(
        pattern="gateway_502",
        title="api-gateway: 502s after Friday deploy of v2.47.0",
        service="api-gateway",
        severity="SEV2",
        created="2026-07-24T16:50:00",
        ttr=52,
        alert_text=(
            "[FIRING] ApiGateway5xxRate: 502 rate 14.9% on api-gateway (threshold 2%). Started 3 minutes after "
            "deploy of api-gateway release v2.47.0."
        ),
        logs="""
{t-180} INFO  deploy api-gateway release v2.47.0 rolled out (3/3 pods ready) by ci-bot, change: "migrate to shared helm chart gateway-base 1.4"
{t-40.4} {"ts":"{t-40.4}","method":"POST","path":"/api/orders","status":502,"upstream":"orders-svc:8080","response_flags":"UC","duration_ms":2,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
{t-39.9} {"ts":"{t-39.9}","method":"GET","path":"/api/catalog/items","status":502,"upstream":"catalog-svc:3000","response_flags":"UC","duration_ms":3,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
{t-10.0} WARN  rendered config diff v2.46.1 -> v2.47.0: clusters.*.common_http_protocol_options.idle_timeout: 4s -> 300s (chart default)
""",
        triage="On-call acknowledged. Same 502 UC signature as INC-1002; checking the deploy's rendered config.",
        failed_attempts=[
            "Drained gateway connection pools via the admin endpoint (502s returned within 2 minutes)",
        ],
        root_cause=(
            "Config change: moving to the shared helm chart in v2.47.0 reset the upstream idle_timeout from 4s to the "
            "chart default of 300s, longer than the upstreams' 5s keep-alive, reintroducing the INC-1002 failure"
        ),
        resolution=(
            "Rolled back api-gateway to v2.46.1; pinned upstream idle_timeout=4s in values-prod.yaml so chart defaults "
            "can't override it"
        ),
        notes="Recognized the INC-1002 pattern via memory; went to rollback first.",
    ),
    # INC-1012 ----------------------------------------------------------------
    dict(
        pattern="notif_oom",
        title="notification-worker: OOMKilled running new invoice reminder batch",
        service="notification-worker",
        severity="SEV3",
        created="2026-08-11T02:05:00",
        ttr=80,
        alert_text=(
            "[FIRING] KubePodCrashLooping: notification-worker-5d8b6c-q7w2n restarted 4 times in 15m "
            "(last state: OOMKilled). email_queue depth 96,410."
        ),
        logs="""
{t-500} INFO  notification_worker.jobs.invoice_reminder: starting batch run_id=ir-2026-08 accounts=310,488
{t-430} INFO  notification_worker.pdf: generated 120000/310488 invoice PDFs (rss=1.71Gi)
$ kubectl -n notifications describe pod notification-worker-5d8b6c-q7w2n
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
    Restart Count:  4
    Limits:
      memory:  1Gi
{t-20} WARN  celery.worker: Worker exited prematurely: signal 9 (SIGKILL) Job: 2210. exit code 137
""",
        triage="On-call acknowledged. Same OOMKilled-in-batch signature as INC-1005, but in the new invoice_reminder job.",
        failed_attempts=["Raised the memory limit from 1Gi to 2Gi (still OOMKilled at ~1.9Gi)"],
        root_cause=(
            "The new monthly invoice-reminder batch built PDF attachments for all 310k accounts in memory, bypassing "
            "the chunked recipient loader added after INC-1005"
        ),
        resolution=(
            "Paused the invoice-reminder CronJob; moved it onto the chunked recipient loader (5,000 per chunk) "
            "generating PDFs per chunk; resumed the job"
        ),
        notes=None,
    ),
    # INC-1013 ----------------------------------------------------------------
    dict(
        pattern="auth_redis",
        title="auth-service: session lookups timing out, Redis blocked",
        service="auth-service",
        severity="SEV2",
        created="2026-08-19T15:45:00",
        ttr=52,
        alert_text=(
            "[FIRING] AuthLatencyHigh: auth-service p99 2.8s (threshold 300ms); 9% of session validations "
            "failing with Redis timeouts."
        ),
        logs="""
{t-75} ERROR auth-service session.validate failed
Traceback (most recent call last):
  File "/app/auth/session.py", line 63, in validate
    raw = redis_client.get(f"session:{sid}")
  File "/usr/local/lib/python3.11/site-packages/redis/client.py", line 1269, in get
    return self.execute_command("GET", name)
redis.exceptions.TimeoutError: Timeout reading from socket
{t-60} WARN  redis slowlog: id=88121 duration_us=2310442 cmd="KEYS session:*" client=10.20.7.33:51122 (auth-admin)
{t-30} WARN  redis slowlog: id=88130 duration_us=1987310 cmd="KEYS session:*" client=10.20.7.33:51188 (auth-admin)
""",
        triage="On-call acknowledged. Redis slowlog shows KEYS session:* taking ~2s, from the auth-admin pod.",
        failed_attempts=[
            "Failed over to the Redis replica (it was blocked the same way as soon as the endpoint was hit again)",
        ],
        root_cause=(
            "A new admin 'active sessions' endpoint ran KEYS session:* against the 4.1M-key session Redis, blocking "
            "the single-threaded server for ~2s per call"
        ),
        resolution=(
            "Disabled the admin endpoint with its feature flag; replaced KEYS with SCAN-based iteration; renamed the "
            "KEYS command in the Redis parameter group so it can't be used in production"
        ),
        notes=None,
    ),
    # INC-1014 ----------------------------------------------------------------
    dict(
        pattern="stripe_webhook",
        title="payments-api: Stripe webhook signature verification failing",
        service="payments-api",
        severity="SEV2",
        created="2026-08-27T10:15:00",
        ttr=35,
        alert_text=(
            "[FIRING] WebhookFailures: 100% of POST /webhooks/stripe returning 400 for 15m. "
            "1,840 payment confirmations pending."
        ),
        logs="""
{t-54.3} WARN  [http-nio-8080-exec-3] c.a.payments.webhook.StripeWebhookController - Rejected webhook evt_1PqX92 (payment_intent.succeeded)
com.stripe.exception.SignatureVerificationException: No signatures found matching the expected signature for payload
    at com.stripe.net.Webhook$Signature.verifyHeader(Webhook.java:178)
    at com.aftermath.payments.webhook.StripeWebhookController.handle(StripeWebhookController.java:44)
{t-12.0} WARN  [http-nio-8080-exec-7] c.a.payments.webhook.StripeWebhookController - Rejected webhook evt_1PqXa1 (charge.refunded): No signatures found matching the expected signature for payload
""",
        triage="On-call acknowledged. Every webhook fails signature verification; no payments-api deploy in the last 24h.",
        failed_attempts=["Redeployed payments-api with the same config (secret unchanged; still failing)"],
        root_cause=(
            "The Stripe webhook signing secret was rolled in the Stripe dashboard during an account settings cleanup, "
            "but STRIPE_WEBHOOK_SECRET in payments-api was not updated"
        ),
        resolution=(
            "Updated STRIPE_WEBHOOK_SECRET in the secrets manager and restarted payments-api; replayed failed webhook "
            "events from the last 3 hours via the Stripe dashboard"
        ),
        notes=None,
    ),
    # INC-1015 ----------------------------------------------------------------
    dict(
        pattern="orders_lock",
        title="orders-db: writes blocked by index migration on orders",
        service="orders-db",
        severity="SEV2",
        created="2026-09-08T10:20:00",
        ttr=35,
        alert_text=(
            "[FIRING] OrdersApiErrorRate: 22% of POST /orders failing with 'canceling statement due to lock timeout'. "
            "Started during deploy of orders-api 4.19.0 (runs migrations)."
        ),
        logs="""
{pg-140.0} [8802] migrator@orders LOG:  statement: CREATE INDEX idx_orders_created_at ON orders (created_at)
{pg-70.2} [9120] orders_api@orders LOG:  process 9120 still waiting for RowExclusiveLock on relation 16420 of database 16385 after 1000.087 ms
{pg-70.2} [9120] orders_api@orders DETAIL:  Process holding the lock: 8802. Wait queue: 9120, 9124, 9131.
{pg-69.2} [9120] orders_api@orders ERROR:  canceling statement due to lock timeout
{pg-69.2} [9120] orders_api@orders STATEMENT:  UPDATE orders SET status = 'paid' WHERE id = $1
""",
        triage="On-call acknowledged. Same lock-timeout signature as INC-1008; migration pid 8802 is building an index without CONCURRENTLY.",
        failed_attempts=[],
        root_cause=(
            "Migration 0167 created an index on orders(created_at) without CONCURRENTLY, which blocks all writes to "
            "the orders table for the duration of the build"
        ),
        resolution=(
            "Cancelled the migration with pg_cancel_backend(8802); re-ran it as CREATE INDEX CONCURRENTLY outside a "
            "transaction; added a migration linter rule rejecting non-concurrent index creation on large tables"
        ),
        notes="Memory of INC-1008 meant we cancelled the migration immediately instead of waiting.",
    ),
    # INC-1016 ----------------------------------------------------------------
    dict(
        pattern="jwks_rotation",
        title="auth-service: tokens rejected after key rotation in new cluster",
        service="auth-service",
        severity="SEV1",
        created="2026-09-16T08:30:00",
        ttr=22,
        alert_text=(
            "[FIRING] AuthFailuresHigh: 401 rate 48% across api services in cluster prod-eu-2 (threshold 5%)."
        ),
        logs="""
{t-240} INFO  auth-service keyrotation: activated signing key kid="2026-09-k3" (previous kid="2026-06-k2") prepublish_window=0s
{t-90} {"level":"error","ts":"{t-90}","service":"orders-svc","cluster":"prod-eu-2","msg":"jwt verification failed","error":"no matching key found in JWKS for kid \\"2026-09-k3\\""}
{t-88} {"level":"error","ts":"{t-88}","service":"catalog-svc","cluster":"prod-eu-2","msg":"jwt verification failed","error":"no matching key found in JWKS for kid \\"2026-09-k3\\""}
""",
        triage="On-call acknowledged. Same 'no matching key in JWKS' signature as INC-1006; prepublish_window=0s in the new cluster.",
        failed_attempts=[],
        root_cause=(
            "The key-rotation CronJob in the new prod-eu-2 cluster was deployed without the 48h pre-publish window "
            "added after INC-1006, so it signed with a key consumers hadn't cached yet"
        ),
        resolution=(
            "Switched signing back to the previous kid 2026-06-k2; purged JWKS caches; fixed the prod-eu-2 rotation "
            "CronJob to use the 48h pre-publish window"
        ),
        notes=None,
    ),
    # INC-1017 ----------------------------------------------------------------
    dict(
        pattern="payments_pool",
        title="payments-api: connection pool exhausted after v3.31.0 deploy",
        service="payments-api",
        severity="SEV1",
        created="2026-09-29T14:20:00",
        ttr=24,
        alert_text=(
            "[FIRING] PaymentsApiHighErrorRate: 5xx rate 29.4% on payments-api (threshold 5%) for 5m. "
            "p99 latency 30.0s. HikariPool-1 active=40/40, pending=166."
        ),
        logs="""
{t-48.0} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.ProxyLeakTask - Connection leak detection triggered for org.postgresql.jdbc.PgConnection@1d7e44a9 on thread refund-reconciler-2, stack trace follows
java.lang.Exception: Apparent connection leak detected
    at com.aftermath.payments.refund.RefundRetryHandler.retry(RefundRetryHandler.java:58)
{t-30.5} ERROR [http-nio-8080-exec-21] c.a.payments.api.ChargeController - POST /v1/charges failed (order_id=ord_2mm81z)
java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)
{t-29.9} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Pool stats (total=40, active=40, idle=0, waiting=166)
""",
        triage="On-call acknowledged. Agent matched INC-1001/INC-1009; leak detection points at the new RefundRetryHandler from v3.31.0.",
        failed_attempts=[],
        root_cause=(
            "payments-api v3.31.0 reintroduced a manual getConnection() without close() in the new "
            "RefundRetryHandler, leaking connections on every refund retry (same class of bug as INC-1001)"
        ),
        resolution=(
            "Rolled back payments-api to v3.30.4; fixed RefundRetryHandler to use try-with-resources; added a test "
            "asserting the pool has zero active connections after the refund retry path"
        ),
        notes="Skipped restarts and pool-size bumps because memory showed they never helped.",
    ),
    # INC-1018 ----------------------------------------------------------------
    dict(
        pattern="notif_oom",
        title="notification-worker: OOMKilled in weekly digest after config change",
        service="notification-worker",
        severity="SEV3",
        created="2026-10-06T02:15:00",
        ttr=30,
        alert_text=(
            "[FIRING] KubePodCrashLooping: notification-worker-6f4d7b-m2k9c restarted 3 times in 15m "
            "(last state: OOMKilled). email_queue depth 61,035."
        ),
        logs="""
{t-300} INFO  notification_worker.jobs.weekly_digest: starting batch run_id=wd-2026-10-06 chunk_size=100000
{t-250} INFO  notification_worker.render: rendered chunk 1/13 (100000 digests, rss=982Mi)
$ kubectl -n notifications describe pod notification-worker-6f4d7b-m2k9c
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
    Restart Count:  3
    Limits:
      memory:  1Gi
""",
        triage="On-call acknowledged. Same OOMKilled-in-batch signature as INC-1005/INC-1012; chunk_size is 100000 instead of 5000.",
        failed_attempts=[],
        root_cause=(
            "A ConfigMap change set DIGEST_CHUNK_SIZE=100000 (meant for staging), so each digest chunk no longer fit "
            "in the 1Gi memory limit"
        ),
        resolution=(
            "Reset DIGEST_CHUNK_SIZE to 5000 in the notification-worker ConfigMap and restarted the job; added "
            "validation capping chunk size at 10000"
        ),
        notes=None,
    ),
    # INC-1019 ----------------------------------------------------------------
    dict(
        pattern="stale_chunks",
        title="checkout-web: ChunkLoadError after deploy on new CDN",
        service="checkout-web",
        severity="SEV3",
        created="2026-10-14T19:30:00",
        ttr=15,
        alert_text=(
            "[FIRING] FrontendErrorSpike: ChunkLoadError 840 events/5m on checkout-web (baseline < 10). "
            "Started right after deploy checkout-web release 2026.10.14-1."
        ),
        logs="""
{t-200} INFO  deploy checkout-web release 2026.10.14-1 uploaded to s3://aftermath-web-assets (kept last 3 builds)
{t-95} ERROR [sentry] ChunkLoadError: Loading chunk 377 failed.
(error: https://cdn2.aftermath.shop/_next/static/chunks/377.c81d0e7a.js)
{t-94} WARN  cdn2-edge GET /checkout 200 cache=HIT age=1840 pop=AMS cache-control-override="public, max-age=3600"
""",
        triage="On-call acknowledged. Same ChunkLoadError signature as INC-1007; the new CDN is caching HTML for 1h, ignoring no-cache.",
        failed_attempts=[],
        root_cause=(
            "After migrating to the new CDN, a default rule overrode Cache-Control on HTML to max-age=3600, so stale "
            "HTML referenced chunk hashes from older builds"
        ),
        resolution=(
            "Purged the HTML cache on the new CDN; added a CDN rule honoring Cache-Control: no-cache for HTML routes"
        ),
        notes=None,
    ),
    # INC-1020 ----------------------------------------------------------------
    dict(
        pattern="gateway_502",
        title="api-gateway: 502 spike after Friday deploy of v2.53.0",
        service="api-gateway",
        severity="SEV2",
        created="2026-10-23T17:05:00",
        ttr=18,
        alert_text=(
            "[FIRING] ApiGateway5xxRate: 502 rate 18.2% on api-gateway (threshold 2%). Started 2 minutes after "
            "deploy of api-gateway release v2.53.0."
        ),
        logs="""
{t-120} INFO  deploy api-gateway release v2.53.0 rolled out (3/3 pods ready) by ci-bot, change: "routes: add /api/returns, refactor upstream defaults"
{t-50.1} {"ts":"{t-50.1}","method":"GET","path":"/api/returns","status":502,"upstream":"returns-svc:3000","response_flags":"UC","duration_ms":2,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
{t-49.7} {"ts":"{t-49.7}","method":"POST","path":"/api/orders","status":502,"upstream":"orders-svc:8080","response_flags":"UC","duration_ms":3,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
""",
        triage="On-call acknowledged. Agent matched INC-1002/INC-1011 (502 UC after Friday deploy); rolling back first.",
        failed_attempts=[],
        root_cause=(
            "Config change: the v2.53.0 refactor moved upstream defaults into a new shared block that dropped the "
            "pinned idle_timeout=4s, so the gateway again outlived the upstreams' 5s keep-alive"
        ),
        resolution=(
            "Rolled back api-gateway to v2.52.2 within 10 minutes; restored idle_timeout=4s; added a CI check that "
            "fails if any upstream idle_timeout is >= 5s"
        ),
        notes="Third time. Proposed a freeze on gateway deploys after 15:00 on Fridays.",
    ),
]

# Relevance of the k-th most recent earlier incident with the same pattern.
SAME_PATTERN_RELEVANCE = [0.93, 0.88, 0.84]

DEMO_ALERTS = [
    {
        "_demo_note": "Hero demo: 4th occurrence of the payments pool pattern. Agent should recall INC-1017/1009/1001.",
        "title": "payments-api: connection pool exhausted, checkout payments failing",
        "service": "payments-api",
        "severity": "SEV1",
        "alert_text": (
            "[FIRING] PaymentsApiHighErrorRate: 5xx rate 38.2% on payments-api (threshold 5%) for 5m. "
            "p99 latency 30.4s. HikariPool-1 active=40/40, pending=212."
        ),
        "logs": """
{t-42.7} ERROR [http-nio-8080-exec-41] c.a.payments.api.ChargeController - POST /v1/charges failed (order_id=ord_k21x0p)
org.springframework.jdbc.CannotGetJdbcConnectionException: Failed to obtain JDBC Connection; nested exception is java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:696)
    at com.aftermath.payments.service.ChargeService.charge(ChargeService.java:131)
{t-30.1} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.ProxyLeakTask - Connection leak detection triggered for org.postgresql.jdbc.PgConnection@4ac2d1f0 on thread chargeback-sync-4, stack trace follows
java.lang.Exception: Apparent connection leak detected
    at com.aftermath.payments.chargeback.ChargebackSyncJob.sync(ChargebackSyncJob.java:92)
{t-29.8} WARN  [HikariPool-1 housekeeper] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Pool stats (total=40, active=40, idle=0, waiting=212)
""",
    },
    {
        "_demo_note": "Friday-deploy 502s again. Agent should recall INC-1020/1011/1002 and say: roll back first.",
        "title": "api-gateway: 502 Bad Gateway after deploy of v2.58.0",
        "service": "api-gateway",
        "severity": "SEV2",
        "alert_text": (
            "[FIRING] ApiGateway5xxRate: 502 rate 16.0% on api-gateway (threshold 2%). Started 3 minutes after "
            "deploy of api-gateway release v2.58.0."
        ),
        "logs": """
{t-180} INFO  deploy api-gateway release v2.58.0 rolled out (3/3 pods ready) by ci-bot, change: "bump gateway-base chart to 2.0"
{t-35.2} {"ts":"{t-35.2}","method":"POST","path":"/api/orders","status":502,"upstream":"orders-svc:8080","response_flags":"UC","duration_ms":2,"body":"upstream connect error or disconnect/reset before headers. reset reason: connection termination"}
""",
    },
    {
        "_demo_note": "Batch OOM. Agent should recall INC-1018/1012/1005.",
        "title": "notification-worker: OOMKilled during monthly statement batch",
        "service": "notification-worker",
        "severity": "SEV3",
        "alert_text": (
            "[FIRING] KubePodCrashLooping: notification-worker-8a1c2e-z9p4t restarted 5 times in 15m "
            "(last state: OOMKilled). email_queue depth 142,880."
        ),
        "logs": """
{t-360} INFO  notification_worker.jobs.monthly_statement: starting batch run_id=ms-2026-11 accounts=402,113
$ kubectl -n notifications describe pod notification-worker-8a1c2e-z9p4t
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
    Limits:
      memory:  1Gi
""",
    },
    {
        "_demo_note": "Novel incident: nothing in memory matches. Shows the low-confidence path.",
        "title": "search-api: Elasticsearch cluster red, search returning empty results",
        "service": "search-api",
        "severity": "SEV2",
        "alert_text": "[FIRING] ElasticsearchClusterRed: cluster search-prod status RED, 14 unassigned primary shards.",
        "logs": """
{t-60} WARN  search-api SearchPhaseExecutionException: all shards failed; index [products-v7] primary shard [3] is not active
{t-40} WARN  es-node-2 high disk watermark [90%] exceeded on [es-node-2][/usr/share/elasticsearch/data] free: 9.1gb[8.9%], shards will be relocated away from this node
""",
    },
]


def build() -> tuple[dict, dict]:
    incidents = []
    by_pattern: dict[str, list[dict]] = {}
    for n, spec in enumerate(INCIDENTS, start=1001):
        created = utc(spec["created"])
        if spec["pattern"] == "gateway_502":
            assert created.weekday() == 4, f"INC-{n} should be a Friday deploy"
        ttr = spec["ttr"]
        resolved = created + timedelta(minutes=ttr)
        incident_id = f"INC-{n}"

        # Memories the agent used for this incident: earlier incidents with the same pattern (newest first).
        memories = []
        for k, past in enumerate(reversed(by_pattern.get(spec["pattern"], []))):
            memories.append((past["id"], SAME_PATTERN_RELEVANCE[min(k, 2)]))
        memories += spec.get("extra_memories", [])
        lookup = {i["id"]: i for i in incidents}
        memories_used = [
            {
                "past_incident_id": pid,
                "summary": f"Root cause: {lookup[pid]['root_cause']}. Fix: {lookup[pid]['resolution']}.",
                "relevance_score": score,
                "used_at": iso(created + timedelta(minutes=2)),
            }
            for pid, score in memories
        ]

        timeline = [
            {"timestamp": iso(created), "type": "alert", "message": f"[{spec['severity']}] Alert fired: {spec['alert_text']}"},
        ]
        if memories_used:
            recalled = ", ".join(m["past_incident_id"] for m in memories_used)
            timeline.append(
                {
                    "timestamp": iso(created + timedelta(minutes=2)),
                    "type": "analysis",
                    "message": f"Agent recalled {recalled}. Suggested root cause: {lookup[memories[0][0]]['root_cause']}",
                }
            )
        timeline.append({"timestamp": iso(created + timedelta(minutes=min(4, ttr // 4))), "type": "analysis", "message": spec["triage"]})
        attempts = spec["failed_attempts"]
        for idx, attempt in enumerate(attempts):
            at = created + timedelta(minutes=round(ttr * (0.25 + 0.4 * (idx + 1) / (len(attempts) + 1))))
            timeline.append({"timestamp": iso(at), "type": "action", "message": f"Tried (did not work): {attempt}"})
        timeline.append(
            {"timestamp": iso(created + timedelta(minutes=round(ttr * 0.8))), "type": "action", "message": f"Applied fix: {spec['resolution']}"}
        )
        timeline.append(
            {
                "timestamp": iso(resolved),
                "type": "resolution",
                "message": f"Resolved in {ttr} min. Root cause: {spec['root_cause']}. Fix: {spec['resolution']}",
            }
        )

        incident = {
            "id": incident_id,
            "pattern": spec["pattern"],
            "title": spec["title"],
            "service": spec["service"],
            "severity": spec["severity"],
            "status": "resolved",
            "alert_text": spec["alert_text"],
            "logs": render_logs(spec["logs"], created),
            "created_at": iso(created),
            "resolved_at": iso(resolved),
            "root_cause": spec["root_cause"],
            "resolution": spec["resolution"],
            "failed_attempts": attempts,
            "time_to_resolve_minutes": ttr,
            "notes": spec["notes"],
            "timeline": timeline,
            "memories_used": memories_used,
        }
        incidents.append(incident)
        by_pattern.setdefault(spec["pattern"], []).append(incident)

    seed = {
        "description": "Historical incidents for the Aftermath demo. Generated by scripts/generate_seed.py; do not edit by hand.",
        "incidents": incidents,
    }
    demo_now = utc("2026-11-05T14:03:00")
    demo = {
        "description": "Fresh alerts to POST to /incidents during the demo (drop the _demo_note field or leave it; it is ignored).",
        "alerts": [{**a, "logs": render_logs(a["logs"], demo_now)} for a in DEMO_ALERTS],
    }
    return seed, demo


def main() -> None:
    seed, demo = build()
    SEED_OUT.parent.mkdir(parents=True, exist_ok=True)
    SEED_OUT.write_text(json.dumps(seed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    DEMO_OUT.write_text(json.dumps(demo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(seed['incidents'])} incidents to {SEED_OUT.relative_to(ROOT)}")
    print(f"Wrote {len(demo['alerts'])} demo alerts to {DEMO_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
