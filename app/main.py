import asyncio
import os,ctypes
import random
from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor

from app.database import init_db,close_db, save_event, mark_delivered
from app.downstream import forward_event
from app.processor import scrub, fingerprint_of

app = FastAPI(title="Webhook Ingestion Service")

executor = ThreadPoolExecutor(max_workers=max(1, os.cpu_count()))

#creates an hashmap to get faster to reject repetitive keys
idempotency_cache: dict[str, str] = {}
_background_tasks: set[asyncio.Task] = set()

class WebhookPayload(BaseModel):
    event_id: str
    tenant_id: str
    content: str


async def deliver_with_retry(event_id: str, cleaned_text: str, fingerprint: str) -> None:
    for attempt in range(8):
        try:
            await forward_event(event_id, cleaned_text, fingerprint)
            break
        except Exception:
            await asyncio.sleep(min(0.05 * 2**attempt, 0.5) * (0.5 + random.random()))
    else:
        return  # fica 'pending'

    for attempt in range(5):
        try:
            await mark_delivered(event_id, fingerprint)
            return
        except Exception:
            await asyncio.sleep(0.05 * (attempt + 1))

async def process_and_deliver(event_id: str, cleaned_text: str) -> None:
    await asyncio.sleep(0.3)
    loop = asyncio.get_running_loop()
    fingerprint = await loop.run_in_executor(executor, fingerprint_of, cleaned_text)
    await deliver_with_retry(event_id, cleaned_text, fingerprint)

@app.on_event("startup")
async def startup():
    await init_db()
    if os.name == "nt":
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00008000)  # ABOVE_NORMAL

@app.on_event("shutdown")
async def shutdown():
    await close_db()    
 


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhooks/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_webhook(
    payload: WebhookPayload,
    response: Response,
    x_idempotency_key: str | None = Header(default=None),
):
    if not x_idempotency_key:
        raise HTTPException(status_code=400, detail="Missing X-Idempotency-Key header")

    if x_idempotency_key in idempotency_cache:
        response.status_code = status.HTTP_200_OK
        return {"status": "duplicate"}

    idempotency_cache[x_idempotency_key] = payload.event_id

    try:
        cleaned_text = scrub(payload.content)  # regex: microssegundos
        is_new = await save_event(
            x_idempotency_key, payload.event_id, payload.tenant_id, cleaned_text
        )
    except Exception:
        idempotency_cache.pop(x_idempotency_key, None)
        raise

    if not is_new:
        response.status_code = status.HTTP_200_OK
        return {"status": "duplicate"}

    task = asyncio.create_task(process_and_deliver(payload.event_id, cleaned_text))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "queued"}