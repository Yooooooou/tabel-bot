"""
Табель-бот — FastAPI + python-telegram-bot v20 (webhook mode)
APScheduler — напоминания и авто-создание листа.
v2.1
"""
import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes,
)

import config
from bot_utils import today_tz, is_authorized, month_label, TZ
from config import (
    BOT_TOKEN, ADMIN_CHAT_ID, SECTIONS, SECTION_LABELS,
    MONTH_NAMES_RU, WEBHOOK_URL,
)
import database as db
import sheets
from keyboards import (
    kb_main, kb_employees, kb_shifts, kb_finance, kb_table,
    kb_home, kb_home_repeat,
)
from handlers import (
    conv_add_employee, conv_edit_employee,
    conv_fire_employee, conv_delete_employee,
    conv_shifts, conv_finance, conv_new_admin,
)

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── /start ───────────────────────────────────────────────────────────────────

def _main_menu_text() -> str:
    t = today_tz()
    return (f"🏠 <b>Главное меню</b>\n"
            f"📅 {t.day} {MONTH_NAMES_RU[t.month]} {t.year} г.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text(_main_menu_text(),
                                    parse_mode="HTML", reply_markup=kb_main())


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Отменено.\n" + _main_menu_text(),
                                    parse_mode="HTML", reply_markup=kb_main())
    from telegram.ext import ConversationHandler
    return ConversationHandler.END


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "❌ Отменено.\n" + _main_menu_text(),
        parse_mode="HTML", reply_markup=kb_main()
    )
    from telegram.ext import ConversationHandler
    return ConversationHandler.END


async def cb_nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(_main_menu_text(), parse_mode="HTML", reply_markup=kb_main())


# ─── Главное меню ─────────────────────────────────────────────────────────────

async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_authorized(q.from_user.id):
        await q.answer("⛔ Нет доступа.")
        return
    await q.answer()
    action = q.data.split(":")[1]

    if action == "employees":
        await q.edit_message_text(
            "👥 <b>Сотрудники</b>\nДобавление, редактирование, увольнение:",
            parse_mode="HTML", reply_markup=kb_employees()
        )
    elif action == "shifts":
        await q.edit_message_text(
            "📅 <b>Смены</b>\nОтметьте или исправьте смены за любой день месяца:",
            parse_mode="HTML", reply_markup=kb_shifts()
        )
    elif action == "finance":
        await q.edit_message_text(
            "💰 <b>Финансы</b>\nАванс, удержание или процент для сотрудника:",
            parse_mode="HTML", reply_markup=kb_finance()
        )
    elif action == "table":
        t = today_tz()
        await q.edit_message_text(
            f"📊 <b>Таблица</b>\nТекущий месяц: {month_label(t.year, t.month)}",
            parse_mode="HTML", reply_markup=kb_table()
        )
    elif action == "xlsx":
        await q.edit_message_text("⏳ Формирую .xlsx, подождите…")
        await _action_send_xlsx(update, context)
    elif action == "settings":
        await _action_settings(update, context)


# ─── Список сотрудников ───────────────────────────────────────────────────────

async def cb_emp_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    employees = db.get_all_employees()
    if not employees:
        await q.edit_message_text("Список пуст.", reply_markup=kb_main())
        return
    lines = []
    for sec in SECTIONS:
        sec_emps = [e for e in employees
                    if e["section"] == sec and not e.get("is_replacement_for")]
        if not sec_emps:
            continue
        lines.append(f"\n<b>{SECTION_LABELS[sec]}</b>")
        for e in sec_emps:
            status = " 🚫" if e.get("fired") else ""
            lines.append(f"  • {e['name']}{status} | {e.get('schedule','')} | {e.get('phone','')}")
    await q.edit_message_text("\n".join(lines) or "Список пуст.",
                              parse_mode="HTML",
                              reply_markup=kb_home_repeat("👥 Сотрудники", "menu:employees"))


# ─── Очистить всех сотрудников ────────────────────────────────────────────────

async def cb_clear_employees_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🧹 Удалить <b>всех</b> сотрудников из базы?\n"
        "Это действие нельзя отменить.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, удалить всех", callback_data="emp:clear_yes")],
            [InlineKeyboardButton("❌ Отмена",           callback_data="menu:employees")],
        ])
    )


async def cb_clear_employees_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db.clear_all_employees()
    await q.edit_message_text(
        "✅ Все сотрудники удалены.",
        reply_markup=kb_home_repeat("👥 Сотрудники", "menu:employees")
    )


# ─── Таблица ──────────────────────────────────────────────────────────────────

async def cb_table_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]

    if action == "build":
        t = today_tz()
        await q.edit_message_text(
            f"⏳ Создаю/пересобираю таблицу за {month_label(t.year, t.month)}…"
        )
        try:
            ws, _ = sheets.build_sheet(t.year, t.month)
            await q.edit_message_text(
                f"✅ <b>Таблица готова!</b>\n\n"
                f"📋 Лист: «{ws.title}»\n"
                f"📅 {month_label(t.year, t.month)}",
                parse_mode="HTML",
                reply_markup=kb_home_repeat("📁 Скачать .xlsx", "menu:xlsx")
            )
        except Exception as e:
            logger.exception("build_sheet error")
            await q.edit_message_text(f"❌ Ошибка: {e}", reply_markup=kb_home())

    elif action == "clear":
        t = today_tz()
        await q.edit_message_text(
            f"🧹 Удалить лист <b>{month_label(t.year, t.month)}</b>?\n"
            f"Это действие нельзя отменить.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да, удалить", callback_data="table:clear_yes")],
                [InlineKeyboardButton("❌ Отмена",      callback_data="nav:home")],
            ])
        )

    elif action == "clear_yes":
        t = today_tz()
        try:
            deleted = sheets.delete_sheet(t.year, t.month)
            if deleted:
                await q.edit_message_text(
                    f"✅ Лист <b>{month_label(t.year, t.month)}</b> удалён.",
                    parse_mode="HTML",
                    reply_markup=kb_home_repeat("📊 Таблица", "menu:table")
                )
            else:
                await q.edit_message_text(
                    f"⚠️ Лист не найден.",
                    reply_markup=kb_home_repeat("📊 Таблица", "menu:table")
                )
        except Exception as e:
            logger.exception("delete_sheet error")
            await q.edit_message_text(f"❌ Ошибка: {e}", reply_markup=kb_home())


# ─── Скачать xlsx ─────────────────────────────────────────────────────────────

async def _action_send_xlsx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    t = today_tz()
    try:
        path = sheets.export_to_xlsx(t.year, t.month)
        if path:
            with open(path, "rb") as f:
                await context.bot.send_document(
                    chat_id=q.message.chat_id,
                    document=f,
                    filename=os.path.basename(path),
                    caption=f"📁 Табель {month_label(t.year, t.month)}"
                )
            os.remove(path)
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=_main_menu_text(),
                parse_mode="HTML",
                reply_markup=kb_main()
            )
        else:
            await q.edit_message_text(
                "❌ Лист не найден. Сначала создайте таблицу 📊.",
                reply_markup=kb_home_repeat("📊 Создать таблицу", "menu:table")
            )
    except Exception as e:
        logger.exception("export_to_xlsx error")
        await q.edit_message_text(f"❌ Ошибка экспорта: {e}", reply_markup=kb_home())


# ─── Настройки ────────────────────────────────────────────────────────────────

async def _action_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    admins = db.get_bot_admins()
    admins_text = "\n".join(f"  • {a}" for a in admins) if admins else "  (только владелец)"
    text = f"⚙️ <b>Настройки</b>\n\nАдминистраторы бота:\n{admins_text}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить администратора", callback_data="settings:add_admin")],
        [InlineKeyboardButton("🏠 Главное меню",            callback_data="nav:home")],
    ])
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)


# ─── Напоминания и авто-создание листа ────────────────────────────────────────

async def _send_reminder(bot, text: str):
    targets = [ADMIN_CHAT_ID] + db.get_bot_admins()
    for chat_id in targets:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            pass


async def morning_reminder(bot):
    t = today_tz()
    await _send_reminder(
        bot,
        f"☀️ Доброе утро! Не забудьте отметить утренние смены за {t.day} {MONTH_NAMES_RU[t.month]}."
    )


async def evening_reminder(bot):
    t = today_tz()
    await _send_reminder(
        bot,
        f"🌙 Добрый вечер! Не забудьте отметить вечерние смены за {t.day} {MONTH_NAMES_RU[t.month]}."
    )


async def auto_create_sheet(bot):
    t = today_tz()
    try:
        sheets.build_sheet(t.year, t.month)
        await _send_reminder(
            bot,
            f"📊 Автоматически создан новый лист табеля: {month_label(t.year, t.month)}"
        )
    except Exception as e:
        logger.exception("auto_create_sheet error")
        await _send_reminder(bot, f"❌ Ошибка авто-создания листа: {e}")


# ─── Регистрация хэндлеров ────────────────────────────────────────────────────

def setup_handlers(app: Application):
    # ConversationHandlers (специфичные раньше общих)
    app.add_handler(conv_add_employee())
    app.add_handler(conv_edit_employee())
    app.add_handler(conv_shifts())
    app.add_handler(conv_finance())
    app.add_handler(conv_fire_employee())
    app.add_handler(conv_delete_employee())
    app.add_handler(conv_new_admin())

    # Команды
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("menu",   cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Навигация
    app.add_handler(CallbackQueryHandler(cb_nav_home,   pattern="^nav:home$"))
    app.add_handler(CallbackQueryHandler(cb_main_menu,  pattern="^menu:"))

    # Список сотрудников
    app.add_handler(CallbackQueryHandler(cb_emp_list, pattern="^emp:list$"))

    # Очистить всех сотрудников
    app.add_handler(CallbackQueryHandler(cb_clear_employees_confirm, pattern="^emp:clear$"))
    app.add_handler(CallbackQueryHandler(cb_clear_employees_execute, pattern="^emp:clear_yes$"))

    # Таблица
    app.add_handler(CallbackQueryHandler(cb_table_action, pattern="^table:"))

    # Отмена вне конверсейшнов
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern="^cancel$"))


# ─── FastAPI + lifespan ────────────────────────────────────────────────────────

ptb_app: Application = None   # type: ignore
scheduler: AsyncIOScheduler = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app, scheduler

    ptb_app = Application.builder().token(BOT_TOKEN).build()
    setup_handlers(ptb_app)
    await ptb_app.initialize()

    if WEBHOOK_URL:
        await ptb_app.bot.set_webhook(
            url=f"{WEBHOOK_URL}/webhook",
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info(f"Webhook set: {WEBHOOK_URL}/webhook")
    else:
        logger.warning("WEBHOOK_URL не задан — бот не получит обновления!")

    await ptb_app.start()

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(
        morning_reminder, "cron",
        hour=config.REMINDER_MORNING[0], minute=config.REMINDER_MORNING[1],
        kwargs={"bot": ptb_app.bot},
    )
    scheduler.add_job(
        evening_reminder, "cron",
        hour=config.REMINDER_EVENING[0], minute=config.REMINDER_EVENING[1],
        kwargs={"bot": ptb_app.bot},
    )
    scheduler.add_job(
        auto_create_sheet, "cron",
        day=1, hour=0, minute=5,
        kwargs={"bot": ptb_app.bot},
    )
    scheduler.start()
    logger.info("APScheduler started")

    yield

    scheduler.shutdown()
    await ptb_app.stop()
    await ptb_app.shutdown()


fast_app = FastAPI(lifespan=lifespan)


@fast_app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return {"ok": True}


@fast_app.get("/")
async def health():
    return {"status": "ok", "bot": "tabel-bot"}
