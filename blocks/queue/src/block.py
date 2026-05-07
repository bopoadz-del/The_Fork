"""Queue Block - Async job queue with Redis/memory backend"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional, Callable
import asyncio
import time
import json
from collections import deque
from enum import Enum


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class QueueBlock(LegoBlock):
    """
    Queue Block - Background job processing
    Supports Redis (production) or memory (edge/local)
    """
    
    name = "queue"
    version = "1.0.0"
    requires = ["config", "memory"]
    layer = 2  # Core layer
    tags = ["queue", "jobs", "async", "core"]
    default_config = {
        "backend": "memory",
        "redis_url": None,
        "max_workers": 4
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.memory_block = None
        self.redis_url = config.get("redis_url")
        self.use_redis = bool(self.redis_url)
        
        # In-memory queue
        self._queues = {}  # queue_name -> deque
        self._jobs = {}    # job_id -> job_data
        self._handlers = {}  # job_type -> handler_func
        self._running = False
        self._worker_task = None
    
    async def initialize(self):
        """Initialize queue"""
        print(f"📬 Queue Block initialized")
        print(f"   Backend: {'Redis' if self.use_redis else 'Memory'}")
        
        # Start worker
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        
        return True
    
    def register_handler(self, job_type: str, handler: Callable):
        """Register a job handler"""
        self._handlers[job_type] = handler
        print(f"   Handler registered: {job_type}")
    
    async def execute(self, input_data: Dict) -> Dict:
        """Queue operations"""
        action = input_data.get("action")
        
        if action == "enqueue":
            return await self._enqueue(input_data)
        elif action == "dequeue":
            return await self._dequeue(input_data.get("queue", "default"))
        elif action == "status":
            return await self._get_status(input_data.get("job_id"))
        elif action == "list":
            return await self._list_jobs(input_data.get("queue", "default"))
        
        return {"error": f"Unknown action: {action}"}
    
    async def _enqueue(self, data: Dict) -> Dict:
        """Add job to queue"""
        job = {
            "id": f"job_{int(time.time() * 1000)}",
            "type": data.get("job_type"),
            "payload": data.get("payload", {}),
            "queue": data.get("queue", "default"),
            "priority": data.get("priority", 0),  # 0 = normal, 1 = high
            "status": JobStatus.PENDING.value,
            "created_at": time.time(),
            "retry_count": 0,
            "max_retries": data.get("max_retries", 3),
        }
        
        if self.use_redis and self.memory_block:
            # Store in Redis via memory block
            await self.memory_block.execute({
                "action": "set",
                "key": f"queue:job:{job['id']}",
                "value": job,
                "ttl": 86400  # 24 hours
            })
        else:
            # In-memory
            queue_name = job["queue"]
            if queue_name not in self._queues:
                self._queues[queue_name] = deque()
            
            if job["priority"] > 0:
                self._queues[queue_name].appendleft(job)
            else:
                self._queues[queue_name].append(job)
            
            self._jobs[job["id"]] = job
        
        return {"enqueued": True, "job_id": job["id"]}
    
    async def _dequeue(self, queue_name: str) -> Optional[Dict]:
        """Get next job from queue"""
        if self.use_redis:
            # Would use Redis BRPOP
            pass
        else:
            if queue_name in self._queues and self._queues[queue_name]:
                job = self._queues[queue_name].popleft()
                job["status"] = JobStatus.RUNNING.value
                job["started_at"] = time.time()
                return job
        
        return None
    
    async def _get_status(self, job_id: str) -> Dict:
        """Get job status"""
        if job_id in self._jobs:
            job = self._jobs[job_id]
            return {
                "job_id": job_id,
                "status": job["status"],
                "type": job["type"],
                "created_at": job["created_at"],
            }
        return {"error": "job_not_found"}
    
    async def _list_jobs(self, queue_name: str) -> Dict:
        """List jobs in queue"""
        if queue_name in self._queues:
            jobs = list(self._queues[queue_name])
            return {
                "queue": queue_name,
                "pending": len(jobs),
                "jobs": [{"id": j["id"], "type": j["type"]} for j in jobs[:10]]
            }
        return {"queue": queue_name, "pending": 0, "jobs": []}
    
    async def _worker(self):
        """Background worker"""
        while self._running:
            try:
                # Try each queue
                for queue_name in list(self._queues.keys()):
                    job = await self._dequeue(queue_name)
                    if job:
                        await self._process_job(job)
                
                await asyncio.sleep(0.1)  # Small delay
            except Exception as e:
                print(f"Worker error: {e}")
                await asyncio.sleep(1)
    
    async def _process_job(self, job: Dict):
        """Process a job"""
        handler = self._handlers.get(job["type"])
        if not handler:
            job["status"] = JobStatus.FAILED.value
            job["error"] = "no_handler"
            return
        
        try:
            result = await handler(job["payload"])
            job["status"] = JobStatus.COMPLETED.value
            job["result"] = result
            job["completed_at"] = time.time()
        except Exception as e:
            job["retry_count"] += 1
            if job["retry_count"] >= job["max_retries"]:
                job["status"] = JobStatus.FAILED.value
                job["error"] = str(e)
            else:
                job["status"] = JobStatus.RETRYING.value
                # Re-queue
                await self._enqueue({
                    "job_type": job["type"],
                    "payload": job["payload"],
                    "queue": job["queue"],
                })
    
    def health(self) -> Dict[str, Any]:
        """Health check"""
        h = super().health()
        h["backend"] = "redis" if self.use_redis else "memory"
        h["queues"] = list(self._queues.keys())
        h["handlers"] = list(self._handlers.keys())
        h["total_jobs"] = len(self._jobs)
        return h
