from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from hh_raiser.bot.models import BotSettings, ManagedInstance
from hh_raiser.bot.presentation import (
    format_logs,
    format_periodic_summary,
    format_service_status,
    format_statistics,
)
from hh_raiser.bot.service_manager import ServiceCommandError, SystemdServiceManager
from hh_raiser.bot.statistics import (
    StatisticsReadError,
    read_instance_statistics,
    response_report_path,
)
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

_CALLBACK_PREFIX = "hh"


class TelegramControlBot:
    """Authorized Telegram UI for fixed HHRaiser systemd instances."""

    def __init__(
        self,
        settings: BotSettings,
        *,
        service_manager: SystemdServiceManager | None = None,
    ) -> None:
        self.settings = settings
        self.services = service_manager or SystemdServiceManager(user_mode=settings.user_systemd)
        self.router = Router(name="hhraiser-control")
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.router.message.register(self.show_menu, CommandStart())
        self.router.message.register(self.show_menu, Command("menu"))
        self.router.message.register(self.show_all_statuses, Command("status"))
        self.router.message.register(self.show_help, Command("help"))
        self.router.callback_query.register(
            self.handle_callback, F.data.startswith(f"{_CALLBACK_PREFIX}:")
        )

    async def show_menu(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        await message.answer(
            "<b>HHRaiser</b>\nВыберите экземпляр, которым хотите управлять.",
            reply_markup=self._instances_keyboard(),
        )

    async def show_help(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        await message.answer(
            "<b>Команды HHRaiser</b>\n"
            "/menu — открыть панель управления\n"
            "/status — проверить все экземпляры\n"
            "/help — показать эту справку\n\n"
            "Изменяющие состояние кнопки всегда требуют отдельного подтверждения."
        )

    async def show_all_statuses(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        parts: list[str] = []
        for instance in self.settings.instances.values():
            try:
                snapshot = await self.services.snapshot(instance.service_name)
                parts.append(format_service_status(instance, snapshot))
            except ServiceCommandError as error:
                parts.append(f"<b>{escape(instance.name)}</b>\n🔴 {escape(str(error))}")
        await message.answer("\n\n".join(parts), reply_markup=self._instances_keyboard())

    async def handle_callback(self, query: CallbackQuery) -> None:
        if not await self._authorize_callback(query):
            return
        parts = (query.data or "").split(":")
        if len(parts) < 3:
            await query.answer("Команда устарела.", show_alert=True)
            return
        _, action, instance_key, *rest = parts
        if action == "menu":
            await self._edit(
                query,
                "<b>HHRaiser</b>\nВыберите экземпляр, которым хотите управлять.",
                self._instances_keyboard(),
            )
            return
        instance = self.settings.instances.get(instance_key)
        if instance is None:
            await query.answer("Экземпляр больше не настроен.", show_alert=True)
            return
        await query.answer()
        if action == "instance":
            await self._edit(
                query, self._instance_text(instance), self._instance_keyboard(instance)
            )
        elif action == "status":
            await self._show_status(query, instance)
        elif action == "statistics":
            await self._show_statistics(query, instance)
        elif action == "logs":
            await self._show_logs(query, instance)
        elif action == "report":
            await self._send_report(query, instance)
        elif action in {"start", "stop"}:
            await self._show_confirmation(query, instance, action)
        elif action == "confirm" and rest and rest[0] in {"start", "stop"}:
            await self._change_service_state(query, instance, rest[0])
        else:
            await self._edit(query, "Эта команда больше не поддерживается.", self._back_keyboard())

    async def _show_status(self, query: CallbackQuery, instance: ManagedInstance) -> None:
        try:
            snapshot = await self.services.snapshot(instance.service_name)
            text = format_service_status(instance, snapshot)
        except ServiceCommandError as error:
            text = f"🔴 {escape(str(error))}"
        await self._edit(query, text, self._instance_keyboard(instance))

    async def _show_statistics(self, query: CallbackQuery, instance: ManagedInstance) -> None:
        try:
            statistics = await asyncio.to_thread(read_instance_statistics, instance.state_dir)
            text = format_statistics(instance, statistics)
        except StatisticsReadError as error:
            text = f"🔴 {escape(str(error))}"
        await self._edit(query, text, self._instance_keyboard(instance))

    async def _show_logs(self, query: CallbackQuery, instance: ManagedInstance) -> None:
        try:
            logs = await self.services.recent_logs(
                instance.service_name,
                lines=self.settings.log_lines,
            )
            text = format_logs(logs)
        except ServiceCommandError as error:
            text = f"🔴 {escape(str(error))}"
        await self._edit(query, text, self._instance_keyboard(instance))

    async def _send_report(self, query: CallbackQuery, instance: ManagedInstance) -> None:
        message = query.message
        if not isinstance(message, Message):
            return
        path = response_report_path(instance.state_dir)
        if path is None:
            await message.answer(
                "Журнал откликов пока не создан.",
                reply_markup=self._instance_keyboard(instance),
            )
            return
        await message.answer_document(
            FSInputFile(path, filename=f"{instance.key}-vacancy-responses.xlsx"),
            caption=f"Журнал откликов: <b>{escape(instance.name)}</b>",
        )

    async def _show_confirmation(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
        action: str,
    ) -> None:
        verb = "запустить" if action == "start" else "остановить"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Да, {verb}",
                        callback_data=f"{_CALLBACK_PREFIX}:confirm:{instance.key}:{action}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=f"{_CALLBACK_PREFIX}:instance:{instance.key}",
                    )
                ],
            ]
        )
        await self._edit(
            query,
            f"Подтвердите действие: <b>{verb} {escape(instance.name)}</b>?",
            keyboard,
        )

    async def _change_service_state(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
        action: str,
    ) -> None:
        operation: Callable[[str], Awaitable[None]] = (
            self.services.start if action == "start" else self.services.stop
        )
        try:
            await operation(instance.service_name)
            snapshot = await self.services.snapshot(instance.service_name)
            text = format_service_status(instance, snapshot)
            LOGGER.info(
                "Telegram-бот выполнил действие %s для %s.",
                action,
                instance.service_name,
                extra=event_data(LogEvent.SYSTEM),
            )
        except ServiceCommandError as error:
            text = f"🔴 {escape(str(error))}"
        await self._edit(query, text, self._instance_keyboard(instance))

    async def _authorize_message(self, message: Message) -> bool:
        user_id = message.from_user.id if message.from_user else None
        if user_id in self.settings.allowed_user_ids:
            return True
        LOGGER.warning(
            "Telegram-бот отклонил команду неразрешённого пользователя.",
            extra=event_data(LogEvent.AUTH),
        )
        await message.answer("Доступ запрещён.")
        return False

    async def _authorize_callback(self, query: CallbackQuery) -> bool:
        if query.from_user.id in self.settings.allowed_user_ids:
            return True
        LOGGER.warning(
            "Telegram-бот отклонил callback неразрешённого пользователя.",
            extra=event_data(LogEvent.AUTH),
        )
        await query.answer("Доступ запрещён.", show_alert=True)
        return False

    async def send_periodic_summaries(self, bot: Bot) -> None:
        """Send owner-only summaries at the configured interval until cancelled."""

        interval_seconds = self.settings.summary_interval_minutes * 60
        if interval_seconds <= 0:
            return
        while True:
            await asyncio.sleep(interval_seconds)
            parts: list[str] = ["<b>Периодическая сводка HHRaiser</b>"]
            for instance in self.settings.instances.values():
                try:
                    snapshot, statistics = await asyncio.gather(
                        self.services.snapshot(instance.service_name),
                        asyncio.to_thread(read_instance_statistics, instance.state_dir),
                    )
                    parts.append(format_periodic_summary(instance, snapshot, statistics))
                except (ServiceCommandError, StatisticsReadError) as error:
                    parts.append(f"<b>{escape(instance.name)}</b> — 🔴 {escape(str(error))}")
            text = "\n\n".join(parts)
            for user_id in self.settings.allowed_user_ids:
                try:
                    await bot.send_message(user_id, text)
                except TelegramAPIError:
                    LOGGER.warning(
                        "Не удалось доставить периодическую сводку разрешённому пользователю.",
                        extra=event_data(LogEvent.NETWORK),
                    )

    async def _edit(
        self,
        query: CallbackQuery,
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        if isinstance(query.message, Message):
            await query.message.edit_text(text, reply_markup=keyboard)

    def _instances_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"📄 {instance.name}",
                    callback_data=f"{_CALLBACK_PREFIX}:instance:{instance.key}",
                )
            ]
            for instance in self.settings.instances.values()
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Обновить состояния",
                    callback_data=f"{_CALLBACK_PREFIX}:menu:all",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _instance_keyboard(self, instance: ManagedInstance) -> InlineKeyboardMarkup:
        key = instance.key
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="● Состояние",
                        callback_data=f"{_CALLBACK_PREFIX}:status:{key}",
                    ),
                    InlineKeyboardButton(
                        text="📊 Статистика",
                        callback_data=f"{_CALLBACK_PREFIX}:statistics:{key}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📜 Последние события",
                        callback_data=f"{_CALLBACK_PREFIX}:logs:{key}",
                    ),
                    InlineKeyboardButton(
                        text="📥 Журнал откликов",
                        callback_data=f"{_CALLBACK_PREFIX}:report:{key}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="▶ Запустить",
                        callback_data=f"{_CALLBACK_PREFIX}:start:{key}",
                    ),
                    InlineKeyboardButton(
                        text="■ Остановить",
                        callback_data=f"{_CALLBACK_PREFIX}:stop:{key}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="← Все экземпляры",
                        callback_data=f"{_CALLBACK_PREFIX}:menu:all",
                    )
                ],
            ]
        )

    def _back_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="← В меню",
                        callback_data=f"{_CALLBACK_PREFIX}:menu:all",
                    )
                ]
            ]
        )

    @staticmethod
    def _instance_text(instance: ManagedInstance) -> str:
        return (
            f"<b>{escape(instance.name)}</b>\n"
            "Здесь можно проверить работу, посмотреть статистику и журнал либо "
            "вручную запустить или остановить экземпляр."
        )


async def run_telegram_bot(settings: BotSettings) -> None:
    """Start one long-polling process and discard stale control commands."""

    controller = TelegramControlBot(settings)
    dispatcher = Dispatcher()
    dispatcher.include_router(controller.router)
    async with Bot(
        token=settings.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    ) as bot:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_my_commands(
            [
                BotCommand(command="menu", description="Панель управления"),
                BotCommand(command="status", description="Состояние всех экземпляров"),
                BotCommand(command="help", description="Справка"),
            ]
        )
        LOGGER.info(
            "Telegram-бот HHRaiser запущен; настроено экземпляров: %s.",
            len(settings.instances),
            extra=event_data(LogEvent.SYSTEM),
        )
        summary_task = asyncio.create_task(controller.send_periodic_summaries(bot))
        try:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                handle_signals=True,
            )
        finally:
            summary_task.cancel()
            with suppress(asyncio.CancelledError):
                await summary_task
