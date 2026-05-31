from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from typing import TYPE_CHECKING

from .config import GatewayConfig, TelegramConfig

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes
from .db import Conversation, GatewayDB
from .session_manager import PiSessionManager

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096

HELP = """Pi Gateway commands:
/start - initialize and show help
/help - show this help
/status - current Pi session/model/stats
/new - start a fresh Pi session for this chat
/name <name> - name current Pi session
/compact [instructions] - compact current Pi context
/stop - abort current Pi operation
/last - resend last assistant response
/export - export current session to HTML
/sessions - list your known sessions
/switch <id> - point this chat at another listed conversation's Pi session
/clone - clone current Pi branch into a new session
/models - list available models
/model <provider/model-id> - switch model
/thinking <off|minimal|low|medium|high|xhigh> - set thinking level
/queue <text> - queue as follow-up
/steer <text> - steer current/next turn
/pi <text> - send raw text to Pi, including Pi slash commands

Normal messages are sent to Pi as prompts.
"""


def chunks(text: str, limit: int = 3900) -> list[str]:
    if not text:
        return ["(no response)"]
    out: list[str] = []
    while text:
        if len(text) <= limit:
            out.append(text)
            break
        split = text.rfind("\n", 0, limit)
        if split < limit // 2:
            split = limit
        out.append(text[:split])
        text = text[split:].lstrip("\n")
    return out


class TelegramGateway:
    def __init__(self, config: GatewayConfig, db: GatewayDB, sessions: PiSessionManager):
        if not config.telegram:
            raise ValueError("telegram config missing")
        self.config = config
        self.telegram: TelegramConfig = config.telegram
        from telegram.ext import Application

        self.db = db
        self.sessions = sessions
        self.app = Application.builder().token(self.telegram.bot_token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        from telegram.ext import CommandHandler, MessageHandler, filters

        for command in [
            "start", "help", "status", "new", "name", "compact", "stop", "last", "export",
            "sessions", "switch", "clone", "models", "model", "thinking", "queue", "steer", "pi",
        ]:
            self.app.add_handler(CommandHandler(command, self._command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._message))

    async def start(self) -> None:
        from telegram import BotCommand, Update

        await self.app.initialize()
        await self.app.bot.set_my_commands([
            BotCommand("help", "Show help"),
            BotCommand("status", "Show current Pi session"),
            BotCommand("new", "Start a fresh Pi session"),
            BotCommand("name", "Name current Pi session"),
            BotCommand("compact", "Compact current Pi context"),
            BotCommand("stop", "Abort current Pi operation"),
            BotCommand("last", "Resend last assistant response"),
            BotCommand("sessions", "List known sessions"),
            BotCommand("model", "Switch model"),
            BotCommand("thinking", "Set thinking level"),
        ])
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info("telegram gateway started")

    async def stop(self) -> None:
        if self.app.updater.running:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if self.telegram.allowed_user_ids and (not user or user.id not in self.telegram.allowed_user_ids):
            return False
        if chat and chat.type != "private" and not self.telegram.allow_groups:
            return False
        return True

    def _session_key_parts(self, update: Update) -> tuple[str, str | None, str | None, str]:
        chat = update.effective_chat
        user = update.effective_user
        msg = update.effective_message
        chat_id = str(chat.id if chat else "unknown")
        thread_id = str(msg.message_thread_id) if msg and msg.message_thread_id else None
        user_id = str(user.id) if user else None
        include_user = (chat and chat.type == "private") or self.telegram.include_user_in_group_session_key
        key_parts = ["telegram", chat_id]
        if thread_id:
            key_parts.append(thread_id)
        if include_user and user_id:
            key_parts.append(user_id)
        return chat_id, thread_id, user_id if include_user else None, ":".join(key_parts)

    async def conversation_for(self, update: Update) -> Conversation:
        chat_id, thread_id, user_id, key = self._session_key_parts(update)
        return await self.db.get_or_create_conversation(
            platform="telegram",
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=user_id,
            gateway_session_key=key,
            cwd=self.config.pi.cwd,
        )

    async def _reply(self, update: Update, text: str, *, document_path: str | None = None) -> None:
        msg = update.effective_message
        if not msg:
            return
        if document_path and Path(document_path).exists():
            with open(document_path, "rb") as f:
                await msg.reply_document(document=f, caption=text[:1000])
            return
        for chunk in chunks(text):
            await msg.reply_text(chunk, disable_web_page_preview=True)

    async def _typing(self, update: Update) -> None:
        chat = update.effective_chat
        if chat:
            await self.app.bot.send_chat_action(chat.id, "typing")

    async def _command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        msg = update.effective_message
        if not msg or not msg.text:
            return
        command = msg.text.split(maxsplit=1)[0].split("@", 1)[0][1:]
        arg = msg.text.split(maxsplit=1)[1] if len(msg.text.split(maxsplit=1)) > 1 else ""
        conv = await self.conversation_for(update)
        try:
            await self.db.log_message(conv.id, direction="inbound", text=msg.text, platform_message_id=str(msg.message_id))
            if command in {"start", "help"}:
                await self._reply(update, HELP)
            elif command == "status":
                await self._status(update, conv)
            elif command == "new":
                await self._typing(update)
                data = await self.sessions.new_session(conv)
                await self._reply(update, f"Started new Pi session.\n{self._state_summary(data['state'])}")
            elif command == "name":
                if not arg:
                    await self._reply(update, "Usage: /name <session name>")
                    return
                await self.sessions.set_name(conv, arg)
                await self._reply(update, f"Named session: {arg}")
            elif command == "compact":
                await self._typing(update)
                data = await self.sessions.compact(conv, arg or None)
                await self._reply(update, f"Compacted. Tokens before: {data.get('tokensBefore', 'unknown')}\n\n{data.get('summary', '')[:2500]}")
            elif command == "stop":
                await self.sessions.abort(conv)
                await self._reply(update, "Abort requested.")
            elif command == "last":
                await self._reply(update, await self.sessions.last(conv) or "No assistant message yet.")
            elif command == "export":
                path = await self.sessions.export_html(conv)
                await self._reply(update, f"Exported session: {path}", document_path=path)
            elif command == "sessions":
                await self._sessions(update, conv)
            elif command == "switch":
                await self._switch(update, conv, arg)
            elif command == "clone":
                await self.sessions.clone(conv)
                state = await self.sessions.state(conv)
                await self._reply(update, f"Cloned into current conversation.\n{self._state_summary(state)}")
            elif command == "models":
                models = await self.sessions.models(conv)
                lines = []
                for m in models[:50]:
                    provider = m.get("provider") or m.get("providerName") or "?"
                    model_id = m.get("id") or m.get("modelId") or m.get("name") or "?"
                    lines.append(f"{provider}/{model_id}")
                await self._reply(update, "Available models:\n" + "\n".join(lines))
            elif command == "model":
                if "/" not in arg:
                    await self._reply(update, "Usage: /model <provider/model-id>")
                    return
                provider, model_id = arg.split("/", 1)
                await self.sessions.set_model(conv, provider, model_id)
                await self._reply(update, f"Model set to {provider}/{model_id}")
            elif command == "thinking":
                if arg not in {"off", "minimal", "low", "medium", "high", "xhigh"}:
                    await self._reply(update, "Usage: /thinking <off|minimal|low|medium|high|xhigh>")
                    return
                await self.sessions.set_thinking(conv, arg)
                await self._reply(update, f"Thinking level set to {arg}")
            elif command in {"queue", "steer", "pi"}:
                if not arg:
                    await self._reply(update, f"Usage: /{command} <message>")
                    return
                await self._send_to_pi(update, conv, arg, streaming_behavior=("followUp" if command == "queue" else "steer" if command == "steer" else None))
            else:
                await self._reply(update, "Unknown command. Try /help")
        except Exception as e:
            log.exception("command failed")
            await self._reply(update, f"Error: {e}")

    async def _message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        msg = update.effective_message
        if not msg or not msg.text:
            return
        conv = await self.conversation_for(update)
        await self.db.log_message(conv.id, direction="inbound", text=msg.text, platform_message_id=str(msg.message_id))
        try:
            await self._send_to_pi(update, conv, msg.text)
        except Exception as e:
            log.exception("message failed")
            await self._reply(update, f"Error: {e}")

    async def _send_to_pi(self, update: Update, conv: Conversation, text: str, *, streaming_behavior: str | None = None) -> None:
        await self._typing(update)
        working = await update.effective_message.reply_text("⏳ Pi is working...")
        result = await self.sessions.prompt(conv, text, streaming_behavior=streaming_behavior)
        await self.db.touch_message(conv.id)
        await self.db.log_message(conv.id, direction="outbound", text=result.text)
        try:
            await working.delete()
        except Exception:
            pass
        await self._reply(update, result.text or "(Pi returned no text.)")

    def _state_summary(self, state: dict[str, Any]) -> str:
        model = state.get("model") or {}
        provider = model.get("provider") or model.get("providerName") or "?"
        model_id = model.get("id") or model.get("modelId") or model.get("name") or "?"
        return (
            f"Session: {state.get('sessionName') or '(unnamed)'}\n"
            f"ID: {state.get('sessionId')}\n"
            f"File: {state.get('sessionFile')}\n"
            f"Model: {provider}/{model_id}\n"
            f"Thinking: {state.get('thinkingLevel')}\n"
            f"Streaming: {state.get('isStreaming')}"
        )

    async def _status(self, update: Update, conv: Conversation) -> None:
        state = await self.sessions.state(conv)
        text = self._state_summary(state)
        try:
            stats = await self.sessions.stats(conv)
            usage = stats.get("contextUsage") or {}
            cost = stats.get("cost")
            text += f"\nMessages: {stats.get('totalMessages')}\nCost: {cost}\nContext: {usage.get('percent')}%"
        except Exception:
            pass
        await self._reply(update, text)

    async def _sessions(self, update: Update, conv: Conversation) -> None:
        rows = await self.db.list_conversations_for_user("telegram", conv.user_id, limit=10)
        lines = ["Known sessions:"]
        for c in rows:
            marker = "*" if c.id == conv.id else " "
            lines.append(f"{marker} {c.id}: {c.pi_session_name or '(unnamed)'} {c.pi_session_id or ''}\n   {c.pi_session_file or '(no file yet)'}")
        lines.append("\nUse /switch <id> to point this Telegram chat at one of these Pi sessions.")
        await self._reply(update, "\n".join(lines))

    async def _switch(self, update: Update, conv: Conversation, arg: str) -> None:
        try:
            source_id = int(arg.strip())
        except ValueError:
            await self._reply(update, "Usage: /switch <conversation-id from /sessions>")
            return
        source = await self.db.get_conversation(source_id)
        if not source:
            await self._reply(update, "Conversation not found.")
            return
        if conv.user_id and source.user_id and conv.user_id != source.user_id:
            await self._reply(update, "That session belongs to a different Telegram user.")
            return
        await self.db.point_conversation_to(conv.id, source)
        await self.sessions.forget(conv.id)
        await self._reply(update, f"Switched this chat to Pi session from conversation {source_id}.")
