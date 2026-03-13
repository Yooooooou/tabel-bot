"""
Табель-бот — FastAPI + python-telegram-bot v20 (webhook mode)
APScheduler — напоминания и авто-создание листа.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters,
)

import config
from config import (
    BOT_TOKEN, ADMIN_CHAT_ID, SECTIONS, SECTION_LABELS,
    TIMEZONE, MONTH_NAMES_RU, WEBHOOK_URL,
)
import database as db
import sheets
from schedule import days_in_month

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TZ = ZoneInfo(TIMEZONE)


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def now_tz() -> datetime:
    return datetime.now(tz=TZ)

def today_tz() -> date:
    return now_tz().date()

def is_authorized(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID or chat_id in db.get_bot_admins()

def month_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES_RU[month]} {year}"


# ─── ConversationHandler states ───────────────────────────────────────────────
(
    # Добавление сотрудника
    ADD_NAME, ADD_PHONE, ADD_POSITION, ADD_SECTION,
    ADD_SCHEDULE, ADD_DAYS_OFF, ADD_START_DATE,
    # Редактирование сотрудника
    EDIT_SELECT_EMP, EDIT_FIELD, EDIT_VALUE,
    # Смена
    SHIFT_SELECT_EMP, SHIFT_SELECT_DATE, SHIFT_SELECT_VALUE,
    SHIFT_IS_REPLACE, SHIFT_REPLACE_FOR,
    # Финансы
    FIN_SELECT_EMP, FIN_TYPE, FIN_VALUE,
    # Увольнение
    FIRE_SELECT_EMP, FIRE_DATE,
    # Удаление
    DELETE_SELECT_EMP,
    # Добавление администратора
    NEW_ADMIN_ID,
) = range(22)


# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Сотрудники",    callback_data="menu:employees")],
        [InlineKeyboardButton("📅 Смены",          callback_data="menu:shifts")],
        [InlineKeyboardButton("💰 Финансы",        callback_data="menu:finance")],
        [InlineKeyboardButton("📊 Таблица",        callback_data="menu:table")],
        [InlineKeyboardButton("📁 Скачать .xlsx",  callback_data="menu:xlsx")],
        [InlineKeyboardButton("⚙️ Настройки",      callback_data="menu:settings")],
    ])

def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
    ]])

def kb_skip_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭ Пропустить", callback_data="skip"),
        InlineKeyboardButton("❌ Отмена",     callback_data="cancel"),
    ]])

def kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Главное меню", callback_data="nav:home"),
    ]])

def kb_home_repeat(repeat_label: str, repeat_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(repeat_label, callback_data=repeat_data)],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="nav:home")],
    ])

def kb_employees() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить сотрудника",  callback_data="emp:add")],
        [InlineKeyboardButton("✏️ Редактировать",         callback_data="emp:edit")],
        [InlineKeyboardButton("🔥 Уволить",               callback_data="emp:fire")],
        [InlineKeyboardButton("🗑 Удалить из базы",        callback_data="emp:delete")],
        [InlineKeyboardButton("📋 Список сотрудников",    callback_data="emp:list")],
        [InlineKeyboardButton("🧹 Очистить всех сотрудников", callback_data="emp:clear")],
        [InlineKeyboardButton("🏠 Главное меню",          callback_data="nav:home")],
    ])

def kb_shifts() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отметить смену",   callback_data="shift:mark")],
        [InlineKeyboardButton("✏️ Исправить смену",  callback_data="shift:edit")],
        [InlineKeyboardButton("🏠 Главное меню",     callback_data="nav:home")],
    ])

def kb_finance() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Аванс",         callback_data="fin:advance")],
        [InlineKeyboardButton("📉 Удержание",     callback_data="fin:deduction")],
        [InlineKeyboardButton("🏠 Главное меню",  callback_data="nav:home")],
    ])

def kb_table() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Создать / пересобрать таблицу", callback_data="table:build")],
        [InlineKeyboardButton("🧹 Очистить таблицу",              callback_data="table:clear")],
        [InlineKeyboardButton("🏠 Главное меню",                   callback_data="nav:home")],
    ])

def kb_sections() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(SECTION_LABELS[s], callback_data=f"sec:{s}")]
        for s in SECTIONS
    ]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def kb_schedules(section: str) -> InlineKeyboardMarkup:
    mapping = {
        "admins":      ["2/2"],
        "waiters_day": ["5/2"],
        "waiters_eve": ["5/2"],
        "runners":     ["свободный"],
        "tech":        ["2/2", "7/0"],
    }
    scheds = mapping.get(section, ["2/2", "5/2", "7/0", "свободный"])
    buttons = [
        [InlineKeyboardButton(s, callback_data=f"sch:{s}")]
        for s in scheds
    ]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def kb_days_off(selected: list) -> InlineKeyboardMarkup:
    names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    row = [
        InlineKeyboardButton(
            f"{'✅' if i in selected else ''}{names[i]}",
            callback_data=f"doff:{i}"
        )
        for i in range(7)
    ]
    return InlineKeyboardMarkup([
        row[:4], row[4:],
        [
            InlineKeyboardButton("✔️ Готово", callback_data="doff:done"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
        ],
    ])

def kb_employees_list(employees: list, prefix: str,
                       skip_replacements: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    for e in employees:
        if skip_replacements and e.get("is_replacement_for"):
            continue
        name = e["name"]
        if e.get("fired"):
            name = f"🚫 {name}"
        buttons.append([InlineKeyboardButton(name, callback_data=f"{prefix}:{e['id']}")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def kb_day_picker(year: int, month: int) -> InlineKeyboardMarkup:
    total = days_in_month(year, month)
    buttons = []
    row = []
    for d in range(1, total + 1):
        row.append(InlineKeyboardButton(str(d), callback_data=f"day:{d}"))
        if len(row) == 7:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    t = today_tz()
    if t.year == year and t.month == month:
        buttons.append([
            InlineKeyboardButton(f"📅 Сегодня ({t.day})", callback_data=f"day:{t.day}")
        ])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)

def kb_shift_values(section: str) -> InlineKeyboardMarkup:
    if section == "runners":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
        ]])
    if section == "tech":
        rows = [
            [InlineKeyboardButton("1 — вышел",    callback_data="val:1")],
            [InlineKeyboardButton("0 — не вышел", callback_data="val:0")],
        ]
    else:
        rows = [
            [InlineKeyboardButton("1 — полная смена",  callback_data="val:1")],
            [InlineKeyboardButton("0.7",               callback_data="val:0.7")],
            [InlineKeyboardButton("0.5",               callback_data="val:0.5")],
            [InlineKeyboardButton("0.3",               callback_data="val:0.3")],
            [InlineKeyboardButton("0 — не вышел",      callback_data="val:0")],
        ]
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)

def kb_yes_no(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data=yes_data),
        InlineKeyboardButton("❌ Нет", callback_data=no_data),
    ]])

def kb_edit_fields() -> InlineKeyboardMarkup:
    fields = [
        ("ФИО",        "name"),
        ("Телефон",    "phone"),
        ("Должность",  "position"),
        ("График",     "schedule"),
        ("Выходные",   "days_off"),
        ("Дата старта (2/2)", "start_date"),
    ]
    buttons = [[InlineKeyboardButton(label, callback_data=f"field:{key}")]
               for label, key in fields]
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(buttons)


# ─── /start ───────────────────────────────────────────────────────────────────

def _main_menu_text() -> str:
    t = today_tz()
    return (f"🏠 <b>Главное меню</b>\n"
            f"📅 {t.day} {MONTH_NAMES_RU[t.month]} {t.year}")

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
    return ConversationHandler.END

async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "❌ Отменено.\n" + _main_menu_text(),
        parse_mode="HTML", reply_markup=kb_main()
    )
    return ConversationHandler.END

async def cb_nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «🏠 Главное меню» из любого места."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(_main_menu_text(), parse_mode="HTML", reply_markup=kb_main())


# ─── Главное меню (callback) ──────────────────────────────────────────────────

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
            "💰 <b>Финансы</b>\nАванс или удержание для сотрудника:",
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


# ════════════════════════════════════════════════════════════════════
#  ДОБАВЛЕНИЕ СОТРУДНИКА
# ════════════════════════════════════════════════════════════════════

async def conv_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.edit_message_text(
        "👥 Сотрудники › ➕ Добавить\n\nШаг 1/5 — Введите ФИО сотрудника:",
        reply_markup=kb_cancel()
    )
    return ADD_NAME

async def conv_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ ФИО: <b>{context.user_data['name']}</b>\n\nШаг 2/5 — Введите номер телефона (или пропустите):",
        parse_mode="HTML", reply_markup=kb_skip_cancel()
    )
    return ADD_PHONE

async def conv_add_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 3/5 — Введите должность (например: «Официант», «Кассир», «Администратор»):",
        reply_markup=kb_cancel()
    )
    return ADD_POSITION

async def conv_add_phone_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = ""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Шаг 3/5 — Введите должность:",
        reply_markup=kb_cancel()
    )
    return ADD_POSITION

async def conv_add_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["position"] = update.message.text.strip()
    await update.message.reply_text(
        "Шаг 4/5 — Выберите раздел табеля:",
        reply_markup=kb_sections()
    )
    return ADD_SECTION

async def conv_add_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    section = q.data.split(":")[1]
    context.user_data["section"] = section
    await q.edit_message_text(
        f"✅ Раздел: <b>{SECTION_LABELS[section]}</b>\n\nШаг 5/5 — Выберите график:",
        parse_mode="HTML", reply_markup=kb_schedules(section)
    )
    return ADD_SCHEDULE

async def conv_add_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    schedule = q.data.split(":")[1]
    context.user_data["schedule"] = schedule
    context.user_data["days_off"] = []

    if schedule == "5/2":
        await q.edit_message_text(
            "Выберите выходные дни сотрудника (можно несколько), затем «Готово»:",
            reply_markup=kb_days_off([])
        )
        return ADD_DAYS_OFF
    elif schedule == "2/2":
        await q.edit_message_text(
            "Введите дату начала цикла (первый рабочий день) в формате ДД.ММ.ГГГГ:",
            reply_markup=kb_cancel()
        )
        return ADD_START_DATE
    else:
        # 7/0 или свободный — сохраняем сразу
        return await _finish_add_employee(q, context)

async def conv_add_days_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = q.data.split(":")[1]
    if val == "done":
        if not context.user_data.get("days_off"):
            await q.answer("Выберите хотя бы один выходной день!", show_alert=True)
            return ADD_DAYS_OFF
        return await _finish_add_employee(q, context)
    day_idx = int(val)
    selected = context.user_data.setdefault("days_off", [])
    if day_idx in selected:
        selected.remove(day_idx)
    else:
        selected.append(day_idx)
    await q.edit_message_text(
        "Выберите выходные дни сотрудника, затем «Готово»:",
        reply_markup=kb_days_off(selected)
    )
    return ADD_DAYS_OFF

async def conv_add_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        dt = datetime.strptime(raw, "%d.%m.%Y")
        context.user_data["start_date"] = dt.strftime("%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Неверный формат. Введите ДД.ММ.ГГГГ:",
                                        reply_markup=kb_cancel())
        return ADD_START_DATE
    return await _finish_add_employee(update.message, context)

async def _finish_add_employee(msg_or_query, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    emp = db.add_employee({
        "name":       data["name"],
        "phone":      data.get("phone", ""),
        "position":   data.get("position", ""),
        "section":    data["section"],
        "schedule":   data.get("schedule", ""),
        "days_off":   data.get("days_off", []),
        "start_date": data.get("start_date", ""),
    })
    text = (f"✅ <b>Сотрудник добавлен!</b>\n\n"
            f"👤 {emp['name']}\n"
            f"💼 {emp.get('position','—')}\n"
            f"📋 {emp.get('schedule','—')} | {SECTION_LABELS.get(emp['section'],'')}\n"
            f"📞 {emp.get('phone','—')}")
    kb = kb_home_repeat("➕ Добавить ещё", "emp:add")
    if hasattr(msg_or_query, "edit_message_text"):
        await msg_or_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await msg_or_query.reply_text(text, parse_mode="HTML", reply_markup=kb)
    context.user_data.clear()
    return ConversationHandler.END


def conv_add_employee() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_add_start, pattern="^emp:add$")],
        states={
            ADD_NAME:       [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_add_name)],
            ADD_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_add_phone),
                CallbackQueryHandler(conv_add_phone_skip, pattern="^skip$"),
            ],
            ADD_POSITION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_add_position)],
            ADD_SECTION:    [CallbackQueryHandler(conv_add_section,  pattern="^sec:")],
            ADD_SCHEDULE:   [CallbackQueryHandler(conv_add_schedule, pattern="^sch:")],
            ADD_DAYS_OFF:   [CallbackQueryHandler(conv_add_days_off, pattern="^doff:")],
            ADD_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_add_start_date)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  РЕДАКТИРОВАНИЕ СОТРУДНИКА
# ════════════════════════════════════════════════════════════════════

async def conv_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    employees = db.get_all_employees()
    if not employees:
        await q.edit_message_text("Нет сотрудников.", reply_markup=kb_main())
        return ConversationHandler.END
    await q.edit_message_text("Выберите сотрудника:",
                               reply_markup=kb_employees_list(employees, "esel"))
    return EDIT_SELECT_EMP

async def conv_edit_select_emp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    emp_id = int(q.data.split(":")[1])
    emp = db.get_employee(emp_id)
    if not emp:
        await q.edit_message_text("Сотрудник не найден.")
        return ConversationHandler.END
    context.user_data["edit_emp_id"] = emp_id
    info = (f"<b>{emp['name']}</b>\n"
            f"Телефон: {emp.get('phone','—')}\n"
            f"Должность: {emp.get('position','—')}\n"
            f"График: {emp.get('schedule','—')}\n"
            f"Выходные: {emp.get('days_off','—')}\n"
            f"Дата старта: {emp.get('start_date','—')}")
    await q.edit_message_text(f"{info}\n\nЧто изменить?",
                               parse_mode="HTML", reply_markup=kb_edit_fields())
    return EDIT_FIELD

async def conv_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = q.data.split(":")[1]
    context.user_data["edit_field"] = field

    if field == "days_off":
        emp = db.get_employee(context.user_data["edit_emp_id"])
        context.user_data["days_off"] = list(emp.get("days_off", []))
        await q.edit_message_text("Выберите новые выходные дни:",
                                   reply_markup=kb_days_off(context.user_data["days_off"]))
        return EDIT_VALUE
    elif field == "schedule":
        emp = db.get_employee(context.user_data["edit_emp_id"])
        await q.edit_message_text("Выберите новый график:",
                                   reply_markup=kb_schedules(emp.get("section", "")))
        return EDIT_VALUE
    else:
        labels = {
            "name": "ФИО", "phone": "телефон", "position": "должность",
            "start_date": "дату старта (ДД.ММ.ГГГГ)",
        }
        await q.edit_message_text(f"Введите {labels.get(field, field)}:",
                                   reply_markup=kb_cancel())
        return EDIT_VALUE

async def conv_edit_value_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get("edit_field")
    raw = update.message.text.strip()
    emp_id = context.user_data["edit_emp_id"]

    if field == "start_date":
        try:
            dt = datetime.strptime(raw, "%d.%m.%Y")
            raw = dt.strftime("%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("Неверный формат. Введите ДД.ММ.ГГГГ:",
                                            reply_markup=kb_cancel())
            return EDIT_VALUE

    db.update_employee(emp_id, {field: raw})
    emp = db.get_employee(emp_id)
    await update.message.reply_text(
        f"✅ Обновлено! <b>{emp['name']}</b>",
        parse_mode="HTML",
        reply_markup=kb_home_repeat("✏️ Редактировать ещё", "emp:edit")
    )
    context.user_data.clear()
    return ConversationHandler.END

async def conv_edit_value_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    field = context.user_data.get("edit_field")
    emp_id = context.user_data["edit_emp_id"]

    if field == "days_off":
        val = q.data.split(":")[1]
        if val == "done":
            db.update_employee(emp_id, {"days_off": context.user_data["days_off"]})
            await q.edit_message_text(
                "✅ Выходные обновлены.",
                reply_markup=kb_home_repeat("✏️ Редактировать ещё", "emp:edit")
            )
            context.user_data.clear()
            return ConversationHandler.END
        idx = int(val)
        selected = context.user_data.setdefault("days_off", [])
        if idx in selected:
            selected.remove(idx)
        else:
            selected.append(idx)
        await q.edit_message_text("Выберите новые выходные дни:",
                                   reply_markup=kb_days_off(selected))
        return EDIT_VALUE

    elif field == "schedule":
        schedule = q.data.split(":")[1]
        db.update_employee(emp_id, {"schedule": schedule})
        await q.edit_message_text(
            f"✅ График обновлён: <b>{schedule}</b>",
            parse_mode="HTML",
            reply_markup=kb_home_repeat("✏️ Редактировать ещё", "emp:edit")
        )
        context.user_data.clear()
        return ConversationHandler.END

    return EDIT_VALUE


def conv_edit_employee() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_edit_start, pattern="^emp:edit$")],
        states={
            EDIT_SELECT_EMP: [CallbackQueryHandler(conv_edit_select_emp, pattern="^esel:")],
            EDIT_FIELD:      [CallbackQueryHandler(conv_edit_field,      pattern="^field:")],
            EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_edit_value_text),
                CallbackQueryHandler(conv_edit_value_cb, pattern="^(doff:|sch:)"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  СМЕНЫ
# ════════════════════════════════════════════════════════════════════

async def conv_shift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]   # mark | edit
    context.user_data.clear()
    context.user_data["shift_action"] = action

    employees = [e for e in db.get_all_employees() if not e.get("is_replacement_for")]
    if not employees:
        await q.edit_message_text("Нет сотрудников.", reply_markup=kb_main())
        return ConversationHandler.END

    await q.edit_message_text("Выберите сотрудника:",
                               reply_markup=kb_employees_list(employees, "shsel"))
    return SHIFT_SELECT_EMP

async def conv_shift_emp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    emp_id = int(q.data.split(":")[1])
    emp = db.get_employee(emp_id)
    if not emp:
        await q.edit_message_text("Сотрудник не найден.")
        return ConversationHandler.END

    context.user_data["shift_emp_id"] = emp_id
    context.user_data["shift_emp_section"] = emp["section"]

    t = today_tz()
    await q.edit_message_text(
        f"Сотрудник: <b>{emp['name']}</b>\nВыберите день {MONTH_NAMES_RU[t.month]} {t.year}:",
        parse_mode="HTML",
        reply_markup=kb_day_picker(t.year, t.month),
    )
    return SHIFT_SELECT_DATE

async def conv_shift_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    day = int(q.data.split(":")[1])
    context.user_data["shift_day"] = day

    section = context.user_data["shift_emp_section"]
    t = today_tz()

    if section == "runners":
        await q.edit_message_text(
            f"День {day}. Введите количество часов (например: 6, 8.5, 12):",
            reply_markup=kb_cancel()
        )
        return SHIFT_SELECT_VALUE
    else:
        await q.edit_message_text(
            f"День {day}. Выберите значение смены:",
            reply_markup=kb_shift_values(section)
        )
        return SHIFT_SELECT_VALUE

async def conv_shift_value_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор значения кнопкой (не раннеры)."""
    q = update.callback_query
    await q.answer()
    value = q.data.split(":")[1]
    context.user_data["shift_value"] = value

    # Спросить о замене
    emp = db.get_employee(context.user_data["shift_emp_id"])
    await q.edit_message_text(
        f"Это замена другого сотрудника вместо <b>{emp['name']}</b>?",
        parse_mode="HTML",
        reply_markup=kb_yes_no("rep:yes", "rep:no"),
    )
    return SHIFT_IS_REPLACE

async def conv_shift_value_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод часов текстом (раннеры)."""
    raw = update.message.text.strip().replace(",", ".")
    try:
        hours = float(raw)
    except ValueError:
        await update.message.reply_text("Введите число (например 6 или 8.5):",
                                        reply_markup=kb_cancel())
        return SHIFT_SELECT_VALUE
    context.user_data["shift_value"] = hours
    # Раннеры — замены не бывает
    return await _finish_shift(update.message, context)

async def conv_shift_is_replace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    answer = q.data.split(":")[1]   # yes | no

    if answer == "no":
        return await _finish_shift(q, context)

    # Выбрать, за кого работает
    employees = [e for e in db.get_all_employees() if not e.get("is_replacement_for")]
    target_id = context.user_data["shift_emp_id"]
    others = [e for e in employees if e["id"] != target_id]
    if not others:
        await q.edit_message_text("Нет других сотрудников для замены.")
        return ConversationHandler.END

    await q.edit_message_text("Замена за кого?",
                               reply_markup=kb_employees_list(others, "repfor"))
    return SHIFT_REPLACE_FOR

async def conv_shift_replace_for(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    main_emp_id = int(q.data.split(":")[1])
    context.user_data["shift_replace_for"] = main_emp_id
    return await _finish_shift(q, context, is_replacement=True)

async def _finish_shift(msg_or_query, context: ContextTypes.DEFAULT_TYPE,
                         is_replacement: bool = False):
    ud = context.user_data
    emp_id   = ud["shift_emp_id"]
    day      = ud["shift_day"]
    value    = ud["shift_value"]
    t        = today_tz()

    emp = db.get_employee(emp_id)

    if is_replacement:
        main_emp_id = ud["shift_replace_for"]
        # Убедимся что строка замены существует в БД и листе
        existing = db.find_replacement_row(main_emp_id, emp["name"])
        if existing:
            replacer_id = existing["id"]
        else:
            # Создаём новую запись замены в БД
            rep_data = {
                "name":               emp["name"],
                "phone":              emp.get("phone", ""),
                "position":           emp.get("position", ""),
                "section":            db.get_employee(main_emp_id)["section"],
                "schedule":           "",
                "is_replacement_for": main_emp_id,
            }
            new_rep = db.add_employee(rep_data)
            replacer_id = new_rep["id"]
            # Добавить строку в лист
            sheets.add_replacement_row_to_sheet(main_emp_id, replacer_id, t.year, t.month)

        ok = sheets.write_shift(replacer_id, day, value, t.year, t.month)
        main_name = db.get_employee(main_emp_id)["name"]
        result_text = (f"✅ Записано: {emp['name']} (замена за {main_name}) | "
                       f"день {day} = {value}")
    else:
        ok = sheets.write_shift(emp_id, day, value, t.year, t.month)
        result_text = f"✅ Записано: {emp['name']} | день {day} = {value}"

    if not ok:
        result_text = ("⚠️ Строка сотрудника не найдена в таблице.\n"
                       "Сначала создайте таблицу через меню 📊.")

    kb = kb_home_repeat("📅 Отметить ещё", "shift:mark")
    if hasattr(msg_or_query, "edit_message_text"):
        await msg_or_query.edit_message_text(result_text, reply_markup=kb)
    else:
        await msg_or_query.reply_text(result_text, reply_markup=kb)

    context.user_data.clear()
    return ConversationHandler.END


def conv_shifts() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(conv_shift_start, pattern="^shift:(mark|edit)$"),
        ],
        states={
            SHIFT_SELECT_EMP: [
                CallbackQueryHandler(conv_shift_emp, pattern="^shsel:"),
            ],
            SHIFT_SELECT_DATE: [
                CallbackQueryHandler(conv_shift_date, pattern="^day:"),
            ],
            SHIFT_SELECT_VALUE: [
                CallbackQueryHandler(conv_shift_value_cb,  pattern="^val:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, conv_shift_value_text),
            ],
            SHIFT_IS_REPLACE: [
                CallbackQueryHandler(conv_shift_is_replace, pattern="^rep:"),
            ],
            SHIFT_REPLACE_FOR: [
                CallbackQueryHandler(conv_shift_replace_for, pattern="^repfor:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  ФИНАНСЫ
# ════════════════════════════════════════════════════════════════════

async def conv_fin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    fin_type = q.data.split(":")[1]   # advance | deduction
    context.user_data.clear()
    context.user_data["fin_type"] = fin_type

    employees = [e for e in db.get_all_employees() if not e.get("is_replacement_for")]
    if not employees:
        await q.edit_message_text("Нет сотрудников.", reply_markup=kb_main())
        return ConversationHandler.END

    label = "аванс" if fin_type == "advance" else "удержание"
    await q.edit_message_text(f"💰 {label.capitalize()}. Выберите сотрудника:",
                               reply_markup=kb_employees_list(employees, "finsel"))
    return FIN_SELECT_EMP

async def conv_fin_emp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    emp_id = int(q.data.split(":")[1])
    context.user_data["fin_emp_id"] = emp_id
    emp = db.get_employee(emp_id)
    fin_type = context.user_data["fin_type"]
    label = "аванс" if fin_type == "advance" else "удержание"
    await q.edit_message_text(
        f"Сотрудник: <b>{emp['name']}</b>\nВведите сумму ({label}):",
        parse_mode="HTML",
        reply_markup=kb_cancel()
    )
    return FIN_VALUE

async def conv_fin_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        amount = int(raw)
    except ValueError:
        await update.message.reply_text("Введите целое число (сумма в тенге):",
                                        reply_markup=kb_cancel())
        return FIN_VALUE

    emp_id   = context.user_data["fin_emp_id"]
    fin_type = context.user_data["fin_type"]
    emp      = db.get_employee(emp_id)
    t        = today_tz()

    ok = sheets.write_finance(emp_id, fin_type, amount, t.year, t.month)
    label = "Аванс" if fin_type == "advance" else "Удержание"
    kb = kb_home_repeat("💰 Ещё финансы", "menu:finance")
    if ok:
        await update.message.reply_text(
            f"✅ <b>{label}</b> для {emp['name']} = {amount:,} ₸".replace(",", " "),
            parse_mode="HTML", reply_markup=kb
        )
    else:
        await update.message.reply_text(
            "⚠️ Строка не найдена. Сначала создайте таблицу 📊.",
            reply_markup=kb_home()
        )
    context.user_data.clear()
    return ConversationHandler.END


def conv_finance() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(conv_fin_start, pattern="^fin:(advance|deduction)$"),
        ],
        states={
            FIN_SELECT_EMP: [CallbackQueryHandler(conv_fin_emp, pattern="^finsel:")],
            FIN_VALUE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_fin_value)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  УВОЛЬНЕНИЕ
# ════════════════════════════════════════════════════════════════════

async def conv_fire_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    employees = [e for e in db.get_all_employees()
                 if not e.get("fired") and not e.get("is_replacement_for")]
    if not employees:
        await q.edit_message_text("Нет активных сотрудников.")
        return ConversationHandler.END
    await q.edit_message_text("Выберите сотрудника для увольнения:",
                               reply_markup=kb_employees_list(employees, "firesel"))
    return FIRE_SELECT_EMP

async def conv_fire_emp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    emp_id = int(q.data.split(":")[1])
    context.user_data["fire_emp_id"] = emp_id
    emp = db.get_employee(emp_id)
    await q.edit_message_text(
        f"Увольнение: <b>{emp['name']}</b>\nВведите дату увольнения (ДД.ММ.ГГГГ):",
        parse_mode="HTML",
        reply_markup=kb_cancel()
    )
    return FIRE_DATE

async def conv_fire_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        datetime.strptime(raw, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text("Неверный формат. Введите ДД.ММ.ГГГГ:",
                                        reply_markup=kb_cancel())
        return FIRE_DATE

    emp_id = context.user_data["fire_emp_id"]
    emp    = db.get_employee(emp_id)
    t      = today_tz()

    db.update_employee(emp_id, {"fired": True, "fired_date": raw})
    sheets.mark_employee_fired(emp_id, raw, t.year, t.month)

    await update.message.reply_text(
        f"🚫 <b>{emp['name']}</b> уволен с {raw}.\nСтрока помечена в таблице розовым.",
        parse_mode="HTML", reply_markup=kb_home()
    )
    context.user_data.clear()
    return ConversationHandler.END


def conv_fire_employee() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_fire_start, pattern="^emp:fire$")],
        states={
            FIRE_SELECT_EMP: [CallbackQueryHandler(conv_fire_emp, pattern="^firesel:")],
            FIRE_DATE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_fire_date)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  УДАЛЕНИЕ СОТРУДНИКА
# ════════════════════════════════════════════════════════════════════

async def conv_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    employees = db.get_all_employees()
    if not employees:
        await q.edit_message_text("Нет сотрудников.")
        return ConversationHandler.END
    await q.edit_message_text(
        "⚠️ Выберите сотрудника для удаления из БД (строка в листе останется):",
        reply_markup=kb_employees_list(employees, "delsel", skip_replacements=False)
    )
    return DELETE_SELECT_EMP

async def conv_delete_emp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    emp_id = int(q.data.split(":")[1])
    emp = db.get_employee(emp_id)
    name = emp["name"] if emp else str(emp_id)
    db.delete_employee(emp_id)
    await q.edit_message_text(
        f"🗑 Сотрудник <b>{name}</b> удалён из базы.",
        parse_mode="HTML",
        reply_markup=kb_home_repeat("👥 Сотрудники", "menu:employees")
    )
    return ConversationHandler.END


def conv_delete_employee() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_delete_start, pattern="^emp:delete$")],
        states={
            DELETE_SELECT_EMP: [CallbackQueryHandler(conv_delete_emp, pattern="^delsel:")],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  ДОБАВЛЕНИЕ АДМИНИСТРАТОРА БОТА
# ════════════════════════════════════════════════════════════════════

async def conv_newadmin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Введите Telegram chat_id нового администратора бота:",
        reply_markup=kb_cancel()
    )
    return NEW_ADMIN_ID

async def conv_newadmin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        new_id = int(raw)
    except ValueError:
        await update.message.reply_text("Введите числовой chat_id:", reply_markup=kb_cancel())
        return NEW_ADMIN_ID
    db.add_bot_admin(new_id)
    await update.message.reply_text(
        f"✅ Администратор <code>{new_id}</code> добавлен.",
        parse_mode="HTML",
        reply_markup=kb_home()
    )
    return ConversationHandler.END


def conv_new_admin() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_newadmin_start, pattern="^settings:add_admin$")],
        states={
            NEW_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_newadmin_id)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ════════════════════════════════════════════════════════════════════
#  ОЧИСТКА ВСЕХ СОТРУДНИКОВ
# ════════════════════════════════════════════════════════════════════

async def cb_clear_employees_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос подтверждения перед удалением всех сотрудников."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "⚠️ <b>Внимание!</b>\n\n"
        "Вы собираетесь удалить <b>ВСЕХ</b> сотрудников из базы данных.\n"
        "Это действие <b>необратимо</b>!\n\n"
        "Данные в Google Sheets останутся без изменений.\n\n"
        "Продолжить?",
        parse_mode="HTML",
        reply_markup=kb_yes_no("emp:clear_yes", "menu:employees"),
    )


async def cb_clear_employees_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнить удаление всех сотрудников."""
    q = update.callback_query
    await q.answer()
    db.clear_all_employees()
    await q.edit_message_text(
        "✅ Все сотрудники удалены из базы данных.",
        reply_markup=kb_home_repeat("👥 Сотрудники", "menu:employees"),
    )


# ════════════════════════════════════════════════════════════════════
#  ТАБЛИЦА
# ════════════════════════════════════════════════════════════════════

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
            f"⚠️ <b>Внимание!</b>\n\n"
            f"Вы собираетесь <b>удалить лист</b> «{month_label(t.year, t.month)}» из Google Sheets.\n"
            f"Все данные за этот месяц будут потеряны.\n\n"
            f"Продолжить?",
            parse_mode="HTML",
            reply_markup=kb_yes_no("table:clear_yes", "menu:table"),
        )

    elif action == "clear_yes":
        t = today_tz()
        await q.edit_message_text(f"⏳ Удаляю лист «{month_label(t.year, t.month)}»…")
        try:
            deleted = sheets.delete_sheet(t.year, t.month)
            if deleted:
                await q.edit_message_text(
                    f"✅ Лист «{month_label(t.year, t.month)}» удалён из Google Sheets.",
                    reply_markup=kb_home_repeat("📊 Таблица", "menu:table"),
                )
            else:
                await q.edit_message_text(
                    f"ℹ️ Лист «{month_label(t.year, t.month)}» не найден.",
                    reply_markup=kb_home_repeat("📊 Таблица", "menu:table"),
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


# ════════════════════════════════════════════════════════════════════
#  НАПОМИНАНИЯ и АВТО-СОЗДАНИЕ ЛИСТА
# ════════════════════════════════════════════════════════════════════

async def _send_reminder(bot, text: str):
    targets = [ADMIN_CHAT_ID] + db.get_bot_admins()
    for chat_id in targets:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            pass

async def morning_reminder(bot):
    t = today_tz()
    await _send_reminder(bot, f"☀️ Доброе утро! Не забудьте отметить утренние смены за {t.day} {MONTH_NAMES_RU[t.month]}.")

async def evening_reminder(bot):
    t = today_tz()
    await _send_reminder(bot, f"🌙 Добрый вечер! Не забудьте отметить вечерние смены за {t.day} {MONTH_NAMES_RU[t.month]}.")

async def auto_create_sheet(bot):
    t = today_tz()
    try:
        sheets.build_sheet(t.year, t.month)
        await _send_reminder(bot, f"📊 Автоматически создан новый лист табеля: {month_label(t.year, t.month)}")
    except Exception as e:
        logger.exception("auto_create_sheet error")
        await _send_reminder(bot, f"❌ Ошибка авто-создания листа: {e}")


# ════════════════════════════════════════════════════════════════════
#  СБОРКА ПРИЛОЖЕНИЯ
# ════════════════════════════════════════════════════════════════════

def setup_handlers(app: Application):
    # Конверсейшн-хэндлеры (важен порядок — специфичные раньше общих)
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

    # Очистка сотрудников
    app.add_handler(CallbackQueryHandler(cb_clear_employees_confirm, pattern="^emp:clear$"))
    app.add_handler(CallbackQueryHandler(cb_clear_employees_execute, pattern="^emp:clear_yes$"))

    # Таблица
    app.add_handler(CallbackQueryHandler(cb_table_action, pattern="^table:"))

    # Отмена вне конверсейшнов
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern="^cancel$"))


# ════════════════════════════════════════════════════════════════════
#  FastAPI + lifespan
# ════════════════════════════════════════════════════════════════════

ptb_app: Application = None   # type: ignore
scheduler: AsyncIOScheduler = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app, scheduler

    db.init_db()

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

    # APScheduler
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
