"""
Google Sheets: создание и обновление табеля.
Структура листа повторяет шаблон Н8.
"""
import json
import os
import calendar
import requests
from datetime import date
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

from config import (
    SPREADSHEET_ID, GOOGLE_CREDS_JSON, MONTH_NAMES_RU,
    SECTION_LABELS, SECTION_SHEET_HEADER, SECTIONS,
)
from database import get_all_employees
from schedule import calc_plan_shifts, weekday_name_ru, days_in_month

# ─── Подключение ──────────────────────────────────────────────────────────────

def _get_spreadsheet():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    if os.path.exists(GOOGLE_CREDS_JSON):
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_JSON, scopes=scopes)
    else:
        info = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID)


def get_or_create_sheet(year: int, month: int):
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"
    sp = _get_spreadsheet()
    try:
        return sp.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = sp.add_worksheet(title=sheet_name, rows=300, cols=45)
        return ws


# ─── Колонки ──────────────────────────────────────────────────────────────────

def col_letter(n: int) -> str:
    """1-based индекс → буква(ы). 1=A, 27=AA, …"""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


# Структура колонок:
# 1=A  ФИО
# 2=B  Номер
# 3=C  Должность
# 4=D  График
# 5=E  Кол-во раб.дн (план)  / пусто для раннеров
# 6=F  Кол-во отр.дн (факт) = SUM  / Кол-во отр.часов для раннеров
# 7=G  День 1
# …
# 6+total_days = последний день
# 6+total_days+1 = Удержание
# 6+total_days+2 = Аванс

def day_col(day: int) -> str:
    return col_letter(6 + day)

def deduction_col(total_days: int) -> str:
    return col_letter(6 + total_days + 1)

def advance_col(total_days: int) -> str:
    return col_letter(6 + total_days + 2)


# ─── Построение табеля ────────────────────────────────────────────────────────

def build_sheet(year: int, month: int):
    """
    Полностью пересобрать лист табеля.
    Возвращает (worksheet, row_map).
    row_map = {emp_id: row_number}  для всех строк (включая замены).
    """
    ws = get_or_create_sheet(year, month)
    ws.clear()

    employees = get_all_employees()
    total_days = days_in_month(year, month)
    n_cols = 6 + total_days + 2  # A…Аванс

    all_data = []     # строки для batch update
    row_map = {}      # emp_id → номер строки (1-based)
    current_row = 1

    # ── Заголовок листа ──
    title_row = [""] * n_cols
    title_row[9] = f"{MONTH_NAMES_RU[month]} {year}"
    all_data.append(title_row)
    all_data.append([""] * n_cols)
    all_data.append([""] * n_cols)
    current_row += 3

    # ── Секции ──
    for section in SECTIONS:
        # Основные сотрудники раздела (не строки-замены)
        sec_emps = [
            e for e in employees
            if e["section"] == section and not e.get("is_replacement_for")
        ]
        # Пропускаем пустые разделы (кроме первого — чтобы шапка всегда была)
        if not sec_emps and section != SECTIONS[0]:
            continue

        # Заголовок раздела
        header_text = SECTION_SHEET_HEADER[section]
        if header_text:
            h_row = [""] * n_cols
            h_row[5] = header_text
            all_data.append(h_row)
            all_data.append([""] * n_cols)
            current_row += 2

        # Строка названий колонок
        if section == "runners":
            col_headers = ["Ф.И.О.", "Номер", "Должность", "", "", "Кол-во отр.часов"]
        else:
            col_headers = ["Ф.И.О.", "Номер", "Должность", "график",
                           "Кол-во раб.дн", "Кол-во отр.дн"]
        col_headers += list(range(1, total_days + 1)) + ["Удержание", "Аванс"]
        all_data.append(col_headers)
        current_row += 1

        # Строка дней недели
        dow_row = ["", "", "", "", "", ""]
        for d in range(1, total_days + 1):
            dow_row.append(weekday_name_ru(d, year, month))
        dow_row += ["", ""]
        all_data.append(dow_row)
        current_row += 1

        # ── Строки сотрудников ──
        emp_start_row = current_row

        for emp in sec_emps:
            emp_row = _build_emp_row(emp, section, current_row, year, month, total_days)
            all_data.append(emp_row)
            row_map[emp["id"]] = current_row
            current_row += 1

            # Строки замен для этого сотрудника
            replacements = [
                e for e in employees
                if e.get("is_replacement_for") == emp["id"]
            ]
            for rep in replacements:
                rep_row = _build_replacement_row(rep, emp, current_row, total_days)
                all_data.append(rep_row)
                row_map[rep["id"]] = current_row
                current_row += 1

        # Итоговая строка раздела
        total_row = _build_total_row(section, emp_start_row, current_row - 1,
                                     total_days, n_cols)
        all_data.append(total_row)
        current_row += 1

        # Разделитель
        all_data.append([""] * n_cols)
        current_row += 1

    # ── Запись ──
    ws.update("A1", all_data, value_input_option="USER_ENTERED")

    _save_row_map(year, month, row_map)
    return ws, row_map


def _build_emp_row(emp: dict, section: str, row: int,
                   year: int, month: int, total_days: int) -> list:
    plan = None if section == "runners" else calc_plan_shifts(emp, year, month)

    fired_str = ""
    if emp.get("fired"):
        fired_str = f"Уволен с {emp.get('fired_date', '')}"

    if section == "runners":
        base = [emp["name"], emp.get("phone", ""), emp.get("position", "Раннер"), "", "",
                f"=SUM({day_col(1)}{row}:{day_col(total_days)}{row})"]
    else:
        fact_formula = f"=SUM({day_col(1)}{row}:{day_col(total_days)}{row})"
        base = [
            emp["name"],
            emp.get("phone", ""),
            emp.get("position", ""),
            emp.get("schedule", ""),
            plan if plan is not None else "",
            fact_formula,
        ]

    base += [0] * total_days
    base += [fired_str or "", ""]
    return base


def _build_replacement_row(rep: dict, main_emp: dict, row: int, total_days: int) -> list:
    label = f"замена за {main_emp['name']}"
    base = [
        rep["name"],
        rep.get("phone", ""),
        rep.get("position", main_emp.get("position", "")),
        "",
        "",
        f"=SUM({day_col(1)}{row}:{day_col(total_days)}{row})",
    ]
    base += [0] * total_days
    base += ["", ""]
    # Имя сотрудника с пометкой «замена»
    base[0] = f"{rep['name']} ({label})"
    return base


def _build_total_row(section: str, start_row: int, end_row: int,
                     total_days: int, n_cols: int) -> list:
    total_row = [""] * n_cols
    if section == "runners":
        total_row[5] = f"=SUM(F{start_row}:F{end_row})"
    else:
        total_row[4] = f"=SUM(E{start_row}:E{end_row})"
        total_row[5] = f"=SUM(F{start_row}:F{end_row})"
    for d in range(1, total_days + 1):
        col = day_col(d)
        total_row[5 + d] = f"=SUM({col}{start_row}:{col}{end_row})"
    return total_row


# ─── row_map кэш ──────────────────────────────────────────────────────────────

def _row_map_file(year: int, month: int) -> str:
    return f".row_map_{year}_{month:02d}.json"


def _save_row_map(year: int, month: int, row_map: dict):
    with open(_row_map_file(year, month), "w", encoding="utf-8") as f:
        json.dump(row_map, f)


def _load_row_map(year: int, month: int) -> dict:
    path = _row_map_file(year, month)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_employee_row(emp_id: int, year: int, month: int) -> Optional[int]:
    rm = _load_row_map(year, month)
    val = rm.get(str(emp_id)) or rm.get(emp_id)
    return val


# ─── Запись значений ──────────────────────────────────────────────────────────

def write_shift(emp_id: int, day: int, value, year: int, month: int) -> bool:
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return False
    ws = get_or_create_sheet(year, month)
    ws.update(f"{day_col(day)}{row}", [[value]], value_input_option="USER_ENTERED")
    return True


def write_finance(emp_id: int, field: str, value, year: int, month: int) -> bool:
    """field = 'deduction' | 'advance'"""
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return False
    ws = get_or_create_sheet(year, month)
    total_days = days_in_month(year, month)
    col = deduction_col(total_days) if field == "deduction" else advance_col(total_days)
    ws.update(f"{col}{row}", [[value]], value_input_option="USER_ENTERED")
    return True


def mark_employee_fired(emp_id: int, fired_date: str, year: int, month: int) -> bool:
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return False
    ws = get_or_create_sheet(year, month)
    total_days = days_in_month(year, month)
    col = deduction_col(total_days)
    ws.update(f"{col}{row}", [[f"Уволен с {fired_date}"]], value_input_option="USER_ENTERED")
    # Красим строку розовым
    try:
        ws.format(f"A{row}:{advance_col(total_days)}{row}", {
            "backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8}
        })
    except Exception:
        pass
    return True


def add_replacement_row_to_sheet(main_emp_id: int, replacer_emp_id: int,
                                  year: int, month: int):
    """
    Добавить строку замены в лист (вставить строку сразу под основным сотрудником).
    Обновляет row_map.
    """
    from database import get_employee
    main_emp = get_employee(main_emp_id)
    rep_emp = get_employee(replacer_emp_id)
    if not main_emp or not rep_emp:
        return False

    main_row = get_employee_row(main_emp_id, year, month)
    if main_row is None:
        return False

    ws = get_or_create_sheet(year, month)
    total_days = days_in_month(year, month)
    n_cols = 6 + total_days + 2

    # Новая строка = main_row + 1 (сдвигаем всё ниже)
    new_row = main_row + 1

    # Вставляем пустую строку
    ws.insert_rows([[""]*n_cols], row=new_row)

    # Формируем содержимое строки замены
    rep_row = _build_replacement_row(rep_emp, main_emp, new_row, total_days)
    ws.update(f"A{new_row}", [rep_row], value_input_option="USER_ENTERED")

    # Обновляем row_map — все строки >= new_row сдвигаются +1
    rm = _load_row_map(year, month)
    new_rm = {}
    for eid, r in rm.items():
        if r >= new_row:
            new_rm[eid] = r + 1
        else:
            new_rm[eid] = r
    new_rm[str(replacer_emp_id)] = new_row
    _save_row_map(year, month, new_rm)
    return True


def read_shift(emp_id: int, day: int, year: int, month: int):
    """Прочитать значение смены."""
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return None
    ws = get_or_create_sheet(year, month)
    val = ws.cell(row, 6 + day).value
    return val


# ─── Экспорт xlsx ─────────────────────────────────────────────────────────────

def export_to_xlsx(year: int, month: int) -> Optional[str]:
    """Экспортировать лист в .xlsx. Возвращает путь к файлу."""
    sp = _get_spreadsheet()
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"
    try:
        ws = sp.worksheet(sheet_name)
        gid = ws.id
    except Exception:
        return None

    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export"
        f"?format=xlsx&gid={gid}"
    )

    creds_src = GOOGLE_CREDS_JSON
    if os.path.exists(creds_src):
        with open(creds_src) as f:
            info = json.load(f)
    else:
        info = json.loads(creds_src)

    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    creds.refresh(google.auth.transport.requests.Request())

    resp = requests.get(url, headers={"Authorization": f"Bearer {creds.token}"})
    if resp.status_code == 200:
        filename = f"Табель_{sheet_name.replace(' ', '_')}.xlsx"
        with open(filename, "wb") as f:
            f.write(resp.content)
        return filename
    return None
