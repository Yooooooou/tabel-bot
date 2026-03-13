"""Handlers: аванс, удержание, процент официанта."""
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters,
)

import database as db
import sheets
from bot_utils import today_tz
from keyboards import kb_cancel, kb_home, kb_home_repeat, kb_employees_list, kb_main
from states import FIN_SELECT_EMP, FIN_VALUE

# Типы финансовых операций
FIN_TYPES = {
    "advance":   ("Аванс",      "аванс"),
    "deduction": ("Удержание",  "удержание"),
    "percent":   ("Процент",    "процент"),
}


async def conv_fin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    fin_type = q.data.split(":")[1]
    context.user_data.clear()
    context.user_data["fin_type"] = fin_type

    employees = [e for e in db.get_all_employees() if not e.get("is_replacement_for")]
    if not employees:
        await q.edit_message_text("Нет сотрудников.", reply_markup=kb_main())
        return ConversationHandler.END

    _, label_lower = FIN_TYPES[fin_type]
    await q.edit_message_text(f"💰 {label_lower.capitalize()}. Выберите сотрудника:",
                               reply_markup=kb_employees_list(employees, "finsel"))
    return FIN_SELECT_EMP


async def conv_fin_emp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    emp_id = int(q.data.split(":")[1])
    context.user_data["fin_emp_id"] = emp_id
    emp = db.get_employee(emp_id)
    fin_type = context.user_data["fin_type"]
    _, label_lower = FIN_TYPES[fin_type]

    hint = " (например: 15 для 15%)" if fin_type == "percent" else " (сумма в тенге)"
    await q.edit_message_text(
        f"Сотрудник: <b>{emp['name']}</b>\nВведите {label_lower}{hint}:",
        parse_mode="HTML",
        reply_markup=kb_cancel()
    )
    return FIN_VALUE


async def conv_fin_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(",", ".")
    fin_type = context.user_data["fin_type"]

    emp_id = context.user_data["fin_emp_id"]
    emp    = db.get_employee(emp_id)
    t      = today_tz()

    if fin_type == "percent":
        try:
            pct_val = float(raw)
        except ValueError:
            await update.message.reply_text("Введите число (например 6 для 6%):",
                                            reply_markup=kb_cancel())
            return FIN_VALUE
        percent_str = f"{pct_val:g}%"
        db.update_employee(emp_id, {"percent": percent_str})
        ok = sheets.write_employee_percent(emp_id, percent_str, t.year, t.month)
        display = percent_str
    else:
        try:
            value = int(raw)
            display = f"{value:,} ₸".replace(",", " ")
        except ValueError:
            await update.message.reply_text("Введите целое число (сумма в тенге):",
                                            reply_markup=kb_cancel())
            return FIN_VALUE
        ok = sheets.write_finance(emp_id, fin_type, value, t.year, t.month)

    label_upper, _ = FIN_TYPES[fin_type]
    kb = kb_home_repeat("💰 Ещё финансы", "menu:finance")
    if ok:
        await update.message.reply_text(
            f"✅ <b>{label_upper}</b> для {emp['name']} = {display}",
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
            CallbackQueryHandler(conv_fin_start,
                                 pattern="^fin:(advance|deduction|percent)$"),
        ],
        states={
            FIN_SELECT_EMP: [CallbackQueryHandler(conv_fin_emp, pattern="^finsel:")],
            FIN_VALUE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_fin_value)],
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
