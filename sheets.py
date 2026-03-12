"""
Google Sheets: создание и обновление табеля.
Структура листа точно повторяет шаблон из примера.
"""
import json
import os
import calendar
from datetime import date, datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

from config import (SPREADSHEET_ID, GOOGLE_CREDS_JSON, MONTH_NAMES_RU,
                    WEEKDAYS_RU, SECTION_LABELS, SECTIONS)
from database import get_all_employees, get_employees_by_section
from schedule import calc_plan_shifts, weekday_name_ru, days_in_month

# Кэш подключения
_gc = None
_spreadsheet = None


def _get_spreadsheet():
    global _gc, _spreadsheet
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.path.exists(GOOGLE_CREDS_JSON):
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_JSON, scopes=scopes)
    else:
        info = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=scopes)

    _gc = gspread.authorize(creds)
    _spreadsheet = _gc.open_by_key(SPREADSHEET_ID)
    return _spreadsheet


def get_or_create_sheet(year: int, month: int):
    """Получить или создать лист для данного месяца."""
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"
    sp = _get_spreadsheet()
    try:
        return sp.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = sp.add_worksheet(title=sheet_name, rows=200, cols=40)
        return ws


def col_letter(n: int) -> str:
    """Индекс колонки (1-based) → буква(ы). 1=A, 7=G, 33=AG"""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def day_col(day: int) -> str:
    """День месяца → буква колонки. День 1 = колонка G (7)."""
    return col_letter(6 + day)


# ─── Построение табеля ────────────────────────────────────────────────────────

# Структура листа:
# Строка 1: заголовок "Н8" (или название)
# Строка 2-3: пусто
# Для каждого раздела:
#   - строка "Подразделение: ..."  (кроме первого раздела)
#   - строка заголовков (ФИО, Номер, ...)
#   - строка дней недели
#   - строки сотрудников
#   - итоговая строка
#   - пустая строка-разделитель

def build_sheet(year: int, month: int):
    """Полностью пересобрать лист табеля (вызывается при создании месяца)."""
    ws = get_or_create_sheet(year, month)
    employees = get_all_employees()
    total_days = days_in_month(year, month)
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"

    # Очищаем лист
    ws.clear()

    # Форматирование
    all_data = []   # список строк для batch update

    current_row = 1

    # Заголовок таблицы
    header_row = [""] * (6 + total_days + 2)
    header_row[9] = sheet_name
    all_data.append(header_row)
    current_row += 1

    # Пустые строки
    all_data.append([""] * (6 + total_days + 2))
    all_data.append([""] * (6 + total_days + 2))
    current_row += 2

    # Хранит {section: {emp_id: row_number}} для последующих обновлений
    row_map = {}

    for section in SECTIONS:
        section_employees = [e for e in employees if e["section"] == section]
        if not section_employees and section != "admins":
            continue

        row_map[section] = {}

        # Подзаголовок раздела (кроме первого)
        if section != SECTIONS[0]:
            sub_row = [""] * (6 + total_days + 2)
            sub_row[5] = f"Подразделение: {SECTION_LABELS[section]}"
            all_data.append(sub_row)
            current_row += 1
            # пустая
            all_data.append([""] * (6 + total_days + 2))
            current_row += 1

        # Строка заголовков колонок
        if section == "runners":
            col_headers = ["Ф.И.О.", "Номер", "Должность", "", "", "Кол-во отр.часов"]
        else:
            col_headers = ["Ф.И.О.", "Номер", "Должность", "график",
                           "Кол-во раб.дн", "Кол-во отр.дн"]
        col_headers += list(range(1, total_days + 1)) + ["удержание", "аванс"]
        all_data.append(col_headers)
        current_row += 1

        # Строка дней недели
        dow_row = ["", "", "", "", "", ""]
        for d in range(1, total_days + 1):
            dow_row.append(weekday_name_ru(d, year, month))
        dow_row += ["", ""]
        all_data.append(dow_row)
        current_row += 1

        # Строки сотрудников
        emp_start_row = current_row
        emp_rows = []  # (emp_id, row_number)

        for emp in section_employees:
            plan = calc_plan_shifts(emp, year, month) if section != "runners" else None
            fired_str = ""
            if emp.get("fired"):
                fired_str = f"Уволен с {emp.get('fired_date', '')}"

            if section == "runners":
                emp_row = [
                    emp["name"],
                    emp.get("phone", ""),
                    emp.get("position", "Раннер"),
                    "", "", ""
                ]
            else:
                emp_row = [
                    emp["name"],
                    emp.get("phone", ""),
                    emp.get("position", ""),
                    emp.get("schedule", ""),
                    plan if plan is not None else "",
                    f"=SUM({day_col(1)}{current_row}:{day_col(total_days)}{current_row})"
                ]

            # Ячейки дней — нули
            emp_row += [0] * total_days

            # Удержание и аванс
            emp_row += [fired_str or "", ""]

            all_data.append(emp_row)
            row_map[section][emp["id"]] = current_row
            emp_rows.append((emp["id"], current_row))
            current_row += 1

        # Итоговая строка раздела
        total_row = ["", "", "", ""]
        if section == "runners":
            sum_col = f"=SUM({day_col(1)}{emp_start_row}:{day_col(total_days)}{current_row - 1})"
            total_row = ["", "", "", "", "", sum_col]
        else:
            plan_sum = f"=SUM(E{emp_start_row}:E{current_row - 1})"
            fact_sum = f"=SUM(F{emp_start_row}:F{current_row - 1})"
            total_row = ["", "", "", "", plan_sum, fact_sum]

        for d in range(1, total_days + 1):
            col = day_col(d)
            total_row.append(f"=SUM({col}{emp_start_row}:{col}{current_row - 1})")
        total_row += ["", ""]
        all_data.append(total_row)
        current_row += 1

        # Разделитель
        all_data.append([""] * (6 + total_days + 2))
        current_row += 1

    # Записываем всё одним запросом
    ws.update("A1", all_data, value_input_option="USER_ENTERED")

    # Сохраняем маппинг строк в настройки
    _save_row_map(year, month, row_map)

    return ws, row_map


def _row_map_key(year: int, month: int) -> str:
    return f"row_map_{year}_{month}"


def _save_row_map(year: int, month: int, row_map: dict):
    """Сохранить маппинг {section: {emp_id: row}} в файл."""
    import json
    key = _row_map_key(year, month)
    cache_file = f".{key}.json"
    with open(cache_file, "w") as f:
        json.dump(row_map, f)


def _load_row_map(year: int, month: int) -> dict:
    import json
    key = _row_map_key(year, month)
    cache_file = f".{key}.json"
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return json.load(f)
    return {}


def get_employee_row(emp_id: int, year: int, month: int) -> Optional[int]:
    """Получить номер строки сотрудника в листе."""
    row_map = _load_row_map(year, month)
    for section_map in row_map.values():
        # ключи в JSON всегда строки
        if str(emp_id) in section_map:
            return section_map[str(emp_id)]
    return None


# ─── Запись значений ──────────────────────────────────────────────────────────

def write_shift(emp_id: int, day: int, value, year: int, month: int):
    """Записать значение смены в ячейку."""
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return False
    ws = get_or_create_sheet(year, month)
    cell = f"{day_col(day)}{row}"
    ws.update(cell, [[value]], value_input_option="USER_ENTERED")
    return True


def write_finance(emp_id: int, field: str, value, year: int, month: int):
    """
    Записать удержание или аванс.
    field = 'deduction' | 'advance'
    """
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return False
    ws = get_or_create_sheet(year, month)
    total_days = days_in_month(year, month)
    # удержание = колонка после последнего дня, аванс = +1
    if field == "deduction":
        col = col_letter(6 + total_days + 1)
    else:
        col = col_letter(6 + total_days + 2)
    ws.update(f"{col}{row}", [[value]], value_input_option="USER_ENTERED")
    return True


def read_day_values(year: int, month: int, day: int) -> dict:
    """Прочитать все значения за день. Возвращает {emp_id: value}."""
    row_map = _load_row_map(year, month)
    ws = get_or_create_sheet(year, month)
    col = day_col(day)

    # Собираем все строки одним запросом
    result = {}
    all_rows = []
    emp_ids = []
    for section_map in row_map.values():
        for eid, row in section_map.items():
            all_rows.append(row)
            emp_ids.append(int(eid))

    if not all_rows:
        return {}

    # Читаем всю колонку за раз
    try:
        col_data = ws.col_values(6 + day)  # 1-based, day=1 → col 7
        for eid, row in zip(emp_ids, all_rows):
            if row - 1 < len(col_data):
                raw = col_data[row - 1]
                try:
                    result[eid] = float(raw) if raw else 0
                except ValueError:
                    result[eid] = raw
    except Exception:
        pass

    return result


def mark_employee_fired(emp_id: int, fired_date: str, year: int, month: int):
    """Поставить отметку об увольнении в колонку удержания."""
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return False
    ws = get_or_create_sheet(year, month)
    total_days = days_in_month(year, month)
    col = col_letter(6 + total_days + 1)
    ws.update(f"{col}{row}", [[f"Уволен с {fired_date}"]], value_input_option="USER_ENTERED")
    return True


def add_replacement_row(main_emp_id: int, replacer_emp_id: int,
                        day: int, value, year: int, month: int):
    """
    Добавить строку замены под основным сотрудником.
    Если строка уже есть — просто записать значение.
    """
    # Пока используем write_shift для строки заменяющего
    return write_shift(replacer_emp_id, day, value, year, month)


def export_to_xlsx(year: int, month: int) -> str:
    """
    Экспортировать лист Google Sheets в .xlsx файл.
    Возвращает путь к файлу.
    """
    import requests
    sp = _get_spreadsheet()
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"

    # Получить gid листа
    try:
        ws = sp.worksheet(sheet_name)
        gid = ws.id
    except Exception:
        return None

    # Экспорт через Google Sheets API
    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export"
        f"?format=xlsx&gid={gid}"
    )

    # Получить токен из credentials
    creds_json = GOOGLE_CREDS_JSON
    if os.path.exists(creds_json):
        with open(creds_json) as f:
            info = json.load(f)
    else:
        info = json.loads(creds_json)

    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    creds.refresh(google.auth.transport.requests.Request())

    headers = {"Authorization": f"Bearer {creds.token}"}
    resp = requests.get(url, headers=headers)

    if resp.status_code == 200:
        filename = f"Табель_{sheet_name.replace(' ', '_')}.xlsx"
        with open(filename, "wb") as f:
            f.write(resp.content)
        return filename

    return None
