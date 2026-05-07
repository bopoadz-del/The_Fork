"""Jetson Gateway Block - Edge computing gateway for construction actions on NVIDIA Jetson fleet"""

import os
import time
import uuid
import asyncio
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock


class JetsonGatewayBlock(UniversalBlock):
    name = "jetson_gateway"
    version = "1.0.0"
    description = "Edge computing gateway: dispatch construction actions to NVIDIA Jetson fleet with OTA, health monitoring, parallel execution"
    layer = 2
    tags = ["infrastructure", "edge", "jetson", "iot", "gateway", "construction"]
    requires = []

    default_config = {
        "timeout_seconds": 30,
        "max_parallel": 4,
        "retry_count": 2,
        "fleet_registry_env": "JETSON_FLEET_REGISTRY",
        "fallback_local": True,
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"action_name": "drawing_qto", "file_path": "/data/plan.dxf", "parameters": {}}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "structured_json", "type": "json", "label": "Result"},
                {"name": "execution_time_ms", "type": "number", "label": "Exec Time (ms)"},
                {"name": "device_id", "type": "text", "label": "Device"},
                {"name": "mode", "type": "text", "label": "Mode"},
            ],
        },
        "quick_actions": [
            {"icon": "🔌", "label": "Fleet Health", "prompt": "Check fleet health status"},
            {"icon": "🚀", "label": "Deploy OTA", "prompt": "Deploy OTA update to all devices"},
            {"icon": "📊", "label": "Fleet Status", "prompt": "Show device status and load"},
        ],
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self._fleet: Dict[str, Dict] = {}
        self._load_fleet_from_env()

    def _load_fleet_from_env(self):
        env_key = self.config.get("fleet_registry_env", "JETSON_FLEET_REGISTRY")
        raw = os.environ.get(env_key, "")
        if raw:
            import json
            try:
                self._fleet = json.loads(raw)
            except Exception:
                pass
        # Also check individual JETSON_DEVICE_* env vars
        for key, val in os.environ.items():
            if key.startswith("JETSON_DEVICE_"):
                device_id = key[len("JETSON_DEVICE_"):].lower()
                self._fleet[device_id] = {"url": val, "status": "unknown"}

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        action_name = data.get("action_name") or params.get("action_name", "")
        file_path = data.get("file_path") or params.get("file_path")
        parameters = data.get("parameters", {})
        parameters.update(params.get("parameters", {}))

        if not action_name:
            return {"status": "error", "error": "action_name is required"}

        # Special meta-actions
        if action_name == "fleet_health":
            return await self._fleet_health()
        if action_name == "fleet_status":
            return await self._fleet_status()
        if action_name == "ota_deploy":
            return await self._ota_deploy(data, params)
        if action_name == "parallel_execute":
            return await self._parallel_execute(data.get("actions", []), params)

        # Route single action
        device_id = data.get("device_id") or params.get("device_id")
        return await self._dispatch(action_name, file_path, parameters, device_id)

    async def _dispatch(
        self,
        action_name: str,
        file_path: Optional[str],
        parameters: Dict,
        preferred_device: Optional[str] = None,
    ) -> Dict:
        start_ms = int(time.time() * 1000)

        # Select device
        device = self._select_device(preferred_device)

        if device:
            result = await self._call_device(device, action_name, file_path, parameters)
            if result.get("status") == "success":
                return result

        # Fallback: run locally via block registry
        if self.config.get("fallback_local", True):
            return await self._local_fallback(action_name, file_path, parameters, start_ms)

        return {
            "status": "error",
            "error": f"No available device for action '{action_name}' and local fallback disabled",
            "device_id": "none",
            "execution_time_ms": int(time.time() * 1000) - start_ms,
        }

    async def _call_device(
        self, device: Dict, action_name: str, file_path: Optional[str], parameters: Dict
    ) -> Dict:
        start_ms = int(time.time() * 1000)
        try:
            import httpx
            payload = {
                "block": "construction",
                "input": {"file_path": file_path, "action_name": action_name},
                "params": {"action": action_name, **parameters},
            }
            timeout = float(self.config.get("timeout_seconds", 30))
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{device['url']}/execute",
                    json=payload,
                    headers={"X-Device-ID": device.get("id", "unknown")},
                )
            if resp.status_code == 200:
                body = resp.json()
                body["device_id"] = device.get("id", "unknown")
                body["mode"] = "edge"
                body["execution_time_ms"] = int(time.time() * 1000) - start_ms
                device["status"] = "healthy"
                return body
            device["status"] = f"http_{resp.status_code}"
        except Exception as e:
            device["status"] = f"error: {e}"
        return {"status": "error", "error": device["status"]}

    async def _local_fallback(
        self, action_name: str, file_path: Optional[str], parameters: Dict, start_ms: int
    ) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        # Try direct block first
        block_cls = BLOCK_REGISTRY.get(action_name)
        if block_cls:
            instance = block_cls()
            input_data = {"file_path": file_path} if file_path else {}
            result = await instance.process(input_data, parameters)
            return {
                "status": result.get("status", "success"),
                "structured_json": result,
                "execution_time_ms": int(time.time() * 1000) - start_ms,
                "device_id": "local",
                "mode": "local_fallback",
            }
        # Try construction container
        construction_cls = BLOCK_REGISTRY.get("construction")
        if construction_cls:
            instance = construction_cls()
            input_data = {"file_path": file_path, "action": action_name} if file_path else {"action": action_name}
            result = await instance.process(input_data, {"action": action_name, **parameters})
            return {
                "status": result.get("status", "success"),
                "structured_json": result,
                "execution_time_ms": int(time.time() * 1000) - start_ms,
                "device_id": "local",
                "mode": "local_construction_container",
            }
        return {
            "status": "error",
            "error": f"Action '{action_name}' not found locally",
            "device_id": "local",
            "execution_time_ms": int(time.time() * 1000) - start_ms,
            "mode": "local_fallback",
        }

    async def _parallel_execute(self, actions: List[Dict], params: Dict) -> Dict:
        max_parallel = int(self.config.get("max_parallel", 4))
        start_ms = int(time.time() * 1000)

        sem = asyncio.Semaphore(max_parallel)

        async def run_one(action_spec: Dict) -> Dict:
            async with sem:
                return await self._dispatch(
                    action_spec.get("action_name", ""),
                    action_spec.get("file_path"),
                    action_spec.get("parameters", {}),
                    action_spec.get("device_id"),
                )

        tasks = [asyncio.create_task(run_one(a)) for a in actions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                final.append({"status": "error", "error": str(r), "action": actions[i].get("action_name")})
            else:
                r["action"] = actions[i].get("action_name")
                final.append(r)

        success_count = sum(1 for r in final if r.get("status") == "success")
        return {
            "status": "success" if success_count > 0 else "error",
            "structured_json": {"results": final},
            "execution_time_ms": int(time.time() * 1000) - start_ms,
            "device_id": "parallel",
            "mode": "parallel",
            "total_actions": len(actions),
            "success_count": success_count,
        }

    async def _fleet_health(self) -> Dict:
        if not self._fleet:
            return {
                "status": "success",
                "fleet_size": 0,
                "devices": [],
                "mode": "no_fleet_configured",
                "hint": "Set JETSON_DEVICE_<id>=http://host:port env vars to register devices",
            }
        health = []
        for device_id, device in self._fleet.items():
            device_entry = {"id": device_id, "url": device.get("url", ""), "status": "unknown"}
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{device['url']}/health")
                    device_entry["status"] = "healthy" if resp.status_code == 200 else f"http_{resp.status_code}"
                    if resp.status_code == 200:
                        device_entry.update(resp.json())
            except Exception as e:
                device_entry["status"] = f"unreachable: {e}"
            health.append(device_entry)

        healthy = sum(1 for d in health if d["status"] == "healthy")
        return {
            "status": "success",
            "fleet_size": len(health),
            "healthy_count": healthy,
            "devices": health,
            "device_id": "gateway",
            "mode": "fleet_health",
        }

    async def _fleet_status(self) -> Dict:
        health = await self._fleet_health()
        return {
            **health,
            "mode": "fleet_status",
            "config": {
                "max_parallel": self.config.get("max_parallel"),
                "timeout_seconds": self.config.get("timeout_seconds"),
                "fallback_local": self.config.get("fallback_local"),
            },
        }

    async def _ota_deploy(self, data: Dict, params: Dict) -> Dict:
        version = data.get("version") or params.get("version", "latest")
        target_devices = data.get("devices") or list(self._fleet.keys())
        results = []
        for device_id in target_devices:
            device = self._fleet.get(device_id)
            if not device:
                results.append({"device_id": device_id, "status": "not_found"})
                continue
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{device['url']}/ota/deploy",
                        json={"version": version},
                    )
                results.append({
                    "device_id": device_id,
                    "status": "deployed" if resp.status_code == 200 else f"http_{resp.status_code}",
                    "version": version,
                })
            except Exception as e:
                results.append({"device_id": device_id, "status": f"error: {e}"})

        return {
            "status": "success",
            "mode": "ota_deploy",
            "version": version,
            "device_id": "gateway",
            "devices_targeted": len(target_devices),
            "results": results,
        }

    def _select_device(self, preferred: Optional[str] = None) -> Optional[Dict]:
        if not self._fleet:
            return None
        if preferred and preferred in self._fleet:
            d = self._fleet[preferred]
            return {**d, "id": preferred}
        # Pick first healthy or unknown device
        for device_id, device in self._fleet.items():
            if device.get("status") not in ("error", "unreachable"):
                return {**device, "id": device_id}
        return None
