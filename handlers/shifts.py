"""Handlers: отметка и исправление смен."""
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters,
)

import database as db
import sheets
from bot_utils import today_tz
from config import MONTH_NAMES_RU
from keyboards import (
    kb_cancel, kb_home_repeat, kb_home,
    kb_employees_list, kb_day_picker, kb_shift_values, kb_yes_no, kb_main,
)
from states import (
    SHIFT_SELECT_EMP, SHIFT_SELECT_DATE, SHIFT_SELECT_VALUE,
    SHIFT_IS_REPLACE, SHIFT_REPLACE_FOR,
)


async def conv_shift_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]
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
    return await _finish_shift(update.message, context)


async def conv_shift_is_replace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    answer = q.data.split(":")[1]

    if answer == "no":
        return await _finish_shift(q, context)

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
    emp_id = ud["shift_emp_id"]
    day    = ud["shift_day"]
    value  = ud["shift_value"]
    t      = today_tz()

    emp = db.get_employee(emp_id)

    if is_replacement:
        main_emp_id = ud["shift_replace_for"]
        existing = db.find_replacement_row(main_emp_id, emp["name"])
        if existing:
            replacer_id = existing["id"]
        else:
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
            CommandHandler("cancel", _cmd_cancel),
            CallbackQueryHandler(_cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(_cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


async def _cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cmd_cancel
    return await cmd_cancel(update, context)


async def _cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cb_cancel
    return await cb_cancel(update, context)


async def _cb_nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cb_nav_home
    return await cb_nav_home(update, context)
