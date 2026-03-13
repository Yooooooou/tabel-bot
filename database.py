"""
База данных сотрудников и настроек.

Режимы хранения:
  - PostgreSQL: если задана переменная DATABASE_URL (рекомендуется для Railway/Docker)
  - JSON-файл : fallback, файл db.json рядом с ботом (данные теряются при redeploy)
"""
import json
import os
from typing import Optional

# ─── Определяем режим ─────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# JSON-режим (fallback)
DB_FILE = os.environ.get("DB_PATH", "db.json")

DEFAULT_DB = {
    "employees": [],
    "admins": [],
    "settings": {},
}


# ════════════════════════════════════════════════════════════════════════════════
#  PostgreSQL-бэкенд
# ════════════════════════════════════════════════════════════════════════════════

def _pg_conn():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    return conn


def init_db():
    """
    Создать таблицы при первом запуске.
    В JSON-режиме — просто убедиться, что файл существует.
    """
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS employees (
                        id          SERIAL PRIMARY KEY,
                        data        JSONB NOT NULL
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS bot_admins (
                        chat_id     BIGINT PRIMARY KEY
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key         TEXT PRIMARY KEY,
                        value       JSONB NOT NULL
                    );
                """)
            conn.commit()
        finally:
            conn.close()
    else:
        _json_load()   # создаст файл при отсутствии


# ════════════════════════════════════════════════════════════════════════════════
#  JSON-бэкенд (fallback)
# ════════════════════════════════════════════════════════════════════════════════

def _json_load() -> dict:
    if not os.path.exists(DB_FILE):
        _json_save(DEFAULT_DB.copy())
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _json_save(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════════════════════════
#  Публичный API — Сотрудники
# ════════════════════════════════════════════════════════════════════════════════

def get_all_employees() -> list:
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM employees ORDER BY (data->>'sort_order')::int, id;")
                return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()
    else:
        return _json_load()["employees"]


def get_employee(emp_id: int) -> Optional[dict]:
    for e in get_all_employees():
        if e["id"] == emp_id:
            return e
    return None


def add_employee(data: dict) -> dict:
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX((data->>'id')::int), 0) FROM employees;")
                max_id = cur.fetchone()[0]
                new_id = max_id + 1
                employee = _build_employee(new_id, data)
                import psycopg2.extras
                cur.execute(
                    "INSERT INTO employees (id, data) VALUES (%s, %s);",
                    (new_id, psycopg2.extras.Json(employee)),
                )
            conn.commit()
            return employee
        finally:
            conn.close()
    else:
        db = _json_load()
        existing_ids = [e["id"] for e in db["employees"]]
        new_id = max(existing_ids, default=0) + 1
        employee = _build_employee(new_id, data)
        db["employees"].append(employee)
        _json_save(db)
        return employee


def _build_employee(new_id: int, data: dict) -> dict:
    return {
        "id": new_id,
        "name": data["name"],
        "phone": data.get("phone", ""),
        "position": data.get("position", ""),
        "section": data["section"],
        "schedule": data.get("schedule", "2/2"),
        "days_off": data.get("days_off", []),
        "start_date": data.get("start_date", ""),
        "plan_shifts": data.get("plan_shifts", None),
        "fired": data.get("fired", False),
        "fired_date": data.get("fired_date", ""),
        "is_replacement_for": data.get("is_replacement_for", None),
        "sort_order": data.get("sort_order", new_id),
    }


def update_employee(emp_id: int, updates: dict) -> bool:
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT data FROM employees WHERE id = %s FOR UPDATE;", (emp_id,))
                row = cur.fetchone()
                if row is None:
                    return False
                employee = row[0]
                employee.update(updates)
                import psycopg2.extras
                cur.execute(
                    "UPDATE employees SET data = %s WHERE id = %s;",
                    (psycopg2.extras.Json(employee), emp_id),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    else:
        db = _json_load()
        for e in db["employees"]:
            if e["id"] == emp_id:
                e.update(updates)
                _json_save(db)
                return True
        return False


def delete_employee(emp_id: int) -> bool:
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM employees WHERE id = %s;", (emp_id,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            conn.close()
    else:
        db = _json_load()
        before = len(db["employees"])
        db["employees"] = [e for e in db["employees"] if e["id"] != emp_id]
        if len(db["employees"]) < before:
            _json_save(db)
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


def clear_all_employees():
    """Удалить всех сотрудников из базы."""
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM employees;")
            conn.commit()
        finally:
            conn.close()
    else:
        db = _json_load()
        db["employees"] = []
        _json_save(db)


# ════════════════════════════════════════════════════════════════════════════════
#  Публичный API — Администраторы бота
# ════════════════════════════════════════════════════════════════════════════════

def get_bot_admins() -> list:
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id FROM bot_admins;")
                return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()
    else:
        return _json_load().get("admins", [])


def add_bot_admin(chat_id: int):
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bot_admins (chat_id) VALUES (%s) ON CONFLICT DO NOTHING;",
                    (chat_id,),
                )
            conn.commit()
        finally:
            conn.close()
    else:
        db = _json_load()
        if chat_id not in db["admins"]:
            db["admins"].append(chat_id)
            _json_save(db)


def remove_bot_admin(chat_id: int):
    if DATABASE_URL:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bot_admins WHERE chat_id = %s;", (chat_id,))
            conn.commit()
        finally:
            conn.close()
    else:
        db = _json_load()
        db["admins"] = [a for a in db["admins"] if a != chat_id]
        _json_save(db)
