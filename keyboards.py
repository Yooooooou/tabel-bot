"""Все Inline-клавиатуры бота."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import SECTIONS, SECTION_LABELS
from schedule import days_in_month


# ─── Утилита дата ─────────────────────────────────────────────────────────────

def _today():
    from bot_utils import today_tz
    return today_tz()


# ─── Общие ────────────────────────────────────────────────────────────────────

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


# ─── Разделы ──────────────────────────────────────────────────────────────────

def kb_employees() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить сотрудника",       callback_data="emp:add")],
        [InlineKeyboardButton("✏️ Редактировать",              callback_data="emp:edit")],
        [InlineKeyboardButton("🔥 Уволить",                    callback_data="emp:fire")],
        [InlineKeyboardButton("🗑 Удалить из базы",             callback_data="emp:delete")],
        [InlineKeyboardButton("📋 Список сотрудников",         callback_data="emp:list")],
        [InlineKeyboardButton("🧹 Очистить всех сотрудников",  callback_data="emp:clear")],
        [InlineKeyboardButton("🏠 Главное меню",               callback_data="nav:home")],
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
        [InlineKeyboardButton("💹 Процент",       callback_data="fin:percent")],
        [InlineKeyboardButton("🏠 Главное меню",  callback_data="nav:home")],
    ])


def kb_table() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Создать / пересобрать таблицу", callback_data="table:build")],
        [InlineKeyboardButton("🧹 Очистить таблицу",              callback_data="table:clear")],
        [InlineKeyboardButton("🏠 Главное меню",                   callback_data="nav:home")],
    ])


# ─── Выбор из списка ──────────────────────────────────────────────────────────

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
    t = _today()
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
