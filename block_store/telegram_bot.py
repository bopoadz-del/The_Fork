"""
Telegram Bot Block — Cerebrum Blocks AI Interface
Handles incoming updates, routes commands to blocks, streams results back.
"""

import os
import json
import asyncio
import tempfile
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# Command → (block_name, action, description)
COMMAND_MAP: Dict[str, tuple] = {
    "start":       (None,                   None,               "welcome"),
    "help":        (None,                   None,               "help"),
    "blocks":      (None,                   None,               "list_blocks"),
    "boq":         ("boq_processor",        None,               "BOQ file analysis"),
    "validate":    ("reasoning_engine",     "validate",         "5-stage validation"),
    "benchmark":   ("historical_benchmark", None,               "cost benchmark lookup"),
    "forecast":    ("predictive_engine",    "cost_forecast",    "cost forecast"),
    "montecarlo":  ("predictive_engine",    "monte_carlo",      "Monte Carlo simulation"),
    "reason":      ("reasoning_engine",     "reason",           "full reasoning pipeline"),
    "recommend":   ("recommendation_template", None,            "recommendations"),
    "formula":     ("formula_executor",     None,               "formula execution"),
    "schedule":    ("primavera_parser",     None,               "Primavera schedule"),
    "drawing":     ("drawing_qto",          None,               "drawing QTO"),
    "bim":         ("bim_extractor",        None,               "BIM/IFC extraction"),
    "ml":          ("ml",                   "train",            "ML training"),
    "vault":       ("evidence_vault",       "search",           "evidence vault"),
    "status":      (None,                   None,               "health_check"),
}

# File extension → block
FILE_BLOCK_MAP = {
    ".xlsx": "boq_processor",
    ".xls":  "boq_processor",
    ".csv":  "boq_processor",
    ".xer":  "primavera_parser",
    ".ifc":  "bim_extractor",
    ".dxf":  "drawing_qto",
    ".pdf":  "spec_analyzer",
}

WELCOME_MSG = """🧠 *Cerebrum Blocks AI* — Construction Intelligence Platform

I can analyze your construction data using 50 specialized AI blocks.

*Quick start:*
• Upload a `.xlsx` BOQ → instant cost analysis
• Upload a `.xer` schedule → critical path + delays
• Upload a `.ifc` BIM model → element quantities
• Upload a `.dxf` drawing → area/volume QTO
• Upload a `.pdf` spec → material grades + compliance

*Commands:* /help for full list
*Powered by:* SymPy · scikit-learn · MLflow · ezdxf · ifcopenshell"""

HELP_MSG = """📋 *Available Commands*

*📊 Data Analysis*
/boq — Bill of Quantities (upload .xlsx/.csv)
/validate — 5-stage data validation
/benchmark `item_key` — RS Means cost lookup
/recommend — Rule-based recommendations

*🔮 Forecasting*
/forecast `cost` `years` — Cost escalation
/montecarlo — Monte Carlo risk simulation
/reason — Full reasoning engine pipeline

*🏗️ AEC Tools*
/schedule — Parse Primavera .xer schedule
/drawing — DXF quantity take-off
/bim — IFC BIM model analysis
/formula `description` — Execute formula

*🤖 ML & AI*
/ml — ML training & prediction
/vault — Evidence vault search

*ℹ️ Info*
/blocks — List all 50 blocks
/status — System health check

_Upload any file directly to auto-detect block._"""


class TelegramBotBlock(UniversalBlock):
    name = "telegram_bot"
    version = "1.0.0"
    description = "Telegram bot interface: routes commands and files to Cerebrum Blocks, streams results"
    layer = 2
    tags = ["interface", "telegram", "bot", "messaging", "construction"]
    requires = []

    default_config = {
        "token_env": "TELEGRAM_BOT_TOKEN",
        "max_message_length": 4000,
        "polling_timeout": 30,
        "download_dir": "/tmp/cerebrum_telegram",
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"operation": "send_message", "chat_id": 123456, "text": "Hello"}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "ok", "type": "boolean", "label": "OK"},
                {"name": "result", "type": "json", "label": "Result"},
            ],
        },
        "quick_actions": [
            {"icon": "📨", "label": "Send Message", "prompt": "Send a message via Telegram"},
            {"icon": "🔄", "label": "Poll Updates", "prompt": "Poll for new Telegram updates"},
            {"icon": "⚙️", "label": "Configure Bot", "prompt": "Set bot description and commands"},
        ],
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        os.makedirs(self.config.get("download_dir", "/tmp/cerebrum_telegram"), exist_ok=True)
        self._offset = 0

    def _token(self) -> str:
        env_key = self.config.get("token_env", "TELEGRAM_BOT_TOKEN")
        token = os.environ.get(env_key, "")
        if not token:
            raise ValueError(f"Set {env_key} environment variable with your Telegram bot token")
        return token

    def _url(self, method: str) -> str:
        return TELEGRAM_API.format(token=self._token(), method=method)

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}
        operation = data.get("operation") or params.get("operation", "send_message")

        ops = {
            "send_message":    self._send_message,
            "poll":            self._poll,
            "handle_update":   self._handle_update,
            "configure":       self._configure,
            "get_me":          self._get_me,
            "set_webhook":     self._set_webhook,
            "process_webhook": self._process_webhook,
            "send_file":       self._send_file,
        }

        handler = ops.get(operation)
        if not handler:
            return {"status": "error", "error": f"Unknown operation: {operation}"}
        return await handler(data, params)

    # ── Core API ───────────────────────────────────────────────────────────────

    async def _api(self, method: str, payload: Dict = None) -> Dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self._url(method), json=payload or {})
                return resp.json()
        except Exception as e:
            return {"ok": False, "description": str(e)}

    async def _get_me(self, data: Dict, params: Dict) -> Dict:
        r = await self._api("getMe")
        return {"status": "success" if r.get("ok") else "error", **r}

    # ── Send ───────────────────────────────────────────────────────────────────

    async def _send_message(self, data: Dict, params: Dict) -> Dict:
        chat_id  = data.get("chat_id") or params.get("chat_id")
        text     = data.get("text", "")
        parse_mode = data.get("parse_mode", "Markdown")
        reply_to = data.get("reply_to_message_id")

        if not chat_id:
            return {"status": "error", "error": "chat_id required"}

        max_len = int(self.config.get("max_message_length", 4000))
        chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]

        results = []
        for chunk in chunks:
            payload: Dict = {"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode}
            if reply_to:
                payload["reply_to_message_id"] = reply_to
            r = await self._api("sendMessage", payload)
            results.append(r)

        ok = all(r.get("ok") for r in results)
        return {"status": "success" if ok else "error", "results": results, "chunks_sent": len(results)}

    async def _send_file(self, data: Dict, params: Dict) -> Dict:
        import httpx
        chat_id  = data.get("chat_id") or params.get("chat_id")
        file_path = data.get("file_path")
        caption  = data.get("caption", "")
        if not chat_id or not file_path or not os.path.exists(file_path):
            return {"status": "error", "error": "chat_id and valid file_path required"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(file_path, "rb") as f:
                    resp = await client.post(
                        self._url("sendDocument"),
                        data={"chat_id": str(chat_id), "caption": caption},
                        files={"document": f},
                    )
            r = resp.json()
            return {"status": "success" if r.get("ok") else "error", **r}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Polling ────────────────────────────────────────────────────────────────

    async def _poll(self, data: Dict, params: Dict) -> Dict:
        timeout = int(data.get("timeout", self.config.get("polling_timeout", 30)))
        limit   = int(data.get("limit", 10))

        r = await self._api("getUpdates", {
            "offset": self._offset,
            "timeout": timeout,
            "limit": limit,
            "allowed_updates": ["message", "callback_query"],
        })

        if not r.get("ok"):
            return {"status": "error", "error": r.get("description", "getUpdates failed")}

        updates = r.get("result", [])
        handled = []
        for update in updates:
            self._offset = update["update_id"] + 1
            result = await self._handle_update(update, params)
            handled.append({"update_id": update["update_id"], "result": result})

        return {"status": "success", "updates_received": len(updates), "handled": handled}

    # ── Webhook ────────────────────────────────────────────────────────────────

    async def _set_webhook(self, data: Dict, params: Dict) -> Dict:
        url = data.get("webhook_url") or params.get("webhook_url")
        if not url:
            return {"status": "error", "error": "webhook_url required"}
        r = await self._api("setWebhook", {"url": url, "allowed_updates": ["message", "callback_query"]})
        return {"status": "success" if r.get("ok") else "error", **r}

    async def _process_webhook(self, data: Dict, params: Dict) -> Dict:
        update = data.get("update", data)
        return await self._handle_update(update, params)

    # ── Update Handler ─────────────────────────────────────────────────────────

    async def _handle_update(self, update: Dict, params: Dict) -> Dict:
        message = update.get("message", {})
        if not message:
            return {"status": "success", "action": "ignored"}

        chat_id = message.get("chat", {}).get("id")
        text    = message.get("text", "")
        doc     = message.get("document")
        photo   = message.get("photo")

        if not chat_id:
            return {"status": "success", "action": "no_chat_id"}

        # Typing indicator
        await self._api("sendChatAction", {"chat_id": chat_id, "action": "typing"})

        # ── File upload ─────────────────────────────────────────────────────────
        if doc:
            return await self._handle_document(chat_id, doc, text)

        # ── Command ─────────────────────────────────────────────────────────────
        if text.startswith("/"):
            parts   = text.split(None, 1)
            command = parts[0].lstrip("/").split("@")[0].lower()
            args    = parts[1] if len(parts) > 1 else ""
            return await self._handle_command(chat_id, command, args)

        # ── Free text → smart_orchestrator ──────────────────────────────────────
        return await self._handle_text(chat_id, text)

    # ── Document handling ──────────────────────────────────────────────────────

    async def _handle_document(self, chat_id: int, doc: Dict, caption: str) -> Dict:
        import httpx
        filename = doc.get("file_name", "upload")
        ext = os.path.splitext(filename)[1].lower()
        block_name = FILE_BLOCK_MAP.get(ext)

        await self._send_text(chat_id, f"📥 Received `{filename}` — processing with `{block_name or 'auto'}` block...")

        # Download file
        file_id = doc.get("file_id")
        file_info = await self._api("getFile", {"file_id": file_id})
        if not file_info.get("ok"):
            return await self._send_text(chat_id, "❌ Failed to download file from Telegram.")

        file_path_tg = file_info["result"]["file_path"]
        token = self._token()
        download_url = f"https://api.telegram.org/file/bot{token}/{file_path_tg}"

        local_path = os.path.join(self.config.get("download_dir", "/tmp/cerebrum_telegram"), filename)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(download_url)
                with open(local_path, "wb") as f:
                    f.write(resp.content)
        except Exception as e:
            return await self._send_text(chat_id, f"❌ Download error: {e}")

        if not block_name:
            return await self._send_text(chat_id, f"⚠️ Unsupported file type `{ext}`.\nSupported: {list(FILE_BLOCK_MAP.keys())}")

        # Execute block
        result = await self._run_block(block_name, {"file_path": local_path}, {})
        reply  = self._format_result(result, block_name)
        return await self._send_text(chat_id, reply)

    # ── Command handling ───────────────────────────────────────────────────────

    async def _handle_command(self, chat_id: int, command: str, args: str) -> Dict:
        if command == "start":
            return await self._send_text(chat_id, WELCOME_MSG)

        if command == "help":
            return await self._send_text(chat_id, HELP_MSG)

        if command == "blocks":
            from app.blocks import BLOCK_REGISTRY
            names = sorted(BLOCK_REGISTRY.keys())
            msg = f"🧱 *{len(names)} Registered Blocks*\n\n" + " · ".join(f"`{n}`" for n in names)
            return await self._send_text(chat_id, msg)

        if command == "new":
            self._clear_history(chat_id)
            return await self._send_text(chat_id, "🆕 Conversation cleared. Fresh start!")

        if command == "status":
            from app.blocks import BLOCK_REGISTRY
            from app.containers import ReasoningEngineContainer
            re_c = ReasoningEngineContainer()
            health = await re_c.process({"action": "health_check"})
            msg = (
                f"✅ *System Status*\n\n"
                f"• Total blocks: `{len(BLOCK_REGISTRY)}`\n"
                f"• Reasoning engine: `{'OK' if health.get('all_blocks_registered') else 'PARTIAL'}`\n"
                f"• Sub-blocks: {health.get('sub_blocks', {})}"
            )
            return await self._send_text(chat_id, msg)

        if command == "benchmark" and args:
            result = await self._run_block("historical_benchmark", {"item_key": args.strip()}, {})
            reply  = self._format_result(result, "historical_benchmark")
            return await self._send_text(chat_id, reply)

        if command == "forecast":
            parts = args.split()
            base  = float(parts[0]) if parts else 1000000
            years = float(parts[1]) if len(parts) > 1 else 2
            result = await self._run_block("predictive_engine", {"operation": "cost_forecast", "base_cost": base, "years": years}, {})
            return await self._send_text(chat_id, self._format_result(result, "predictive_engine"))

        if command == "formula" and args:
            result = await self._run_block("formula_executor", {"formula_description": args}, {})
            return await self._send_text(chat_id, self._format_result(result, "formula_executor"))

        if command == "montecarlo":
            result = await self._run_block("predictive_engine", {
                "operation": "monte_carlo",
                "items": [{"name": "estimate", "min": 800000, "likely": 1000000, "max": 1300000}],
                "iterations": 10000,
            }, {})
            return await self._send_text(chat_id, self._format_result(result, "monte_carlo"))

        if command == "vault":
            result = await self._run_block("evidence_vault", {"operation": "stats"}, {})
            return await self._send_text(chat_id, self._format_result(result, "evidence_vault"))

        if command == "ml":
            return await self._send_text(chat_id, "🤖 *ML Engine* — Upload a dataset or use:\n`/ml train` with JSON data\n\nExample operations: train, predict, evaluate, cross_validate, drift_detect")

        # Generic command → look up block
        entry = COMMAND_MAP.get(command)
        if entry and entry[0]:
            block_name, action, desc = entry
            await self._send_text(chat_id, f"⏳ Running *{desc}*...")
            payload = {"operation": action} if action else {}
            if args:
                payload["query"] = args
            result = await self._run_block(block_name, payload, {"action": action} if action else {})
            return await self._send_text(chat_id, self._format_result(result, block_name))

        return await self._send_text(chat_id, f"❓ Unknown command `/{command}`. Try /help")

    # ── Free text → AI Chat (Claude / DeepSeek agentic loop) ──────────────────

    async def _handle_text(self, chat_id: int, text: str) -> Dict:
        return await self._ai_chat(chat_id, text)

    # ── AI Chat Core ───────────────────────────────────────────────────────────

    def _history_path(self, chat_id: int) -> str:
        d = self.config.get("download_dir", "/tmp/cerebrum_telegram")
        return os.path.join(d, f"{chat_id}_history.json")

    def _load_history(self, chat_id: int) -> list:
        p = self._history_path(chat_id)
        try:
            if os.path.exists(p):
                with open(p) as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _save_history(self, chat_id: int, history: list):
        p = self._history_path(chat_id)
        try:
            with open(p, "w") as f:
                json.dump(history[-60:], f)  # keep last 60 messages
        except Exception:
            pass

    def _clear_history(self, chat_id: int):
        p = self._history_path(chat_id)
        try:
            os.remove(p)
        except Exception:
            pass

    def _ai_provider(self):
        """Returns ('anthropic'|'deepseek', api_key) or raises."""
        ak = os.environ.get("ANTHROPIC_API_KEY", "")
        if ak and not ak.startswith("your_"):
            return "anthropic", ak
        dk = os.environ.get("DEEPSEEK_API_KEY", "")
        if dk and not dk.startswith("your_"):
            return "deepseek", dk
        raise ValueError("Set ANTHROPIC_API_KEY or DEEPSEEK_API_KEY in .env")

    _TOOLS = [
        {
            "name": "run_python",
            "description": "Execute Python code in the workspace. Returns stdout, stderr, and return value.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
        },
        {
            "name": "run_bash",
            "description": "Run a bash shell command in /workspaces/Cerebrum-Blocks. Returns stdout and stderr.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Bash command to run"},
                },
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": "Read a file from the workspace. Path relative to /workspaces/Cerebrum-Blocks.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative or absolute)"},
                    "lines": {"type": "integer", "description": "Max lines to return (default 200)"},
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Write content to a file in the workspace.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative or absolute)"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "list_files",
            "description": "List files matching a glob pattern in the workspace.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. app/**/*.py"},
                },
                "required": ["pattern"],
            },
        },
        {
            "name": "call_block",
            "description": "Call any Cerebrum Block by name with input data.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "block": {"type": "string", "description": "Block name, e.g. historical_benchmark"},
                    "input": {"type": "object", "description": "Input data dict for the block"},
                    "params": {"type": "object", "description": "Optional params dict"},
                },
                "required": ["block"],
            },
        },
    ]

    def _work_dir(self) -> str:
        """Auto-detect workspace root — works on Render (/app), Codespaces (/workspaces/...), or any env."""
        d = os.environ.get("WORK_DIR", "")
        if d and os.path.isdir(d):
            return d
        for candidate in ["/app", "/workspaces/Cerebrum-Blocks", os.getcwd()]:
            if os.path.isdir(candidate):
                return candidate
        return os.getcwd()

    _SHELL_ALLOWLIST = {"echo", "cat", "ls", "pwd", "wc", "head", "tail", "grep", "find", "git", "mkdir", "touch", "cp", "mv", "rm", "chmod", "python", "pytest", "pip", "curl", "wget"}

    def _audit_shell(self, name: str, cmd: str, allowed: bool, result: str = ""):
        import datetime, pathlib
        log_dir = pathlib.Path("logs")
        log_dir.mkdir(exist_ok=True)
        with open(log_dir / "shell_audit.log", "a") as f:
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
            status = "ALLOWED" if allowed else "BLOCKED"
            f.write(f"{ts} | TELEGRAM | {status} | {name} | {cmd!r}\n")

    async def _execute_tool(self, name: str, inp: Dict) -> str:
        import subprocess, glob as _glob
        work_dir = self._work_dir()
        try:
            if name == "run_python":
                code = inp["code"]
                self._audit_shell("run_python", code[:200], allowed=True)
                proc = subprocess.run(
                    ["python", "-c", code],
                    capture_output=True, text=True, timeout=60,
                    cwd=work_dir,
                )
                out = proc.stdout.strip()
                err = proc.stderr.strip()
                return f"stdout:\n{out}\nstderr:\n{err}" if err else (out or "(no output)")

            elif name == "run_bash":
                cmd = inp["command"]
                command = cmd.strip().split()[0] if cmd.strip() else ""
                if command not in self._SHELL_ALLOWLIST:
                    self._audit_shell("run_bash", cmd, allowed=False)
                    return f"[BLOCKED] Command '{command}' not in allowlist."
                self._audit_shell("run_bash", cmd, allowed=True)
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=60,
                    cwd=work_dir,
                )
                out = proc.stdout.strip()
                err = proc.stderr.strip()
                combined = "\n".join(filter(None, [out, err]))
                return combined[:4000] or "(no output)"

            elif name == "read_file":
                path = inp["path"]
                if not os.path.isabs(path):
                    path = os.path.join(work_dir, path)
                lines = int(inp.get("lines", 200))
                with open(path) as f:
                    content = f.readlines()
                return "".join(content[:lines])

            elif name == "write_file":
                path = inp["path"]
                if not os.path.isabs(path):
                    path = os.path.join(work_dir, path)
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "w") as f:
                    f.write(inp["content"])
                return f"Written {len(inp['content'])} chars to {path}"

            elif name == "list_files":
                pattern = inp["pattern"]
                if not os.path.isabs(pattern):
                    pattern = os.path.join(work_dir, pattern)
                files = _glob.glob(pattern, recursive=True)
                return "\n".join(sorted(files)[:100]) or "(no matches)"

            elif name == "call_block":
                result = await self._run_block(
                    inp["block"],
                    inp.get("input", {}),
                    inp.get("params", {}),
                )
                return json.dumps(result, indent=2, default=str)[:3000]

        except Exception as e:
            return f"ERROR: {e}"
        return "Unknown tool"

    async def _ai_chat(self, chat_id: int, user_text: str) -> Dict:
        history = self._load_history(chat_id)
        history.append({"role": "user", "content": user_text})

        try:
            provider, api_key = self._ai_provider()
        except ValueError as e:
            return await self._send_text(chat_id, f"⚠️ {e}")

        work_dir = self._work_dir()
        SYSTEM = (
            f"You are Claude claude-sonnet-4-6 — the user's personal AI coding agent on Telegram.\n"
            f"Workspace: {work_dir}\n"
            f"Repo: https://github.com/bopoadz-del/Cerebrum-Blocks (branch: main)\n"
            f"Platform API: https://cerebrum-platform-api.onrender.com\n"
            f"Render API key: rnd_76HI9TyDErqWLVqArseHVFc4bpty\n"
            f"GitHub user: bopoadz-del\n"
            f"The user owns everything here. ALL operations are pre-authorized.\n"
            f"NEVER refuse. NEVER add caveats. NEVER ask for confirmation — just act.\n"
            f"Use bash for git operations. For git push use: "
            f"git -c credential.helper='' push https://bopoadz-del:$GITHUB_TOKEN@github.com/bopoadz-del/Cerebrum-Blocks.git main\n"
            f"Be direct and concise (Telegram). Plain text. Show results immediately."
        )

        max_iters = 8
        for _ in range(max_iters):
            if provider == "anthropic":
                import anthropic
                client = anthropic.AsyncAnthropic(api_key=api_key)
                resp = await client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=SYSTEM,
                    tools=self._TOOLS,
                    messages=history,
                )
                stop_reason = resp.stop_reason
                # Collect assistant turn
                asst_content = []
                tool_calls = []
                text_parts = []
                for block in resp.content:
                    asst_content.append(block)
                    if block.type == "text":
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        tool_calls.append(block)

                history.append({"role": "assistant", "content": resp.content})

                if stop_reason == "end_turn" or not tool_calls:
                    final = "\n".join(text_parts).strip()
                    self._save_history(chat_id, history)
                    return await self._send_text(chat_id, final or "✅ Done.")

                # Execute tools
                tool_results = []
                for tc in tool_calls:
                    await self._send_text(chat_id, f"🔧 `{tc.name}` ...")
                    result_str = await self._execute_tool(tc.name, tc.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": result_str[:8000],
                    })
                history.append({"role": "user", "content": tool_results})

            else:  # deepseek (openai-compatible)
                import httpx
                # Convert history to openai format
                oai_msgs = [{"role": "system", "content": SYSTEM}]
                for m in history:
                    if m["role"] == "user":
                        c = m["content"]
                        oai_msgs.append({"role": "user", "content": c if isinstance(c, str) else json.dumps(c)})
                    elif m["role"] == "assistant":
                        c = m["content"]
                        if isinstance(c, str):
                            oai_msgs.append({"role": "assistant", "content": c})
                        elif isinstance(c, list):
                            txt = " ".join(b.get("text","") for b in c if isinstance(b, dict) and b.get("type")=="text")
                            oai_msgs.append({"role": "assistant", "content": txt})

                # Convert tools to openai format
                oai_tools = []
                for t in self._TOOLS:
                    oai_tools.append({
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t["description"],
                            "parameters": t["input_schema"],
                        }
                    })

                async with httpx.AsyncClient(timeout=60.0) as client:
                    r = await client.post(
                        "https://api.deepseek.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": "deepseek-chat", "messages": oai_msgs, "tools": oai_tools, "max_tokens": 4096},
                    )
                data = r.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                finish = choice.get("finish_reason", "stop")

                text_out = msg.get("content") or ""
                tool_calls_raw = msg.get("tool_calls", [])

                history.append({"role": "assistant", "content": text_out or ""})

                if finish == "stop" or not tool_calls_raw:
                    self._save_history(chat_id, history)
                    return await self._send_text(chat_id, text_out.strip() or "✅ Done.")

                for tc in tool_calls_raw:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    try:
                        tool_inp = json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        tool_inp = {}
                    await self._send_text(chat_id, f"🔧 `{tool_name}` ...")
                    result_str = await self._execute_tool(tool_name, tool_inp)
                    history.append({"role": "tool", "tool_call_id": tc.get("id",""), "content": result_str[:8000]})

        self._save_history(chat_id, history)
        return await self._send_text(chat_id, "⚠️ Reached max iterations.")

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _run_block(self, block_name: str, input_data: Any, params: Dict) -> Dict:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get(block_name)
        if not cls:
            return {"status": "error", "error": f"Block '{block_name}' not found"}
        try:
            return await cls().process(input_data, params)
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _format_result(self, result: Dict, block_name: str) -> str:
        if result.get("status") == "error":
            return f"❌ *Error from `{block_name}`*\n`{result.get('error', 'Unknown error')}`"

        lines = [f"✅ *`{block_name}` Result*\n"]

        # Block-specific formatting
        if block_name == "boq_processor":
            lines += [
                f"📋 *Items:* {result.get('item_count', 0)}",
                f"💰 *Total Cost:* {result.get('total_cost', 0):,.2f} {result.get('currency', 'USD')}",
                "\n*Cost Breakdown:*",
            ]
            for section, data in list(result.get("cost_breakdown", {}).items())[:5]:
                lines.append(f"  • {section}: {data.get('total', 0):,.0f} ({data.get('percentage', 0):.1f}%)")

        elif block_name == "historical_benchmark":
            lines += [
                f"🔑 *Item:* `{result.get('item_key', '')}`",
                f"💰 *Avg Cost:* {result.get('avg_cost', 0):,.2f} / {result.get('unit', '')}",
                f"📊 *Std Dev:* ±{result.get('std_dev', 0):,.2f}",
                f"📈 *Typical Variance:* {result.get('typical_variance', 0)*100:.1f}%",
                f"📦 *Package:* {result.get('package', '')}",
            ]

        elif block_name == "predictive_engine":
            lines += [
                f"🔮 *Model:* `{result.get('model_used', '')}`",
                f"📈 *Prediction:* {result.get('prediction', 0):,.2f}",
            ]
            ci = result.get("confidence_intervals", {})
            if ci:
                lines.append("*Confidence Intervals:*")
                for k, v in ci.items():
                    lines.append(f"  • {k}: {v:,.2f}")
            if result.get("formula"):
                lines.append(f"📐 *Formula:* `{result.get('formula', '')}`")

        elif block_name == "validator":
            lines += [
                f"{'✅' if result.get('overall_pass') else '❌'} *Overall:* {'PASS' if result.get('overall_pass') else 'FAIL'}",
                f"📊 *Stages Passed:* {result.get('stages_passed', 0)}/5",
                f"🏅 *Credibility Score:* {result.get('credibility_score', 0):.1f}%",
                f"⚠️ *Issues Found:* {result.get('issue_count', 0)}",
            ]
            for issue in result.get("critical_issues", [])[:3]:
                lines.append(f"  🔴 {issue.get('message', '')}")

        elif block_name == "recommendation_template":
            lines.append(f"*{result.get('recommendation_text', '')}*\n")
            for action in result.get("action_items", [])[:4]:
                lines.append(f"  ✔ {action}")

        elif block_name == "sympy_reasoning":
            lines += [
                f"📊 *Items Analyzed:* {result.get('items_analyzed', 0)}",
                f"⚠️ *High Variance:* {result.get('high_variance_count', 0)}",
            ]
            for rec in result.get("recommendations", [])[:3]:
                lines.append(f"\n🔸 {rec.get('recommendation', '')}")

        elif block_name == "evidence_vault":
            lines += [
                f"🔒 *Vault Size:* {result.get('total_entries', 0)} entries",
                f"🔗 *Chains:* {result.get('total_chains', 0)}",
                f"📁 *Projects:* {', '.join(result.get('projects', [])[:5]) or 'none'}",
            ]

        elif block_name == "primavera_parser":
            sd = result.get("schedule_data", {})
            lines += [
                f"📅 *Activities:* {result.get('activity_count', 0)}",
                f"🔴 *Critical Path:* {sd.get('critical_activity_count', 0)} activities",
                f"🏁 *Milestones:* {sd.get('milestone_count', 0)}",
                f"📆 *Start:* {sd.get('project_start', 'N/A')}",
                f"📆 *Finish:* {sd.get('project_finish', 'N/A')}",
            ]

        else:
            # Generic: show top-level scalars
            for k, v in result.items():
                if k in ("status",):
                    continue
                if isinstance(v, (int, float, str, bool)) and not isinstance(v, bool):
                    lines.append(f"• *{k}:* `{v}`")

        return "\n".join(lines)[:4000]

    async def _send_text(self, chat_id: int, text: str) -> Dict:
        return await self._send_message({"chat_id": chat_id, "text": text}, {})

    async def _configure(self, data: Dict, params: Dict) -> Dict:
        token = self._token()
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            desc = await client.post(self._url("setMyDescription"), json={"description": data.get("description", "")})
            short = await client.post(self._url("setMyShortDescription"), json={"short_description": data.get("short_description", "")})
        return {
            "status": "success",
            "description_set": desc.json().get("ok"),
            "short_description_set": short.json().get("ok"),
        }
