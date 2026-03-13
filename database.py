"""
Простая JSON база данных для хранения сотрудников и настроек.
Файл db.json создаётся рядом с ботом.
"""
import json
import os
from typing import Optional

DB_FILE = os.environ.get("DB_PATH", "db.json")

DEFAULT_DB = {
    "employees": [],   # список сотрудников
    "admins": [],      # Telegram chat_id дополнительных администраторов
    "settings": {},
}


def _load() -> dict:
    if not os.path.exists(DB_FILE):
        _save(DEFAULT_DB.copy())
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Сотрудники ───────────────────────────────────────────────────────────────

def get_all_employees() -> list:
    return _load()["employees"]


def get_employee(emp_id: int) -> Optional[dict]:
    for e in get_all_employees():
        if e["id"] == emp_id:
            return e
    return None


def add_employee(data: dict) -> dict:
    db = _load()
    existing_ids = [e["id"] for e in db["employees"]]
    new_id = max(existing_ids, default=0) + 1
    employee = {
        "id": new_id,
        "name": data["name"],
        "phone": data.get("phone", ""),
        "position": data.get("position", ""),
        "section": data["section"],          # admins / waiters_day / waiters_eve / runners / tech
        "schedule": data.get("schedule", "2/2"),  # 2/2 / 5/2 / 7/0 / свободный
        "days_off": data.get("days_off", []),     # для 5/2: [5,6] = сб,вс (0=пн..6=вс)
        "start_date": data.get("start_date", ""),  # для 2/2: "2026-01-01"
        "plan_shifts": data.get("plan_shifts", None),  # None = авторасчёт
        "fired": data.get("fired", False),
        "fired_date": data.get("fired_date", ""),
        "is_replacement_for": data.get("is_replacement_for", None),  # id основного сотрудника
        "sort_order": data.get("sort_order", new_id),
    }
    db["employees"].append(employee)
    _save(db)
    return employee


def update_employee(emp_id: int, updates: dict) -> bool:
    db = _load()
    for e in db["employees"]:
        if e["id"] == emp_id:
            e.update(updates)
            _save(db)
            return True
    return False


def delete_employee(emp_id: int) -> bool:
    db = _load()
    before = len(db["employees"])
    db["employees"] = [e for e in db["employees"] if e["id"] != emp_id]
    if len(db["employees"]) < before:
        _save(db)
        return True
    return False


def get_employees_by_section(section: str) -> list:
    return [e for e in get_all_employees() if e["section"] == section]


def find_replacement_row(main_emp_id: int, replacer_name: str) -> Optional[dict]:
    """Найти уже существующую строку замены."""
    for e in get_all_employees():
        if e.get("is_replacement_for") == main_emp_id and e["name"] == replacer_name:
            return e
    return None


# ─── Администраторы бота ──────────────────────────────────────────────────────

def get_bot_admins() -> list:
    return _load().get("admins", [])


def add_bot_admin(chat_id: int):
    db = _load()
    if chat_id not in db["admins"]:
        db["admins"].append(chat_id)
        _save(db)


def remove_bot_admin(chat_id: int):
    db = _load()
    db["admins"] = [a for a in db["admins"] if a != chat_id]
    _save(db)


def clear_all_employees():
    """Удалить всех сотрудников из базы."""
    db = _load()
    db["employees"] = []
    _save(db)
