import asyncio
import unittest
from unittest.mock import AsyncMock

from pi_gateway.config import PiConfig
from pi_gateway.pi_rpc import RPC_ERROR_EVENT, PiRpcClient, PiRpcError


class PiRpcSettledTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = PiRpcClient(PiConfig())
        self.client.start = AsyncMock()
        self.client.request = AsyncMock(return_value={"success": True})

    async def send(self, *events):
        for event in events:
            await self.client._events.put(event)

    async def test_compaction_retry_returns_final_run_only_after_settled(self):
        await self.send(
            {"type": "agent_start"},
            {"type": "message_end", "message": {"role": "assistant", "content": "partial answer"}},
            {"type": "agent_end", "willRetry": True, "messages": [{"role": "assistant", "content": "partial answer"}]},
            {"type": "compaction_start", "reason": "overflow"},
            {"type": "compaction_end"},
            {"type": "agent_start"},
            {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "final answer"}]}},
            {"type": "agent_end", "messages": [{"role": "assistant", "content": "final answer"}]},
            {"type": "agent_settled"},
        )
        # Pi can emit events before it replies to the prompt command. They must
        # remain queued when request() returns, not be cleared afterwards.
        pending = [self.client._events.get_nowait() for _ in range(self.client._events.qsize())]

        async def fast_request(*_args, **_kwargs):
            await self.send(*pending)
            return {"success": True}

        self.client.request.side_effect = fast_request
        result = await asyncio.wait_for(self.client.prompt("hi"), timeout=1)
        self.assertEqual(result.text, "final answer")
        self.assertEqual(result.events[-1]["type"], "agent_settled")
        self.assertEqual(len([e for e in result.events if e["type"] == "agent_end"]), 2)
        self.assertTrue(all("messages" not in e for e in result.events if e["type"] == "agent_end"))
        self.assertTrue(self.client._events.empty())

    async def test_text_deltas_stream_live_and_retry_clears_old_preview(self):
        async def request(*_args, **_kwargs):
            await self.send(
                {"type": "agent_start"},
                {"type": "message_start", "message": {"role": "assistant"}},
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "old"}},
                {"type": "message_end", "message": {"role": "assistant", "content": "old"}},
                {"type": "agent_end", "willRetry": True},
                {"type": "agent_start"},
                {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "secret"}},
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "new"}},
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": " answer"}},
                {"type": "message_end", "message": {"role": "assistant", "content": "new answer"}},
                {"type": "agent_end"}, {"type": "agent_settled"},
            )
            return {"success": True}

        self.client.request.side_effect = request
        seen = []
        result = await asyncio.wait_for(self.client.prompt("hi", on_text=seen.append), timeout=1)
        self.assertEqual(result.text, "new answer")
        self.assertEqual(seen, ["", "", "old", "", "new", "new answer"])

    async def test_tool_events_report_names_only_and_retry_resets_activity(self):
        async def request(*_args, **_kwargs):
            await self.send(
                {"type": "agent_start"},
                {"type": "tool_execution_start", "toolCallId": "first", "toolName": "bash", "args": {"command": "secret"}},
                {"type": "tool_execution_update", "toolCallId": "first", "partialResult": "secret"},
                {"type": "tool_execution_end", "toolCallId": "first", "result": "secret", "isError": True},
                {"type": "tool_execution_start", "toolCallId": "second", "toolName": "read", "args": "secret"},
                {"type": "agent_end", "willRetry": True},
                {"type": "agent_start"},
                {"type": "message_end", "message": {"role": "assistant", "content": "done"}},
                {"type": "agent_settled"},
            )
            return {"success": True}

        self.client.request.side_effect = request
        seen = []
        result = await self.client.prompt("hi", on_tool=lambda tool_id, name: seen.append((tool_id, name)))
        self.assertEqual(result.text, "done")
        self.assertEqual(seen, [(None, None), ("first", "bash"), ("first", None), ("second", "read"), (None, None)])

    async def test_preview_stops_at_telegram_limit_without_truncating_final_text(self):
        async def request(*_args, **_kwargs):
            await self.send(
                {"type": "agent_start"},
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "a" * 5000}},
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "tail"}},
                {"type": "message_end", "message": {"role": "assistant", "content": "a" * 5000 + "tail"}},
                {"type": "agent_settled"},
            )
            return {"success": True}

        self.client.request.side_effect = request
        seen = []
        result = await self.client.prompt("hi", on_text=seen.append)
        self.assertEqual(seen, ["", "a" * 4096])
        self.assertEqual(result.text, "a" * 5000 + "tail")

    async def test_stale_completion_from_earlier_command_is_ignored(self):
        await self.send({"type": "message_end", "message": {"role": "assistant", "content": "old"}},
                        {"type": "agent_settled"})

        async def new_request(*_args, **_kwargs):
            await self.send({"type": "agent_start"},
                            {"type": "message_end", "message": {"role": "assistant", "content": "new"}},
                            {"type": "agent_end"}, {"type": "agent_settled"})
            return {"success": True}

        self.client.request.side_effect = new_request
        result = await asyncio.wait_for(self.client.prompt("second"), timeout=1)
        self.assertEqual(result.text, "new")
        self.assertEqual(len(result.events), 4)

    async def test_fallback_text_uses_last_run_not_failed_attempt(self):
        async def request(*_args, **_kwargs):
            await self.send({"type": "agent_end", "finalText": "partial"},
                            {"type": "agent_start"},
                            {"type": "agent_end", "messages": [{"role": "assistant", "content": "recovered"}]},
                            {"type": "agent_settled"})
            return {"success": True}

        self.client.request.side_effect = request
        result = await asyncio.wait_for(self.client.prompt("overflow"), timeout=1)
        self.assertEqual(result.text, "recovered")

    async def test_abort_settles_without_assistant_text(self):
        async def request(*_args, **_kwargs):
            await self.send({"type": "agent_start"}, {"type": "agent_end"}, {"type": "agent_settled"})
            return {"success": True}

        self.client.request.side_effect = request
        result = await asyncio.wait_for(self.client.prompt("abort"), timeout=1)
        self.assertEqual(result.text, "")

    async def test_reader_failure_is_reported_before_settlement(self):
        async def request(*_args, **_kwargs):
            await self.send({"type": "agent_end"}, {"type": RPC_ERROR_EVENT, "error": "Pi exited"})
            return {"success": True}

        self.client.request.side_effect = request
        with self.assertRaisesRegex(PiRpcError, "Pi exited"):
            await asyncio.wait_for(self.client.prompt("hi"), timeout=1)
