"""
База данных сотрудников и настроек.
Использует PostgreSQL если задан DATABASE_URL, иначе — JSON-файл.
При первом запуске с PostgreSQL автоматически мигрирует данные из db.json.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

DB_FILE = os.environ.get("DB_PATH", "db.json")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

DEFAULT_DB = {
    "employees": [],
    "admins": [],
    "settings": {},
}

# ─── Определяем бэкенд ────────────────────────────────────────────────────────

_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    try:
        import psycopg2
        import psycopg2.extras
        logger.info("PostgreSQL backend selected")
    except ImportError:
        logger.warning("psycopg2 не установлен — откат на JSON")
        _USE_PG = False


# ══════════════════════════════════════════════════════════════════════════════
#  JSON бэкенд
# ══════════════════════════════════════════════════════════════════════════════

def _load() -> dict:
    if not os.path.exists(DB_FILE):
        _save(DEFAULT_DB.copy())
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  PostgreSQL бэкенд
# ══════════════════════════════════════════════════════════════════════════════

def _pg_conn():
    return psycopg2.connect(DATABASE_URL)


def _pg_init():
    """Создаёт таблицы и мигрирует данные из JSON если есть."""
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id           SERIAL PRIMARY KEY,
                    data         JSONB NOT NULL
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    chat_id BIGINT PRIMARY KEY
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            cur.execute("SELECT COUNT(*) FROM employees")
            count = cur.fetchone()[0]
        conn.commit()

    # Миграция из JSON если таблица пустая и файл существует
    if count == 0 and os.path.exists(DB_FILE):
        logger.info("Миграция данных из db.json в PostgreSQL…")
        data = _load()
        for emp in data.get("employees", []):
            _pg_add_employee_raw(emp)
        for admin in data.get("admins", []):
            _pg_add_admin_raw(admin)
        logger.info("Миграция завершена")


def _pg_add_employee_raw(emp: dict):
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO employees (id, data) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (emp["id"], json.dumps(emp, ensure_ascii=False))
            )
        conn.commit()


def _pg_add_admin_raw(chat_id: int):
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO admins (chat_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (chat_id,)
            )
        conn.commit()


def _pg_get_all_employees() -> list:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM employees ORDER BY (data->>'id')::int")
            rows = cur.fetchall()
    return [r[0] for r in rows]


def _pg_get_employee(emp_id: int) -> Optional[dict]:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM employees WHERE id = %s", (emp_id,))
            row = cur.fetchone()
    return row[0] if row else None


def _pg_add_employee(data: dict) -> dict:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id), 0) FROM employees")
            max_id = cur.fetchone()[0]
            new_id = max_id + 1
            employee = _build_employee(new_id, data)
            cur.execute(
                "INSERT INTO employees (id, data) VALUES (%s, %s)",
                (new_id, json.dumps(employee, ensure_ascii=False))
            )
        conn.commit()
    return employee


def _pg_update_employee(emp_id: int, updates: dict) -> bool:
    emp = _pg_get_employee(emp_id)
    if emp is None:
        return False
    emp.update(updates)
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE employees SET data = %s WHERE id = %s",
                (json.dumps(emp, ensure_ascii=False), emp_id)
            )
        conn.commit()
    return True


def _pg_delete_employee(emp_id: int) -> bool:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM employees WHERE id = %s", (emp_id,))
            deleted = cur.rowcount
        conn.commit()
    return deleted > 0


def _pg_get_admins() -> list:
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM admins")
            rows = cur.fetchall()
    return [r[0] for r in rows]


def _pg_add_admin(chat_id: int):
    _pg_add_admin_raw(chat_id)


def _pg_remove_admin(chat_id: int):
    with _pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM admins WHERE chat_id = %s", (chat_id,))
        conn.commit()


# ──── Инициализация PostgreSQL при импорте ─────────────────────────────────

if _USE_PG:
    try:
        _pg_init()
    except Exception as e:
        logger.error(f"Ошибка инициализации PostgreSQL: {e}. Используем JSON.")
        _USE_PG = False


# ══════════════════════════════════════════════════════════════════════════════
#  Общие хелперы
# ══════════════════════════════════════════════════════════════════════════════

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
        "percent": data.get("percent", ""),
        "fired": data.get("fired", False),
        "fired_date": data.get("fired_date", ""),
        "is_replacement_for": data.get("is_replacement_for", None),
        "sort_order": data.get("sort_order", new_id),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Публичный API (единый для обоих бэкендов)
# ══════════════════════════════════════════════════════════════════════════════

# ─── Сотрудники ───────────────────────────────────────────────────────────────

def get_all_employees() -> list:
    if _USE_PG:
        return _pg_get_all_employees()
    return _load()["employees"]


def get_employee(emp_id: int) -> Optional[dict]:
    if _USE_PG:
        return _pg_get_employee(emp_id)
    for e in get_all_employees():
        if e["id"] == emp_id:
            return e
    return None


def add_employee(data: dict) -> dict:
    if _USE_PG:
        return _pg_add_employee(data)
    db = _load()
    existing_ids = [e["id"] for e in db["employees"]]
    new_id = max(existing_ids, default=0) + 1
    employee = _build_employee(new_id, data)
    db["employees"].append(employee)
    _save(db)
    return employee


def update_employee(emp_id: int, updates: dict) -> bool:
    if _USE_PG:
        return _pg_update_employee(emp_id, updates)
    db = _load()
    for e in db["employees"]:
        if e["id"] == emp_id:
            e.update(updates)
            _save(db)
            return True
    return False


def delete_employee(emp_id: int) -> bool:
    if _USE_PG:
        return _pg_delete_employee(emp_id)
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
    for e in get_all_employees():
        if e.get("is_replacement_for") == main_emp_id and e["name"] == replacer_name:
            return e
    return None


# ─── Администраторы бота ──────────────────────────────────────────────────────

def get_bot_admins() -> list:
    if _USE_PG:
        return _pg_get_admins()
    return _load().get("admins", [])


def add_bot_admin(chat_id: int):
    if _USE_PG:
        _pg_add_admin(chat_id)
        return
    db = _load()
    if chat_id not in db["admins"]:
        db["admins"].append(chat_id)
        _save(db)


def remove_bot_admin(chat_id: int):
    if _USE_PG:
        _pg_remove_admin(chat_id)
        return
    db = _load()
    db["admins"] = [a for a in db["admins"] if a != chat_id]
    _save(db)


def clear_all_employees():
    """Удалить всех сотрудников из базы."""
    if _USE_PG:
        with _pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM employees")
            conn.commit()
        return
    db = _load()
    db["employees"] = []
    _save(db)
