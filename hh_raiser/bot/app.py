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

from hh_raiser.bot.config_editor import (
    CATEGORY_LABELS,
    SETTINGS_BY_KEY,
    ConfigChange,
    ConfigEditError,
    IniConfigStore,
    SettingKind,
    display_setting_value,
    settings_for_category,
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
        self.config_stores = {
            key: IniConfigStore(instance.config_file, instance.state_dir)
            for key, instance in settings.instances.items()
        }
        self._awaiting_setting: dict[tuple[int, int], tuple[str, str]] = {}
        self._pending_changes: dict[tuple[int, int], tuple[str, ConfigChange]] = {}
        self.router = Router(name="hhraiser-control")
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.router.message.register(self.show_menu, CommandStart())
        self.router.message.register(self.show_menu, Command("menu"))
        self.router.message.register(self.show_all_statuses, Command("status"))
        self.router.message.register(self.show_help, Command("help"))
        self.router.message.register(self.handle_setting_input, F.text)
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
        elif action == "settings":
            await self._show_settings(query, instance)
        elif action == "category" and rest:
            await self._show_settings_category(query, instance, rest[0])
        elif action == "setting" and rest:
            await self._begin_setting_change(query, instance, rest[0])
        elif action == "apply":
            await self._apply_pending_change(query, instance)
        elif action == "cancel-setting":
            self._clear_pending(query.from_user.id, query)
            await self._show_settings(query, instance)
        elif action == "restore":
            await self._show_restore_confirmation(query, instance)
        elif action == "restore-confirm":
            await self._restore_latest_config(query, instance)
        elif action in {"start", "stop", "restart"}:
            await self._show_confirmation(query, instance, action)
        elif action == "confirm" and rest and rest[0] in {"start", "stop", "restart"}:
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

    async def _show_settings(self, query: CallbackQuery, instance: ManagedInstance) -> None:
        store = self.config_stores[instance.key]
        try:
            title_setting = SETTINGS_BY_KEY["resume_title"]
            title = display_setting_value(title_setting, store.read_value(title_setting))
            text = (
                f"<b>Настройки: {escape(instance.name)}</b>\n"
                f"Резюме: <code>{escape(title)}</code>\n\n"
                "Выберите группу. Бот разрешает менять только перечисленные параметры; "
                "пароль, токен и браузерная сессия недоступны из Telegram."
            )
        except ConfigEditError as error:
            text = f"🔴 {escape(str(error))}"
        await self._edit(query, text, self._settings_keyboard(instance))

    async def _show_settings_category(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
        category: str,
    ) -> None:
        if category not in CATEGORY_LABELS:
            await query.answer("Группа настроек больше не поддерживается.", show_alert=True)
            return
        store = self.config_stores[instance.key]
        lines = [f"<b>{CATEGORY_LABELS[category]} · {escape(instance.name)}</b>"]
        try:
            for setting in settings_for_category(category):
                value = display_setting_value(setting, store.read_value(setting))
                lines.append(f"• {escape(setting.label)}: <code>{escape(value)}</code>")
        except ConfigEditError as error:
            lines.append(f"\n🔴 {escape(str(error))}")
        await self._edit(
            query,
            "\n".join(lines),
            self._settings_category_keyboard(instance, category),
        )

    async def _begin_setting_change(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
        setting_key: str,
    ) -> None:
        setting = SETTINGS_BY_KEY.get(setting_key)
        if setting is None:
            await query.answer("Настройка больше не поддерживается.", show_alert=True)
            return
        pending_key = self._pending_key(query.from_user.id, query)
        store = self.config_stores[instance.key]
        try:
            if setting.kind is SettingKind.BOOLEAN:
                change = store.prepare_toggle(setting.key)
                self._pending_changes[pending_key] = (instance.key, change)
                await self._show_change_preview(query, instance, change)
                return
            self._awaiting_setting[pending_key] = (instance.key, setting.key)
            self._pending_changes.pop(pending_key, None)
            current = display_setting_value(setting, store.read_value(setting))
            await self._edit(
                query,
                f"<b>{escape(setting.label)}</b>\n"
                f"Сейчас: <code>{escape(current)}</code>\n\n"
                f"{escape(setting.help_text)}\n\n"
                "Отправьте новое значение обычным сообщением.",
                self._cancel_setting_keyboard(instance),
            )
        except ConfigEditError as error:
            await self._edit(query, f"🔴 {escape(str(error))}", self._settings_keyboard(instance))

    async def handle_setting_input(self, message: Message) -> None:
        if not await self._authorize_message(message):
            return
        if message.from_user is None or message.text is None:
            return
        pending_key = (message.from_user.id, message.chat.id)
        pending = self._awaiting_setting.get(pending_key)
        if pending is None:
            await message.answer(
                "Сначала выберите параметр через «⚙️ Настройки».",
                reply_markup=self._instances_keyboard(),
            )
            return
        instance_key, setting_key = pending
        instance = self.settings.instances.get(instance_key)
        if instance is None:
            self._awaiting_setting.pop(pending_key, None)
            await message.answer("Экземпляр больше не настроен.")
            return
        try:
            change = self.config_stores[instance_key].prepare_change(setting_key, message.text)
        except ConfigEditError as error:
            await message.answer(
                f"🔴 {escape(str(error))}\n\nПопробуйте ещё раз или нажмите «Отмена».",
                reply_markup=self._cancel_setting_keyboard(instance),
            )
            return
        self._awaiting_setting.pop(pending_key, None)
        self._pending_changes[pending_key] = (instance_key, change)
        await message.answer(
            self._change_preview_text(change),
            reply_markup=self._apply_setting_keyboard(instance),
        )

    async def _show_change_preview(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
        change: ConfigChange,
    ) -> None:
        await self._edit(
            query,
            self._change_preview_text(change),
            self._apply_setting_keyboard(instance),
        )

    async def _apply_pending_change(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
    ) -> None:
        pending_key = self._pending_key(query.from_user.id, query)
        pending = self._pending_changes.get(pending_key)
        if pending is None or pending[0] != instance.key:
            await query.answer("Предложение устарело. Выберите настройку заново.", show_alert=True)
            return
        _, change = pending
        try:
            await asyncio.to_thread(self.config_stores[instance.key].apply, change)
            self._pending_changes.pop(pending_key, None)
            snapshot = await self.services.snapshot(instance.service_name)
            text = (
                f"✅ <b>{escape(change.setting.label)}</b> сохранено.\n"
                "Создана резервная копия, а действие записано в аудит."
            )
            if snapshot.is_active:
                text += "\n\nЭкземпляр работает. Перезапустите его, чтобы применить настройку."
            else:
                text += "\n\nНастройка вступит в силу при следующем запуске."
            keyboard = self._after_setting_keyboard(instance, running=snapshot.is_active)
            LOGGER.info(
                "Telegram-бот изменил разрешённую настройку %s для %s.",
                change.setting.key,
                instance.service_name,
                extra=event_data(LogEvent.SYSTEM, config_setting=change.setting.key),
            )
        except (ConfigEditError, ServiceCommandError) as error:
            text = f"🔴 {escape(str(error))}"
            keyboard = self._settings_keyboard(instance)
        await self._edit(query, text, keyboard)

    async def _show_restore_confirmation(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
    ) -> None:
        store = self.config_stores[instance.key]
        if not store.backups():
            await query.answer("Резервных копий пока нет.", show_alert=True)
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, восстановить",
                        callback_data=f"{_CALLBACK_PREFIX}:restore-confirm:{instance.key}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=f"{_CALLBACK_PREFIX}:settings:{instance.key}",
                    )
                ],
            ]
        )
        await self._edit(
            query,
            "Восстановить предыдущую версию настроек? Текущая версия тоже будет сохранена.",
            keyboard,
        )

    async def _restore_latest_config(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
    ) -> None:
        try:
            await asyncio.to_thread(self.config_stores[instance.key].restore_latest)
            snapshot = await self.services.snapshot(instance.service_name)
            text = "✅ Предыдущая версия настроек восстановлена."
            if snapshot.is_active:
                text += " Перезапустите экземпляр, чтобы применить её."
            keyboard = self._after_setting_keyboard(instance, running=snapshot.is_active)
        except (ConfigEditError, ServiceCommandError) as error:
            text = f"🔴 {escape(str(error))}"
            keyboard = self._settings_keyboard(instance)
        await self._edit(query, text, keyboard)

    async def _show_confirmation(
        self,
        query: CallbackQuery,
        instance: ManagedInstance,
        action: str,
    ) -> None:
        verbs = {
            "start": "запустить",
            "stop": "остановить",
            "restart": "перезапустить",
        }
        verb = verbs[action]
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
        operations: dict[str, Callable[[str], Awaitable[None]]] = {
            "start": self.services.start,
            "stop": self.services.stop,
            "restart": self.services.restart,
        }
        operation = operations[action]
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
                        text="⚙️ Настройки",
                        callback_data=f"{_CALLBACK_PREFIX}:settings:{key}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="← Все экземпляры",
                        callback_data=f"{_CALLBACK_PREFIX}:menu:all",
                    )
                ],
            ]
        )

    def _settings_keyboard(self, instance: ManagedInstance) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{_CALLBACK_PREFIX}:category:{instance.key}:{category}",
                )
            ]
            for category, label in CATEGORY_LABELS.items()
        ]
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="↩️ Восстановить предыдущие",
                        callback_data=f"{_CALLBACK_PREFIX}:restore:{instance.key}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="← К экземпляру",
                        callback_data=f"{_CALLBACK_PREFIX}:instance:{instance.key}",
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _settings_category_keyboard(
        self,
        instance: ManagedInstance,
        category: str,
    ) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton(
                    text=setting.label,
                    callback_data=f"{_CALLBACK_PREFIX}:setting:{instance.key}:{setting.key}",
                )
            ]
            for setting in settings_for_category(category)
        ]
        rows.append(
            [
                InlineKeyboardButton(
                    text="← К настройкам",
                    callback_data=f"{_CALLBACK_PREFIX}:settings:{instance.key}",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _cancel_setting_keyboard(self, instance: ManagedInstance) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=f"{_CALLBACK_PREFIX}:cancel-setting:{instance.key}",
                    )
                ]
            ]
        )

    def _apply_setting_keyboard(self, instance: ManagedInstance) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Применить",
                        callback_data=f"{_CALLBACK_PREFIX}:apply:{instance.key}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Отмена",
                        callback_data=f"{_CALLBACK_PREFIX}:cancel-setting:{instance.key}",
                    )
                ],
            ]
        )

    def _after_setting_keyboard(
        self,
        instance: ManagedInstance,
        *,
        running: bool,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if running:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔄 Перезапустить и применить",
                        callback_data=f"{_CALLBACK_PREFIX}:restart:{instance.key}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text="← К настройкам",
                    callback_data=f"{_CALLBACK_PREFIX}:settings:{instance.key}",
                )
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _pending_key(user_id: int, query: CallbackQuery) -> tuple[int, int]:
        chat_id = query.message.chat.id if isinstance(query.message, Message) else user_id
        return user_id, chat_id

    def _clear_pending(self, user_id: int, query: CallbackQuery) -> None:
        pending_key = self._pending_key(user_id, query)
        self._awaiting_setting.pop(pending_key, None)
        self._pending_changes.pop(pending_key, None)

    @staticmethod
    def _change_preview_text(change: ConfigChange) -> str:
        old_value = display_setting_value(change.setting, change.old_value)
        new_value = display_setting_value(change.setting, change.new_value)
        return (
            f"<b>{escape(change.setting.label)}</b>\n\n"
            f"Было: <code>{escape(old_value)}</code>\n"
            f"Станет: <code>{escape(new_value)}</code>\n\n"
            "Сохранить это изменение?"
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
