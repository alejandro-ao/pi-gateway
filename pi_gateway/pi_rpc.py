from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from .config import PiConfig

log = logging.getLogger(__name__)


class PiRpcError(RuntimeError):
    pass


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def last_assistant_text_from_messages(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            return content_to_text(message.get("content"))
    return ""


@dataclass(slots=True)
class PromptResult:
    text: str
    events: list[dict[str, Any]] = field(default_factory=list)


class PiRpcClient:
    def __init__(self, config: PiConfig, *, session_file: str | None = None, name: str | None = None):
        self.config = config
        self.session_file = session_file
        self.name = name
        self.process: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False
        self.last_used = monotonic()

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        args = [self.config.command, "--mode", "rpc"]
        if self.config.session_dir:
            args += ["--session-dir", self.config.session_dir]
        if self.session_file:
            args += ["--session", self.session_file]
        if self.name:
            args += ["--name", self.name]
        if self.config.default_provider:
            args += ["--provider", self.config.default_provider]
        if self.config.default_model:
            args += ["--model", self.config.default_model]
        if self.config.default_thinking:
            args += ["--thinking", self.config.default_thinking]
        args += self.config.extra_args

        log.info("starting pi rpc: %s cwd=%s", " ".join(args), self.config.cwd)
        self.process = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.config.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout(), name="pi-rpc-stdout")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="pi-rpc-stderr")
        await asyncio.sleep(0)

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8").rstrip("\r\n"))
                except Exception:
                    log.exception("invalid pi rpc json line: %r", line[:500])
                    continue
                if payload.get("type") == "response" and "id" in payload:
                    fut = self._pending.pop(str(payload["id"]), None)
                    if fut and not fut.done():
                        fut.set_result(payload)
                else:
                    await self._events.put(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pi rpc stdout reader crashed")
        finally:
            err = PiRpcError("pi rpc process exited")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(err)
            self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                log.info("pi stderr: %s", line.decode("utf-8", errors="replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pi rpc stderr reader crashed")

    async def request(self, payload: dict[str, Any], timeout: float | None = 300) -> dict[str, Any]:
        await self.start()
        if not self.process or not self.process.stdin:
            raise PiRpcError("pi rpc process not started")
        req_id = str(uuid.uuid4())
        payload = {**payload, "id": req_id}
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()
        self.last_used = monotonic()
        response = await asyncio.wait_for(fut, timeout=timeout)
        if not response.get("success", False):
            raise PiRpcError(str(response.get("error") or response))
        return response

    async def events_until_agent_end(self, timeout: float | None = None) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await asyncio.wait_for(self._events.get(), timeout=timeout)
            yield event
            if event.get("type") == "agent_end":
                return

    async def prompt(self, message: str, *, streaming_behavior: str | None = None) -> PromptResult:
        payload: dict[str, Any] = {"type": "prompt", "message": message}
        if streaming_behavior:
            payload["streamingBehavior"] = streaming_behavior
        await self.request(payload, timeout=60)
        events: list[dict[str, Any]] = []
        final_text = ""
        async for event in self.events_until_agent_end(timeout=None):
            events.append(event)
            if event.get("type") == "message_end":
                msg = event.get("message") or {}
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    final_text = content_to_text(msg.get("content")) or final_text
            elif event.get("type") == "agent_end":
                msgs = event.get("messages") or []
                if isinstance(msgs, list):
                    final_text = last_assistant_text_from_messages(msgs) or final_text
        self.last_used = monotonic()
        return PromptResult(text=final_text, events=events)

    async def get_state(self) -> dict[str, Any]:
        return (await self.request({"type": "get_state"}, timeout=60)).get("data") or {}

    async def get_session_stats(self) -> dict[str, Any]:
        return (await self.request({"type": "get_session_stats"}, timeout=60)).get("data") or {}

    async def new_session(self) -> dict[str, Any]:
        return (await self.request({"type": "new_session"}, timeout=120)).get("data") or {}

    async def compact(self, instructions: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "compact"}
        if instructions:
            payload["customInstructions"] = instructions
        return (await self.request(payload, timeout=None)).get("data") or {}

    async def abort(self) -> None:
        await self.request({"type": "abort"}, timeout=60)

    async def set_session_name(self, name: str) -> None:
        await self.request({"type": "set_session_name", "name": name}, timeout=60)

    async def clone(self) -> dict[str, Any]:
        return (await self.request({"type": "clone"}, timeout=120)).get("data") or {}

    async def export_html(self, output_path: str | None = None) -> str | None:
        payload: dict[str, Any] = {"type": "export_html"}
        if output_path:
            payload["outputPath"] = output_path
        data = (await self.request(payload, timeout=120)).get("data") or {}
        return data.get("path")

    async def get_last_assistant_text(self) -> str | None:
        data = (await self.request({"type": "get_last_assistant_text"}, timeout=60)).get("data") or {}
        return data.get("text")

    async def set_model(self, provider: str, model_id: str) -> None:
        await self.request({"type": "set_model", "provider": provider, "modelId": model_id}, timeout=120)

    async def set_thinking_level(self, level: str) -> None:
        await self.request({"type": "set_thinking_level", "level": level}, timeout=60)

    async def get_available_models(self) -> list[dict[str, Any]]:
        data = (await self.request({"type": "get_available_models"}, timeout=120)).get("data") or {}
        return data.get("models") or []

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()
