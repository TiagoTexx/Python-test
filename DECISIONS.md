# Architecture & Debugging Decisions

## 1. Root-Cause Diagnosis

- **Event-loop / CPU behavior:** 
`scrub_and_fingerprint` ran inside the request
  handler. The fingerprint (PBKDF2, 120,000 iterations) is CPU-bound, so every
  new event paid for it before the acknowledgment and the rest of the burst
  queued behind it. Measured ingest p95: ~9.8 s with too few executor workers,
  ~1.2 s with the hash still in the handler, and ~250-300 ms once the hash was
  moved out of the ack path. The fix was to split the work: the regex scrub
  (microseconds) stays in the handler, the fingerprint runs after the response.
- **Concurrency & race conditions:** 
   Identical retries could both pass a check-then-insert and create duplicate rows 
   and duplicate deliveries; there was no atomic uniqueness guarantee. 
   To fix this, we added an in-memory hashmap (idempotency_cache: dict[str, str]) 
   as an O(1) fast path to reserve keys before the first await and reject duplicates
   immediately without hitting SQLite, backed by a UNIQUE constraint in the database. 
   We also ensured that if persistence fails, the reserved key is removed from the hashmap
   so later retries are not falsely answered with 200 for an event that was never saved.
- **Resource & connection management:**
  - A new SQLite connection was opened for every write, behind a lock. Writes
    queued up: measured `lock_wait` grew in steps from 0 to ~220 ms across 30
    writes, with `connect` and `insert+commit` .
  - Delivery and the "mark delivered" DB update shared one `try` block, so a DB
    failure after a successful delivery re-sent the event downstream.
  - The downstream HTTP client must be shared; creating one per delivery during
    a burst is expensive.

## 2. Concurrency & Offloading Strategy

- **Acknowledgment path:** 
  the handler only does what must happen before the
  response: the idempotency check, the regex scrub, one `INSERT`, then `202`
  (new event) or `200` (duplicate). Nothing slow runs here.
- **CPU-bound preprocessing:** 
  the fingerprint runs in a `ThreadPoolExecutor`
  via `loop.run_in_executor`. `hashlib.pbkdf2_hmac` releases the GIL, so the
  threads really run in parallel and the event loop stays free for `/health`
  and for new requests. A `ProcessPoolExecutor` was not chosen: on Windows it
  adds process spawn cost and IPC for tiny payloads, with no benefit here.
  `asyncio.to_thread` would use the default executor with no control over
  worker count, so an explicit executor was preferred.
- **Downstream delivery:** 
  started with `asyncio.create_task` after the
  acknowledgment, keeping strong references in a set so tasks cannot be
  garbage-collected mid-flight. This was preferred over FastAPI
  `BackgroundTasks` to keep the task lifetime explicit.

## 3. Idempotency & SQLite Concurrency

Idempotency has two layers, and only the second one is a guarantee.

- **Layer 1, in-memory hashmap (fast path):** 
  `idempotency_cache` maps the`X-Idempotency-Key` to the event id. 
  A repeated key is answered with `200` in O(1), without spending CPU or touching the disk.
  The key is reserved beforethe first `await`, so two coroutines in the same 
  process cannot both pass the check. If persistence fails, the key is removed 
  so the client can retry.
- **Layer 2, SQLite (source of truth):** 
  `idempotency_key TEXT NOT NULL UNIQUE` combined with `INSERT OR IGNORE`; 
  `rowcount == 0` means duplicate. SQLite allows one writer at a time, so two 
  identical requests arriving in the same millisecond cannot both insert, even 
  if the cache is empty (restart) or not shared (several workers).
- **SQLite settings:** 
  a single shared connection opened at startup, `journal_mode=WAL`, `synchronous=NORMAL`,
  a busy timeout, an index on `event_id` so `mark_delivered` does not scan the 
  table, and all writes serialized by an `asyncio.Lock`. The isolated effect 
  of each setting was not measured; the p95 improvement (~300 ms to ~250 ms) 
  came from the changes together.
- **Known gap:** 
  A duplicate that arrives while the original is still being
  persisted gets `200` before the original row is committed.

## 4. Failure Modes & Production Trade-offs

- **Downstream unreachable for 60 seconds:** delivery gives up after 8
  attempts (about 0.25 s with the current fixed wait). The row stays
  `pending` and nothing re-drives it, so the event is **not** delivered. This
  design does not cover a real outage. It would need a much longer backoff
  and/or a periodic re-drive of `pending` rows.
- **Crash 50 ms after acknowledging:** 
  the row is already committed as `pending` (the commit happens before the `202`), 
  so the event is not lost from the database. The in-memory delivery task is 
  lost and there is no recovery on startup, so the event stays `pending`. 
  Client retries receive `200` because of the `UNIQUE` constraint and are not reprocessed. 
  Delivery is at-least-once: a crash after the downstream accepted an event but before
  `mark_delivered` could lead to a second delivery on a future re-drive.
  Sending the idempotency key to the downstream would close that gap.
- **4 workers instead of 1:** 
  the in-memory cache and the `asyncio.Lock` are per process, so only the `UNIQUE` 
  constraint protects idempotency, and four processes contend for the single SQLite writer. 
  The cache also grows without bound and would need eviction. Any future startup re-drive of
  `pending` rows would have to claim rows atomically, otherwise every worker
  would send the same rows.

## 5. Test results and known limitation: `/health` criterion

The unique-event path (status codes, idempotency, delivery) was exercised by
the official test. The ingest p95 is close to its limit in our environment, and
the `/health` criterion depends strongly on the machine running the test.

| Environment | `/health` max (official test) |
|
| Intel i7-9750HF, Windows | 166-195 ms (every run) |
| Intel i7-9750HF, Ubuntu | 61 ms |
| AMD Ryzen 7 7800X3D, Ubuntu | 44 ms (passes) |

Ingest p95 on the Windows machine, over two batteries of 20 runs of the same
test: passed the 250 ms limit in 10/20 and 17/20 runs; the failing runs were
all between 250 and 304 ms.

Ingest p95 on the Ubuntu AMD, 

Other measurements on the Windows machine, passes everytime, getting between 50 and 65 ms.

| Measurement | Result |
|
| `/health` handler, server-side | ~0.3 ms |
| Sequential requests (GET, GET, POST, POST) | 4-11 ms |
| TCP connect during the burst (separate process, n=277) | max 3 ms |
| `/health` from a separate process during the burst (n=222) | max 40 ms |
| Event-loop lag of the test process during the burst | up to 117 ms |

Changes tried on the service side that did not move the `/health` number:
executor worker count, SQLite settings, process priority and hash cost. The
hash calculator cost was deliberately **not** reduced to pass the test.