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
                        kwargs["on_text"]("preview")
                        await asyncio.sleep(0.01)
                    return PromptResult("final answer")

                gateway.sessions = SimpleNamespace(prompt=AsyncMock(side_effect=prompt))
                await gateway._send_to_pi(update, SimpleNamespace(id=1), "question")
                self.assertEqual(gateway.sessions.prompt.call_args.kwargs["on_text"] is not None, chat_type == "private" and supports_drafts)
                if chat_type == "private" and supports_drafts:
                    gateway.app.bot.send_message_draft.assert_awaited_once()
                elif supports_drafts:
                    bot.send_message_draft.assert_not_awaited()
                gateway._reply.assert_awaited_once_with(update, "final answer")
                gateway.db.log_message.assert_awaited_once_with(1, direction="outbound", text="final answer")
