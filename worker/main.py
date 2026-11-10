import os
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List
from fastapi import FastAPI
from pydantic import BaseModel

NODE_ID = os.getenv("NODE_ID", "worker-node-local")
MAX_THREADS = int(os.getenv("MAX_THREADS", "8"))

app = FastAPI(title=f"NexusGrid Worker ({NODE_ID})", version="1.0.0")

class SubBatchRequest(BaseModel):
    task_ids: List[int]
    threads_to_use: int = 4
    simulated_load_ms: int = 120

class TaskResult(BaseModel):
    task_id: int
    node_id: str
    thread_name: str
    execution_ms: float
    hash_signature: str

def execute_single_task(task_id: int, load_ms: int) -> dict:
    start = time.perf_counter()
    current_thread = threading.current_thread().name
    # Simulación de carga híbrida I/O + Hash Criptográfico liberando GIL
    time.sleep(load_ms / 1000.0)
    digest = hashlib.sha256(f"{NODE_ID}-{current_thread}-{task_id}".encode()).hexdigest()[:12]
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    return {
        "task_id": task_id,
        "node_id": NODE_ID,
        "thread_name": current_thread,
        "execution_ms": elapsed,
        "hash_signature": digest
    }

@app.get("/health")
def health():
    return {
        "node_id": NODE_ID,
        "status": "ONLINE",
        "max_threads": MAX_THREADS,
        "active_threads": threading.active_count()
    }

@app.post("/process-subbatch", response_model=List[TaskResult])
def process_subbatch(req: SubBatchRequest):
    workers = max(1, min(req.threads_to_use, MAX_THREADS))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{NODE_ID}-Hilo") as executor:
        futures = [executor.submit(execute_single_task, tid, req.simulated_load_ms) for tid in req.task_ids]
        results = [f.result() for f in futures]
    return results