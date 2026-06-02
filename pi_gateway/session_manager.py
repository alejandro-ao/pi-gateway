from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from .config import PiConfig
from .db import Conversation, GatewayDB
from .pi_rpc import PiRpcClient, PromptResult

log = logging.getLogger(__name__)


class PiSessionManager:
    def __init__(self, config: PiConfig, db: GatewayDB):
        self.config = config
        self.db = db
        self._clients: dict[int, PiRpcClient] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="pi-client-cleanup")

    async def stop(self) -> None:
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
        await asyncio.gather(*(client.close() for client in list(self._clients.values())), return_exceptions=True)
        self._clients.clear()

    async def forget(self, conversation_id: int) -> None:
        client = self._clients.pop(conversation_id, None)
        if client:
            await client.close()

    def lock_for(self, conversation_id: int) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if not lock:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    async def _cleanup_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(60)
                now = monotonic()
                stale = [cid for cid, client in self._clients.items() if now - client.last_used > self.config.idle_ttl_seconds]
                for cid in stale:
                    log.info("closing idle pi rpc client for conversation %s", cid)
                    client = self._clients.pop(cid, None)
                    if client:
                        await client.close()
        except asyncio.CancelledError:
            return

    async def client_for(self, conversation: Conversation) -> PiRpcClient:
        client = self._clients.get(conversation.id)
        if client:
            return client
        client = PiRpcClient(self.config, session_file=conversation.pi_session_file, name=conversation.pi_session_name)
        await client.start()
        state = await client.get_state()
        await self._persist_client_state(conversation.id, client, state)
        self._clients[conversation.id] = client
        return client

    async def _persist_state(self, conversation_id: int, state: dict[str, Any]) -> None:
        await self.db.update_pi_session(
            conversation_id,
            pi_session_id=state.get("sessionId"),
            pi_session_file=state.get("sessionFile"),
            pi_session_name=state.get("sessionName"),
        )

    async def _persist_client_state(self, conversation_id: int, client: PiRpcClient, state: dict[str, Any]) -> None:
        client.apply_state(state)
        await self._persist_state(conversation_id, state)

    async def prompt(self, conversation: Conversation, text: str, *, streaming_behavior: str | None = None) -> PromptResult:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            result = await client.prompt(text, streaming_behavior=streaming_behavior)
            await self._persist_client_state(conversation.id, client, await client.get_state())
            return result

    async def state(self, conversation: Conversation) -> dict[str, Any]:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            state = await client.get_state()
            await self._persist_client_state(conversation.id, client, state)
            return state

    async def stats(self, conversation: Conversation) -> dict[str, Any]:
        async with self.lock_for(conversation.id):
            return await (await self.client_for(conversation)).get_session_stats()

    async def new_session(self, conversation: Conversation) -> dict[str, Any]:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            data = await client.new_session()
            state = await client.get_state()
            await self._persist_client_state(conversation.id, client, state)
            return {"newSession": data, "state": state}

    async def compact(self, conversation: Conversation, instructions: str | None = None) -> dict[str, Any]:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            data = await client.compact(instructions)
            await self._persist_client_state(conversation.id, client, await client.get_state())
            return data

    async def abort(self, conversation: Conversation) -> None:
        client = await self.client_for(conversation)
        await client.abort()

    async def set_name(self, conversation: Conversation, name: str) -> None:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            await client.set_session_name(name)
            await self._persist_client_state(conversation.id, client, await client.get_state())

    async def clone(self, conversation: Conversation) -> dict[str, Any]:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            data = await client.clone()
            await self._persist_client_state(conversation.id, client, await client.get_state())
            return data

    async def export_html(self, conversation: Conversation) -> str | None:
        async with self.lock_for(conversation.id):
            return await (await self.client_for(conversation)).export_html()

    async def last(self, conversation: Conversation) -> str | None:
        async with self.lock_for(conversation.id):
            return await (await self.client_for(conversation)).get_last_assistant_text()

    async def set_thinking(self, conversation: Conversation, level: str) -> None:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            await client.set_thinking_level(level)

    async def set_model(self, conversation: Conversation, provider: str, model_id: str) -> None:
        async with self.lock_for(conversation.id):
            client = await self.client_for(conversation)
            await client.set_model(provider, model_id)
            await self._persist_client_state(conversation.id, client, await client.get_state())

    async def models(self, conversation: Conversation) -> list[dict[str, Any]]:
        async with self.lock_for(conversation.id):
            return await (await self.client_for(conversation)).get_available_models()
