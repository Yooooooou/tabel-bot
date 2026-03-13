"""Handlers: добавление, редактирование, увольнение, удаление сотрудников."""
from datetime import datetime

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters,
)

import database as db
import sheets
from bot_utils import today_tz
from config import SECTION_LABELS
from keyboards import (
    kb_cancel, kb_skip_cancel, kb_home, kb_home_repeat,
    kb_sections, kb_schedules, kb_days_off, kb_employees_list,
    kb_edit_fields, kb_main,
)
from states import (
    ADD_NAME, ADD_PHONE, ADD_POSITION, ADD_SECTION,
    ADD_SCHEDULE, ADD_DAYS_OFF, ADD_START_DATE,
    EDIT_SELECT_EMP, EDIT_FIELD, EDIT_VALUE,
    FIRE_SELECT_EMP, FIRE_DATE,
    DELETE_SELECT_EMP,
)


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
            CommandHandler("cancel", _cmd_cancel),
            CallbackQueryHandler(_cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(_cb_nav_home, pattern="^nav:home$"),
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
            CommandHandler("cancel", _cmd_cancel),
            CallbackQueryHandler(_cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(_cb_nav_home, pattern="^nav:home$"),
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
            CommandHandler("cancel", _cmd_cancel),
            CallbackQueryHandler(_cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(_cb_nav_home, pattern="^nav:home$"),
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
            CommandHandler("cancel", _cmd_cancel),
            CallbackQueryHandler(_cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(_cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


# ─── Shared fallback helpers (imported from bot at runtime to avoid circular) ─

async def _cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cmd_cancel
    return await cmd_cancel(update, context)


async def _cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cb_cancel
    return await cb_cancel(update, context)


async def _cb_nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cb_nav_home
    return await cb_nav_home(update, context)
