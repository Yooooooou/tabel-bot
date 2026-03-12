"""
Табель-бот v2 — полная версия.
Запуск: python bot.py
"""
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters
)

import config
from config import (BOT_TOKEN, ADMIN_CHAT_ID, MONTH_NAMES_RU, SECTIONS,
                    SECTION_LABELS, TIMEZONE)
import database as db
import sheets
from schedule import calc_plan_shifts, days_in_month

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TZ = ZoneInfo(TIMEZONE)

# ─── ConversationHandler states ──────────────────────────────────────────────
(
    # Добавление сотрудника
    ADD_NAME, ADD_PHONE, ADD_POSITION, ADD_SECTION,
    ADD_SCHEDULE, ADD_DAYS_OFF, ADD_START_DATE,
    # Редактирование
    EDIT_FIELD, EDIT_VALUE,
    # Смена
    SHIFT_SELECT_EMP, SHIFT_SELECT_DATE, SHIFT_SELECT_VALUE,
    SHIFT_IS_REPLACE, SHIFT_REPLACE_FOR,
    # Финансы
    FIN_SELECT_EMP, FIN_TYPE, FIN_VALUE,
    # Увольнение
    FIRE_DATE,
    # Добавление администратора
    NEW_ADMIN_ID,
) = range(20)

# Временное хранилище состояний разговора
user_data_store: dict = {}


def now_tz() -> datetime:
    return datetime.now(tz=TZ)


def is_authorized(chat_id: int) -> bool:
    if chat_id == ADMIN_CHAT_ID:
        return True
    return chat_id in db.get_bot_admins()


# ─── Главное меню ─────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отметить смены",        callback_data="menu_shift")],
        [InlineKeyboardButton("👥 Сотрудники",            callback_data="menu_employees")],
        [InlineKeyboardButton("💰 Аванс / Удержание",     callback_data="menu_finance")],
        [InlineKeyboardButton("📊 Таблица",               callback_data="menu_table")],
        [InlineKeyboardButton("⚙️ Настройки",             callback_data="menu_settings")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        await update.message.reply_text("⛔ Нет доступа.")
        return
    today = now_tz().date()
    await update.message.reply_text(
        f"📋 *Табель-бот*\n📅 {today.strftime('%d.%m.%Y')}\n\nВыберите действие:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )


async def back_to_main(query, text="Главное меню:"):
    await query.edit_message_text(
        text, reply_markup=main_menu_keyboard(), parse_mode="Markdown"
    )


# ─── Отметка смен ─────────────────────────────────────────────────────────────

async def menu_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    today = now_tz().date()

    keyboard = []
    # Кнопки последних 7 дней + возможность выбрать любой день месяца
    for i in range(7):
        d = today - timedelta(days=i)
        label = f"Сегодня {d.strftime('%d.%m')}" if i == 0 else d.strftime("%d.%m (%a)")
        keyboard.append([InlineKeyboardButton(label, callback_data=f"shift_date_{d.isoformat()}")])

    keyboard.append([InlineKeyboardButton("📅 Другой день месяца", callback_data="shift_pick_month")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])

    await query.edit_message_text(
        "✅ *Отметить смены*\nВыберите дату:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def shift_pick_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все дни текущего месяца для выбора."""
    query = update.callback_query
    await query.answer()
    today = now_tz().date()
    total = days_in_month(today.year, today.month)

    keyboard = []
    row = []
    for d in range(1, total + 1):
        dd = date(today.year, today.month, d)
        row.append(InlineKeyboardButton(str(d), callback_data=f"shift_date_{dd.isoformat()}"))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_shift")])

    await query.edit_message_text(
        f"📅 {MONTH_NAMES_RU[today.month]} {today.year} — выберите день:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def shift_select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """После выбора даты — показываем список сотрудников со статусами."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    date_str = query.data.replace("shift_date_", "")
    sel_date = date.fromisoformat(date_str)
    user_data_store[chat_id] = {"selected_date": sel_date}

    await _show_shift_employees(query, chat_id, sel_date)


async def _show_shift_employees(query, chat_id: int, sel_date: date):
    """Показать всех сотрудников с кнопками для отметки."""
    year, month, day = sel_date.year, sel_date.month, sel_date.day

    # Читаем текущие значения из Sheets
    try:
        current_vals = sheets.read_day_values(year, month, day)
    except Exception:
        current_vals = {}

    employees = db.get_all_employees()
    keyboard = []

    for section in SECTIONS:
        sec_emps = [e for e in employees if e["section"] == section and not e.get("fired")]
        if not sec_emps:
            continue

        # Заголовок раздела
        keyboard.append([InlineKeyboardButton(
            f"── {SECTION_LABELS[section]} ──", callback_data="noop"
        )])

        for emp in sec_emps:
            val = current_vals.get(emp["id"], 0)
            if val == 1:
                icon = "✅"
            elif val and val != 0:
                icon = f"🔸{val}"
            else:
                icon = "⬜"

            short_name = " ".join(emp["name"].split()[:2])
            replace_mark = " 🔄" if emp.get("is_replacement_for") else ""
            keyboard.append([InlineKeyboardButton(
                f"{icon} {short_name}{replace_mark}",
                callback_data=f"shift_emp_{emp['id']}_{sel_date.isoformat()}"
            )])

    keyboard.append([
        InlineKeyboardButton("🔄 Обновить", callback_data=f"shift_date_{sel_date.isoformat()}"),
        InlineKeyboardButton("◀️ Назад",    callback_data="menu_shift"),
    ])

    await query.edit_message_text(
        f"📅 *{sel_date.strftime('%d.%m.%Y')}* — отметьте сотрудников:\n"
        "✅ = 1  🔸 = частично  ⬜ = 0",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def shift_select_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор значения для конкретного сотрудника."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    _, _, emp_id_str, date_str = query.data.split("_", 3)
    emp_id = int(emp_id_str)
    sel_date = date.fromisoformat(date_str)
    emp = db.get_employee(emp_id)
    if not emp:
        return

    user_data_store[chat_id] = {"selected_date": sel_date, "selected_emp": emp_id}

    section = emp.get("section", "")
    name = emp["name"]

    if section == "runners":
        # Для раннеров — ввод часов текстом
        user_data_store[chat_id]["awaiting"] = "runner_hours"
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data=f"shift_date_{date_str}")]]
        await query.edit_message_text(
            f"⏱ *{name}*\n📅 {sel_date.strftime('%d.%m.%Y')}\n\n"
            "Введите количество отработанных часов (например: 8, 6.5, 12):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if section == "tech" and emp.get("position", "").lower() in ("сторож", "дворник-сторож"):
        # Сторожа — только 1 или 0
        values = [("✅ Работал (1)", 1), ("❌ Не работал (0)", 0)]
    else:
        values = [
            ("✅ Полная смена (1)",  1),
            ("🔸 0.7 смены",         0.7),
            ("🔸 0.5 смены",         0.5),
            ("🔸 0.3 смены",         0.3),
            ("❌ Не работал (0)",    0),
        ]

    keyboard = []
    for label, val in values:
        keyboard.append([InlineKeyboardButton(
            label, callback_data=f"shift_set_{emp_id}_{date_str}_{val}"
        )])

    # Кнопка "это замена"
    keyboard.append([InlineKeyboardButton(
        "🔄 Это замена другого сотрудника",
        callback_data=f"shift_is_replace_{emp_id}_{date_str}"
    )])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"shift_date_{date_str}")])

    await query.edit_message_text(
        f"👤 *{name}*\n"
        f"📅 {sel_date.strftime('%d.%m.%Y')} | {emp.get('position', '')} | {emp.get('schedule', '')}\n\n"
        "Выберите статус:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def shift_set_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Записать значение смены."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    # shift_set_{emp_id}_{date}_{value}
    emp_id = int(parts[2])
    date_str = parts[3]
    value = float(parts[4])
    sel_date = date.fromisoformat(date_str)

    try:
        ok = sheets.write_shift(emp_id, sel_date.day, value, sel_date.year, sel_date.month)
        if ok:
            emp = db.get_employee(emp_id)
            await query.answer(f"✅ Сохранено: {emp['name']} = {value}", show_alert=False)
        else:
            await query.answer("❌ Ошибка: сотрудник не найден в таблице", show_alert=True)
    except Exception as e:
        logger.error(e)
        await query.answer(f"❌ Ошибка записи: {e}", show_alert=True)

    # Вернуться к списку
    await _show_shift_employees(query, query.message.chat_id, sel_date)


async def shift_is_replace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пометить что это замена — показать кого заменяет."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    parts = query.data.split("_")
    replacer_id = int(parts[3])
    date_str = parts[4]
    sel_date = date.fromisoformat(date_str)

    # Показываем список сотрудников для выбора "кого заменяет"
    employees = [e for e in db.get_all_employees()
                 if e["id"] != replacer_id and not e.get("fired")
                 and not e.get("is_replacement_for")]

    keyboard = []
    for emp in employees:
        short = " ".join(emp["name"].split()[:2])
        keyboard.append([InlineKeyboardButton(
            f"{short} ({SECTION_LABELS.get(emp['section'], '')})",
            callback_data=f"shift_replace_for_{replacer_id}_{date_str}_{emp['id']}"
        )])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"shift_emp_{replacer_id}_{date_str}")])

    replacer = db.get_employee(replacer_id)
    await query.edit_message_text(
        f"🔄 *{replacer['name']}* заменяет кого?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def shift_replace_for(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить связь замены и записать смену."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    # shift_replace_for_{replacer_id}_{date}_{main_id}
    replacer_id = int(parts[3])
    date_str = parts[4]
    main_id = int(parts[5])
    sel_date = date.fromisoformat(date_str)

    replacer = db.get_employee(replacer_id)
    main_emp = db.get_employee(main_id)

    # Проверяем: есть ли уже строка замены этого сотрудника за этим основным
    existing = db.find_replacement_row(main_id, replacer["name"])
    if not existing:
        # Создаём строку-замену
        new_emp = db.add_employee({
            "name": replacer["name"],
            "phone": replacer.get("phone", ""),
            "position": replacer.get("position", ""),
            "section": main_emp["section"],
            "schedule": replacer.get("schedule", ""),
            "is_replacement_for": main_id,
        })
        replacer_row_id = new_emp["id"]
        # Нужно пересобрать лист чтобы добавить строку
        try:
            sheets.build_sheet(sel_date.year, sel_date.month)
        except Exception as e:
            logger.error(f"build_sheet error: {e}")
    else:
        replacer_row_id = existing["id"]

    # Записываем значение 1 в строку заменяющего
    try:
        sheets.write_shift(replacer_row_id, sel_date.day, 1, sel_date.year, sel_date.month)
        await query.answer(
            f"✅ {replacer['name']} → замена за {main_emp['name']}",
            show_alert=False
        )
    except Exception as e:
        await query.answer(f"❌ Ошибка: {e}", show_alert=True)

    await _show_shift_employees(query, query.message.chat_id, sel_date)


async def handle_runner_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода часов для раннера."""
    chat_id = update.effective_chat.id
    state = user_data_store.get(chat_id, {})

    if state.get("awaiting") != "runner_hours":
        return

    text = update.message.text.strip().replace(",", ".")
    try:
        hours = float(text)
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: 8 или 6.5")
        return

    emp_id = state["selected_emp"]
    sel_date = state["selected_date"]

    try:
        sheets.write_shift(emp_id, sel_date.day, hours, sel_date.year, sel_date.month)
        emp = db.get_employee(emp_id)
        await update.message.reply_text(
            f"✅ *{emp['name']}* — {hours} ч за {sel_date.strftime('%d.%m')} сохранено.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

    user_data_store.pop(chat_id, None)


# ─── Меню сотрудников ─────────────────────────────────────────────────────────

async def menu_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ Добавить сотрудника",    callback_data="emp_add")],
        [InlineKeyboardButton("📋 Список сотрудников",     callback_data="emp_list")],
        [InlineKeyboardButton("✏️ Редактировать",          callback_data="emp_edit_pick")],
        [InlineKeyboardButton("🔴 Уволить сотрудника",     callback_data="emp_fire_pick")],
        [InlineKeyboardButton("◀️ Назад",                  callback_data="back_main")],
    ]
    await query.edit_message_text(
        "👥 *Сотрудники*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def emp_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employees = db.get_all_employees()
    if not employees:
        await query.edit_message_text(
            "Список сотрудников пуст.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="menu_employees")
            ]])
        )
        return

    text = "👥 *Список сотрудников:*\n\n"
    for section in SECTIONS:
        sec_emps = [e for e in employees if e["section"] == section]
        if not sec_emps:
            continue
        text += f"*{SECTION_LABELS[section]}*\n"
        for e in sec_emps:
            fired = " 🔴уволен" if e.get("fired") else ""
            replace = f" (→ замена за ID{e['is_replacement_for']})" if e.get("is_replacement_for") else ""
            text += f"  • {e['name']} | {e.get('schedule','')} | {e.get('phone','')}{fired}{replace}\n"
        text += "\n"

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="menu_employees")]]
    await query.edit_message_text(text, parse_mode="Markdown",
                                   reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Добавление сотрудника (ConversationHandler) ──────────────────────────────

async def emp_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_data_store[chat_id] = {"adding_emp": {}}
    await query.edit_message_text(
        "➕ *Добавление сотрудника*\n\nВведите ФИО:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="menu_employees")
        ]])
    )
    return ADD_NAME


async def emp_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data_store[chat_id]["adding_emp"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "📱 Введите номер телефона (или напишите '-' чтобы пропустить):"
    )
    return ADD_PHONE


async def emp_add_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    phone = update.message.text.strip()
    user_data_store[chat_id]["adding_emp"]["phone"] = "" if phone == "-" else phone
    await update.message.reply_text("💼 Введите должность (например: Администратор, Кассир, Официант):")
    return ADD_POSITION


async def emp_add_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data_store[chat_id]["adding_emp"]["position"] = update.message.text.strip()

    keyboard = []
    for sec_id, sec_label in SECTION_LABELS.items():
        keyboard.append([InlineKeyboardButton(sec_label, callback_data=f"emp_section_{sec_id}")])

    await update.message.reply_text(
        "📂 Выберите раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADD_SECTION


async def emp_add_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    section = query.data.replace("emp_section_", "")
    user_data_store[chat_id]["adding_emp"]["section"] = section

    keyboard = [
        [InlineKeyboardButton("2/2",         callback_data="emp_sched_2/2")],
        [InlineKeyboardButton("5/2",         callback_data="emp_sched_5/2")],
        [InlineKeyboardButton("7/0 (каждый день)", callback_data="emp_sched_7/0")],
        [InlineKeyboardButton("Свободный",   callback_data="emp_sched_свободный")],
    ]
    await query.edit_message_text(
        "📅 Выберите график:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADD_SCHEDULE


async def emp_add_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    schedule = query.data.replace("emp_sched_", "")
    user_data_store[chat_id]["adding_emp"]["schedule"] = schedule

    if schedule == "5/2":
        keyboard = [
            [InlineKeyboardButton("Пн+Вт", callback_data="emp_doff_0,1")],
            [InlineKeyboardButton("Вт+Ср", callback_data="emp_doff_1,2")],
            [InlineKeyboardButton("Ср+Чт", callback_data="emp_doff_2,3")],
            [InlineKeyboardButton("Чт+Пт", callback_data="emp_doff_3,4")],
            [InlineKeyboardButton("Пт+Сб", callback_data="emp_doff_4,5")],
            [InlineKeyboardButton("Сб+Вс", callback_data="emp_doff_5,6")],
            [InlineKeyboardButton("Вс+Пн", callback_data="emp_doff_6,0")],
        ]
        await query.edit_message_text(
            "🗓 Выберите выходные дни (5/2):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ADD_DAYS_OFF

    elif schedule == "2/2":
        await query.edit_message_text(
            "📅 Введите стартовую рабочую дату сотрудника (первый рабочий день) в формате ДД.ММ.ГГГГ:"
        )
        return ADD_START_DATE

    else:
        # свободный или 7/0
        return await _finalize_emp_add(query, chat_id)


async def emp_add_days_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    days_off = [int(x) for x in query.data.replace("emp_doff_", "").split(",")]
    user_data_store[chat_id]["adding_emp"]["days_off"] = days_off
    return await _finalize_emp_add(query, chat_id)


async def emp_add_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    try:
        d = datetime.strptime(text, "%d.%m.%Y")
        user_data_store[chat_id]["adding_emp"]["start_date"] = d.strftime("%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Введите дату в формате ДД.ММ.ГГГГ:")
        return ADD_START_DATE

    emp_data = user_data_store[chat_id]["adding_emp"]
    emp = db.add_employee(emp_data)
    await update.message.reply_text(
        f"✅ Сотрудник *{emp['name']}* добавлен!\n\n"
        f"Не забудьте пересобрать таблицу через меню 📊 Таблица → Пересобрать.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👥 К сотрудникам", callback_data="menu_employees")
        ]])
    )
    user_data_store.pop(chat_id, None)
    return ConversationHandler.END


async def _finalize_emp_add(query_or_msg, chat_id: int):
    emp_data = user_data_store[chat_id]["adding_emp"]
    emp = db.add_employee(emp_data)

    text = (
        f"✅ Сотрудник *{emp['name']}* добавлен!\n\n"
        f"Не забудьте пересобрать таблицу через меню 📊 Таблица → Пересобрать."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👥 К сотрудникам", callback_data="menu_employees")
    ]])

    if hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await query_or_msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)

    user_data_store.pop(chat_id, None)
    return ConversationHandler.END


# ─── Увольнение ───────────────────────────────────────────────────────────────

async def emp_fire_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employees = [e for e in db.get_all_employees() if not e.get("fired")]
    if not employees:
        await query.edit_message_text(
            "Нет активных сотрудников.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="menu_employees")
            ]])
        )
        return

    keyboard = []
    for emp in employees:
        short = " ".join(emp["name"].split()[:2])
        keyboard.append([InlineKeyboardButton(short, callback_data=f"emp_fire_{emp['id']}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_employees")])

    await query.edit_message_text(
        "🔴 Выберите сотрудника для увольнения:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def emp_fire_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    emp_id = int(query.data.replace("emp_fire_", ""))
    emp = db.get_employee(emp_id)
    user_data_store[chat_id] = {"firing_emp": emp_id}

    await query.edit_message_text(
        f"🔴 Увольнение: *{emp['name']}*\n\nВведите дату увольнения (ДД.ММ.ГГГГ) или напишите 'сегодня':",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="menu_employees")
        ]])
    )
    return FIRE_DATE


async def emp_fire_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip().lower()

    if text == "сегодня":
        fired_date = now_tz().date().strftime("%d.%m.%Y")
    else:
        try:
            d = datetime.strptime(text, "%d.%m.%Y")
            fired_date = d.strftime("%d.%m.%Y")
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Введите ДД.ММ.ГГГГ или 'сегодня':")
            return FIRE_DATE

    emp_id = user_data_store[chat_id]["firing_emp"]
    emp = db.get_employee(emp_id)
    db.update_employee(emp_id, {"fired": True, "fired_date": fired_date})

    # Обновить в таблице
    today = now_tz().date()
    try:
        sheets.mark_employee_fired(emp_id, fired_date, today.year, today.month)
    except Exception as e:
        logger.error(e)

    await update.message.reply_text(
        f"✅ *{emp['name']}* уволен с {fired_date}.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👥 К сотрудникам", callback_data="menu_employees")
        ]])
    )
    user_data_store.pop(chat_id, None)
    return ConversationHandler.END


# ─── Финансы ──────────────────────────────────────────────────────────────────

async def menu_finance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    employees = [e for e in db.get_all_employees() if not e.get("fired") and not e.get("is_replacement_for")]
    keyboard = []
    for emp in employees:
        short = " ".join(emp["name"].split()[:2])
        keyboard.append([InlineKeyboardButton(short, callback_data=f"fin_emp_{emp['id']}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])

    await query.edit_message_text(
        "💰 *Аванс / Удержание*\nВыберите сотрудника:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def fin_emp_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    emp_id = int(query.data.replace("fin_emp_", ""))
    emp = db.get_employee(emp_id)
    user_data_store[chat_id] = {"fin_emp": emp_id}

    keyboard = [
        [InlineKeyboardButton("💳 Аванс",       callback_data="fin_type_advance")],
        [InlineKeyboardButton("➖ Удержание",    callback_data="fin_type_deduction")],
        [InlineKeyboardButton("◀️ Назад",        callback_data="menu_finance")],
    ]
    await query.edit_message_text(
        f"💰 *{emp['name']}*\nВыберите тип:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def fin_type_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    fin_type = query.data.replace("fin_type_", "")
    user_data_store[chat_id]["fin_type"] = fin_type
    emp = db.get_employee(user_data_store[chat_id]["fin_emp"])
    label = "аванс" if fin_type == "advance" else "удержание"

    await query.edit_message_text(
        f"💰 *{emp['name']}* — {label}\n\nВведите сумму (или текст, например '1 футболка'):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="menu_finance")
        ]])
    )
    return FIN_VALUE


async def fin_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_data_store.get(chat_id, {})
    emp_id = state.get("fin_emp")
    fin_type = state.get("fin_type")

    value = update.message.text.strip()
    today = now_tz().date()

    try:
        sheets.write_finance(emp_id, fin_type, value, today.year, today.month)
        emp = db.get_employee(emp_id)
        label = "аванс" if fin_type == "advance" else "удержание"
        await update.message.reply_text(
            f"✅ *{emp['name']}* — {label}: {value} сохранено.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ В меню", callback_data="back_main")
            ]])
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

    user_data_store.pop(chat_id, None)
    return ConversationHandler.END


# ─── Меню таблицы ─────────────────────────────────────────────────────────────

async def menu_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    today = now_tz().date()
    url = f"https://docs.google.com/spreadsheets/d/{config.SPREADSHEET_ID}"

    keyboard = [
        [InlineKeyboardButton("🔨 Создать/Пересобрать таблицу месяца",
                              callback_data="table_rebuild")],
        [InlineKeyboardButton("📥 Скачать .xlsx",
                              callback_data="table_export")],
        [InlineKeyboardButton("📊 Открыть Google Sheets", url=url)],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ]
    await query.edit_message_text(
        f"📊 *Таблица*\nТекущий месяц: *{MONTH_NAMES_RU[today.month]} {today.year}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def table_rebuild(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    today = now_tz().date()

    await query.edit_message_text(
        f"⏳ Пересобираю таблицу за {MONTH_NAMES_RU[today.month]} {today.year}...\n"
        "Это может занять 10-20 секунд."
    )

    try:
        sheets.build_sheet(today.year, today.month)
        url = f"https://docs.google.com/spreadsheets/d/{config.SPREADSHEET_ID}"
        keyboard = [
            [InlineKeyboardButton("📊 Открыть таблицу", url=url)],
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_table")],
        ]
        await query.edit_message_text(
            f"✅ Таблица за {MONTH_NAMES_RU[today.month]} {today.year} создана!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(e)
        await query.edit_message_text(
            f"❌ Ошибка при создании таблицы:\n`{e}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="menu_table")
            ]])
        )


async def table_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    today = now_tz().date()

    await query.edit_message_text("⏳ Готовлю файл Excel...")

    try:
        filename = sheets.export_to_xlsx(today.year, today.month)
        if filename and os.path.exists(filename):
            with open(filename, "rb") as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📊 Табель {MONTH_NAMES_RU[today.month]} {today.year}"
                )
            os.remove(filename)
            await query.edit_message_text(
                "✅ Файл отправлен.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ Назад", callback_data="menu_table")
                ]])
            )
        else:
            raise Exception("Файл не создан")
    except Exception as e:
        logger.error(e)
        await query.edit_message_text(
            f"❌ Ошибка экспорта: {e}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад", callback_data="menu_table")
            ]])
        )


# ─── Настройки ────────────────────────────────────────────────────────────────

async def menu_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    admins = db.get_bot_admins()
    admin_list = "\n".join([f"  • {a}" for a in admins]) if admins else "  нет дополнительных"

    keyboard = [
        [InlineKeyboardButton("➕ Добавить администратора бота", callback_data="settings_add_admin")],
        [InlineKeyboardButton("➖ Удалить администратора",       callback_data="settings_del_admin")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ]
    await query.edit_message_text(
        f"⚙️ *Настройки*\n\nДоп. администраторы бота:\n{admin_list}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def settings_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_data_store[chat_id] = {"awaiting": "new_admin_id"}

    await query.edit_message_text(
        "👤 Введите Telegram ID нового администратора:\n\n"
        "_Пользователь может узнать свой ID через @userinfobot_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="menu_settings")
        ]])
    )
    return NEW_ADMIN_ID


async def settings_new_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    try:
        new_id = int(text)
        db.add_bot_admin(new_id)
        await update.message.reply_text(
            f"✅ Администратор {new_id} добавлен.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Настройки", callback_data="menu_settings")
            ]])
        )
    except ValueError:
        await update.message.reply_text("❌ Введите числовой Telegram ID:")
        return NEW_ADMIN_ID

    user_data_store.pop(chat_id, None)
    return ConversationHandler.END


async def settings_del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    admins = db.get_bot_admins()
    if not admins:
        await query.answer("Нет дополнительных администраторов.", show_alert=True)
        return

    keyboard = []
    for a in admins:
        keyboard.append([InlineKeyboardButton(str(a), callback_data=f"del_admin_{a}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_settings")])

    await query.edit_message_text(
        "Выберите администратора для удаления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def del_admin_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    admin_id = int(query.data.replace("del_admin_", ""))
    db.remove_bot_admin(admin_id)
    await query.edit_message_text(
        f"✅ Администратор {admin_id} удалён.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("◀️ Настройки", callback_data="menu_settings")
        ]])
    )


# ─── Напоминания (jobs) ───────────────────────────────────────────────────────

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправить напоминание об отметке смен."""
    today = now_tz().date()
    admins = [ADMIN_CHAT_ID] + db.get_bot_admins()

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Отметить смены",
            callback_data=f"shift_date_{today.isoformat()}"
        )
    ]])

    text = (
        f"⏰ *Напоминание*\n"
        f"📅 {today.strftime('%d.%m.%Y')} — не забудьте отметить смены!"
    )

    for admin_id in admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить напоминание {admin_id}: {e}")


async def monthly_create_sheet(context: ContextTypes.DEFAULT_TYPE):
    """1-го числа каждого месяца создать новый лист."""
    today = now_tz().date()
    if today.day != 1:
        return
    try:
        sheets.build_sheet(today.year, today.month)
        admins = [ADMIN_CHAT_ID] + db.get_bot_admins()
        for admin_id in admins:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📋 Автоматически создан табель за *{MONTH_NAMES_RU[today.month]} {today.year}*",
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"monthly_create_sheet error: {e}")


# ─── Запуск бота ──────────────────────────────────────────────────────────────

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def back_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await back_to_main(query)


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler: добавление сотрудника
    add_emp_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(emp_add_start, pattern="^emp_add$")],
        states={
            ADD_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, emp_add_name)],
            ADD_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, emp_add_phone)],
            ADD_POSITION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, emp_add_position)],
            ADD_SECTION:   [CallbackQueryHandler(emp_add_section, pattern="^emp_section_")],
            ADD_SCHEDULE:  [CallbackQueryHandler(emp_add_schedule, pattern="^emp_sched_")],
            ADD_DAYS_OFF:  [CallbackQueryHandler(emp_add_days_off, pattern="^emp_doff_")],
            ADD_START_DATE:[MessageHandler(filters.TEXT & ~filters.COMMAND, emp_add_start_date)],
        },
        fallbacks=[CallbackQueryHandler(menu_employees, pattern="^menu_employees$")],
        per_message=False,
    )

    # ConversationHandler: увольнение
    fire_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(emp_fire_select, pattern="^emp_fire_\\d+$")],
        states={
            FIRE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, emp_fire_date)],
        },
        fallbacks=[CallbackQueryHandler(menu_employees, pattern="^menu_employees$")],
        per_message=False,
    )

    # ConversationHandler: финансы
    fin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(fin_type_select, pattern="^fin_type_")],
        states={
            FIN_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, fin_value_input)],
        },
        fallbacks=[CallbackQueryHandler(menu_finance, pattern="^menu_finance$")],
        per_message=False,
    )

    # ConversationHandler: добавление администратора
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(settings_add_admin, pattern="^settings_add_admin$")],
        states={
            NEW_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_new_admin_input)],
        },
        fallbacks=[CallbackQueryHandler(menu_settings, pattern="^menu_settings$")],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_emp_conv)
    app.add_handler(fire_conv)
    app.add_handler(fin_conv)
    app.add_handler(admin_conv)

    # Callback handlers
    app.add_handler(CallbackQueryHandler(back_main_callback,        pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(menu_shift,                pattern="^menu_shift$"))
    app.add_handler(CallbackQueryHandler(menu_employees,            pattern="^menu_employees$"))
    app.add_handler(CallbackQueryHandler(emp_list,                  pattern="^emp_list$"))
    app.add_handler(CallbackQueryHandler(emp_fire_pick,             pattern="^emp_fire_pick$"))
    app.add_handler(CallbackQueryHandler(menu_finance,              pattern="^menu_finance$"))
    app.add_handler(CallbackQueryHandler(fin_emp_select,            pattern="^fin_emp_\\d+$"))
    app.add_handler(CallbackQueryHandler(menu_table,                pattern="^menu_table$"))
    app.add_handler(CallbackQueryHandler(table_rebuild,             pattern="^table_rebuild$"))
    app.add_handler(CallbackQueryHandler(table_export,              pattern="^table_export$"))
    app.add_handler(CallbackQueryHandler(menu_settings,             pattern="^menu_settings$"))
    app.add_handler(CallbackQueryHandler(settings_del_admin,        pattern="^settings_del_admin$"))
    app.add_handler(CallbackQueryHandler(del_admin_confirm,         pattern="^del_admin_\\d+$"))
    app.add_handler(CallbackQueryHandler(shift_pick_month,          pattern="^shift_pick_month$"))
    app.add_handler(CallbackQueryHandler(shift_select_date,         pattern="^shift_date_"))
    app.add_handler(CallbackQueryHandler(shift_select_employee,     pattern="^shift_emp_"))
    app.add_handler(CallbackQueryHandler(shift_set_value,           pattern="^shift_set_"))
    app.add_handler(CallbackQueryHandler(shift_is_replace,          pattern="^shift_is_replace_"))
    app.add_handler(CallbackQueryHandler(shift_replace_for,         pattern="^shift_replace_for_"))
    app.add_handler(CallbackQueryHandler(noop_callback,             pattern="^noop$"))

    # Текстовые сообщения (для ConversationHandler не перехваченные)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_runner_hours
    ))

    # Jobs — напоминания
    job_queue = app.job_queue
    tz = ZoneInfo(TIMEZONE)
    job_queue.run_daily(send_reminder,
                        time=datetime.now(tz).replace(
                            hour=config.REMINDER_MORNING[0],
                            minute=config.REMINDER_MORNING[1],
                            second=0, microsecond=0
                        ).timetz())
    job_queue.run_daily(send_reminder,
                        time=datetime.now(tz).replace(
                            hour=config.REMINDER_EVENING[0],
                            minute=config.REMINDER_EVENING[1],
                            second=0, microsecond=0
                        ).timetz())
    # Автосоздание листа каждый день в 00:05 (проверяет: 1-е ли число)
    job_queue.run_daily(monthly_create_sheet,
                        time=datetime.now(tz).replace(
                            hour=0, minute=5, second=0, microsecond=0
                        ).timetz())

    logger.info("✅ Табель-бот запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
