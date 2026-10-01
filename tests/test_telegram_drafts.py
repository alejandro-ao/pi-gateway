import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pi_gateway.pi_rpc import PromptResult
from pi_gateway.telegram_bot import DraftPreview, TelegramGateway


class DraftPreviewTest(unittest.IsolatedAsyncioTestCase):
    async def test_draft_updates_are_coalesced_capped_and_final_is_not_a_draft(self):
        bot = SimpleNamespace(send_message_draft=AsyncMock())
        working = SimpleNamespace(delete=AsyncMock())
        preview = DraftPreview(bot, 123, working)
        preview.update("")
        await asyncio.sleep(0)
        bot.send_message_draft.assert_not_awaited()
        preview.update("first")
        preview.update("a" * 5000)
        await asyncio.wait_for(self._wait_for_calls(bot.send_message_draft, 1), 1)
        self.assertEqual(bot.send_message_draft.call_args.kwargs["text"], "a" * 4096)
        self.assertGreater(bot.send_message_draft.call_args.kwargs["draft_id"], 0)
        working.delete.assert_awaited_once()
        await preview.close()
        preview.update("late")
        self.assertEqual(bot.send_message_draft.await_count, 1)

    async def test_retry_clears_previous_draft_with_same_id(self):
        bot = SimpleNamespace(send_message_draft=AsyncMock())
        preview = DraftPreview(bot, 123, SimpleNamespace(delete=AsyncMock()))
        with patch("pi_gateway.telegram_bot.monotonic", side_effect=[2.0, 4.0, 6.0, 8.0]):
            preview.update("failed attempt")
            await asyncio.wait_for(self._wait_for_calls(bot.send_message_draft, 1), 1)
            preview.update("")
            await asyncio.wait_for(self._wait_for_calls(bot.send_message_draft, 2), 1)
        first, cleared = bot.send_message_draft.call_args_list
        self.assertEqual(first.kwargs["text"], "failed attempt")
        self.assertEqual(cleared.kwargs["text"], "")
        self.assertEqual(first.kwargs["draft_id"], cleared.kwargs["draft_id"])
        await preview.close()

    async def test_tool_activity_is_temporary_and_names_are_sanitized(self):
        bot = SimpleNamespace(send_message_draft=AsyncMock())
        working = SimpleNamespace(delete=AsyncMock())
        preview = DraftPreview(bot, 123, working)
        with patch("pi_gateway.telegram_bot.monotonic", side_effect=[2.0, 4.0, 6.0, 8.0]):
            preview.update_tool("call-1", "bash")
            await asyncio.wait_for(self._wait_for_calls(bot.send_message_draft, 1), 1)
            self.assertEqual(bot.send_message_draft.call_args.kwargs["text"], "🔧 Running bash…")
            working.delete.assert_awaited_once()
            preview.update_tool("call-1", None)
            await asyncio.wait_for(self._wait_for_calls(bot.send_message_draft, 2), 1)
            self.assertEqual(bot.send_message_draft.call_args.kwargs["text"], "")
        preview.update("a" * 4096)
        preview.update_tool("call-2", "bash\nsecret")
        self.assertTrue(preview._display_text().endswith("🔧 Running tool…"))
        self.assertEqual(len(preview._display_text()), 4096)
        preview.update_tool("call-3", "read")
        self.assertTrue(preview._display_text().endswith("🔧 Running read…"))
        preview.update_tool("call-3", None)
        self.assertTrue(preview._display_text().endswith("🔧 Running tool…"))
        preview.update_tool(None, None)
        self.assertEqual(preview._display_text(), "a" * 4096)
        await preview.close()

    async def test_draft_failure_is_nonfatal(self):
        bot = SimpleNamespace(send_message_draft=AsyncMock(side_effect=RuntimeError("unsupported")))
        working = SimpleNamespace(delete=AsyncMock())
        preview = DraftPreview(bot, 123, working)
        preview.update("partial")
        await asyncio.wait_for(preview.task, 1)
        self.assertTrue(preview.closed)
        working.delete.assert_not_awaited()
        await preview.close()

    async def _wait_for_calls(self, mock, count):
        while mock.await_count < count:
            await asyncio.sleep(0.001)


class TelegramDraftRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_status_distinguishes_pi_activity_from_telegram_preview(self):
        for chat_type, supports_drafts in (("private", True), ("private", False), ("supergroup", True)):
            with self.subTest(chat_type=chat_type, supports_drafts=supports_drafts):
                gateway = TelegramGateway.__new__(TelegramGateway)
                bot = SimpleNamespace(send_message_draft=AsyncMock()) if supports_drafts else SimpleNamespace()
                gateway.app = SimpleNamespace(bot=bot)
                gateway.sessions = SimpleNamespace(
                    state=AsyncMock(return_value={"isStreaming": False}), stats=AsyncMock(return_value={}),
                )
                gateway._reply = AsyncMock()
                update = SimpleNamespace(effective_chat=SimpleNamespace(id=123, type=chat_type))
                with patch("pi_gateway.telegram_bot.check_version", new_callable=AsyncMock), patch(
                    "pi_gateway.telegram_bot.format_status_line", return_value="Version: test"
                ):
                    await gateway._status(update, SimpleNamespace(id=1))
                status = gateway._reply.call_args.args[1]
                self.assertIn("Pi generating now: False", status)
                expected = "available" if chat_type == "private" and supports_drafts else "unavailable"
                self.assertIn(f"Telegram draft preview: {expected}", status)

    async def test_only_private_chats_stream_and_final_answer_is_sent(self):
        for chat_type, supports_drafts in (("private", True), ("private", False), ("supergroup", True)):
            with self.subTest(chat_type=chat_type, supports_drafts=supports_drafts):
                gateway = TelegramGateway.__new__(TelegramGateway)
                bot = SimpleNamespace(send_message_draft=AsyncMock()) if supports_drafts else SimpleNamespace()
                gateway.app = SimpleNamespace(bot=bot)
                gateway._typing = AsyncMock()
                gateway._reply = AsyncMock()
                gateway.db = SimpleNamespace(touch_message=AsyncMock(), log_message=AsyncMock())
                working = SimpleNamespace(delete=AsyncMock())
                message = SimpleNamespace(reply_text=AsyncMock(return_value=working), message_thread_id=None)
                update = SimpleNamespace(
                    effective_chat=SimpleNamespace(id=123, type=chat_type), effective_message=message,
                )

                async def prompt(*_args, **kwargs):
                    if kwargs["on_text"]:
                        kwargs["on_tool"]("call-1", "bash")
                        kwargs["on_text"]("preview")
                        kwargs["on_tool"]("call-1", None)
                        await asyncio.sleep(0.01)
                    return PromptResult("final answer")

                gateway.sessions = SimpleNamespace(prompt=AsyncMock(side_effect=prompt))
                await gateway._send_to_pi(update, SimpleNamespace(id=1), "question")
                self.assertEqual(gateway.sessions.prompt.call_args.kwargs["on_text"] is not None, chat_type == "private" and supports_drafts)
                self.assertEqual(gateway.sessions.prompt.call_args.kwargs["on_tool"] is not None, chat_type == "private" and supports_drafts)
                if chat_type == "private" and supports_drafts:
                    gateway.app.bot.send_message_draft.assert_awaited_once()
                elif supports_drafts:
                    bot.send_message_draft.assert_not_awaited()
                gateway._reply.assert_awaited_once_with(update, "final answer")
                gateway.db.log_message.assert_awaited_once_with(1, direction="outbound", text="final answer")
