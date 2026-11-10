import os
import time
import asyncio
from datetime import datetime
from typing import List, Dict, Any
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

WORKER_URLS = [u.strip() for u in os.getenv("WORKER_NODES", "http://worker-node-1:8001,http://worker-node-2:8002").split(",")]

# --- PATRÓN OBSERVER DISTRIBUIDO (Estado y Telemetría del Clúster) ---
TELEMETRY_LOG: List[Dict[str, Any]] = []

class DispatchRequest(BaseModel):
    total_tasks: int = Field(16, ge=2, le=64, examples=[16])
    threads_per_node: int = Field(4, ge=1, le=8, examples=[4])
    task_load_ms: int = Field(120, ge=20, le=500, examples=[120])
    active_nodes_limit: int = Field(2, ge=1, le=2, description="Permite simular caída de 1 nodo o escalamiento")

app = FastAPI(
    title="NexusGrid Cloud - Coordinator API",
    description="Arquitectura Distribuida entre Hilos y Nodos (Patrones Proxy, Mediator, Observer y ThreadPool)",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/v1/cluster-status", tags=["Observer Distribuido"])
async def get_cluster_status():
    nodes_status = []
    async with httpx.AsyncClient(timeout=2.0) as client:
        for url in WORKER_URLS:
            try:
                res = await client.get(f"{url}/health")
                nodes_status.append(res.json())
            except Exception:
                nodes_status.append({"node_id": url, "status": "OFFLINE", "max_threads": 0})
    return {
        "coordinator": "ONLINE",
        "registered_nodes": len(WORKER_URLS),
        "nodes": nodes_status,
        "recent_telemetry": TELEMETRY_LOG[-5:]
    }

@app.post("/api/v1/dispatch", tags=["Mediator & Despacho Distribuido"])
async def dispatch_distributed_batch(req: DispatchRequest):
    start_wall = time.perf_counter()

    # 1. Observer verifica nodos disponibles
    available_urls = WORKER_URLS[:req.active_nodes_limit]
    healthy_urls = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in available_urls:
            try:
                r = await client.get(f"{url}/health")
                if r.status_code == 200:
                    healthy_urls.append(url)
            except Exception:
                continue

        if not healthy_urls:
            healthy_urls = [WORKER_URLS[0]]

        # 2. Mediator particiona el lote equitativamente entre los nodos activos
        all_tasks = list(range(1, req.total_tasks + 1))
        num_nodes = len(healthy_urls)
        sub_batches = [all_tasks[i::num_nodes] for i in range(num_nodes)]

        # 3. Despacho en paralelo por red a todos los nodos (Async I/O)
        async def send_to_worker(worker_url: str, task_subset: List[int]):
            payload = {
                "task_ids": task_subset,
                "threads_to_use": req.threads_per_node,
                "simulated_load_ms": req.task_load_ms
            }
            resp = await client.post(f"{worker_url}/process-subbatch", json=payload)
            return resp.json()

        node_responses = await asyncio.gather(
            *[send_to_worker(healthy_urls[i], sub_batches[i]) for i in range(num_nodes)]
        )

    # 4. Consolidación de resultados y cálculo de métricas de rendimiento
    flat_results = [item for sublist in node_responses for item in sublist]
    flat_results.sort(key=lambda x: x["task_id"])

    total_distributed_ms = round((time.perf_counter() - start_wall) * 1000, 2)
    sequential_estimated_ms = round(req.total_tasks * req.task_load_ms, 2)
    speedup = round(sequential_estimated_ms / total_distributed_ms, 2) if total_distributed_ms > 0 else 1.0
    throughput = round((req.total_tasks / (total_distributed_ms / 1000.0)), 2)

    event = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_tasks": req.total_tasks,
        "nodes_used": num_nodes,
        "threads_per_node": req.threads_per_node,
        "total_threads_cluster": num_nodes * req.threads_per_node,
        "sequential_estimated_ms": sequential_estimated_ms,
        "distributed_actual_ms": total_distributed_ms,
        "speedup_factor": speedup,
        "throughput_tasks_sec": throughput
    }
    TELEMETRY_LOG.append(event)

    return {
        "summary": event,
        "execution_matrix": flat_results
    }