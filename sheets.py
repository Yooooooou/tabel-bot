"""
Google Sheets: создание и обновление табеля с полным форматированием.
Структура и визуал повторяют шаблон Н8.
"""
import json
import os
import requests as http_requests
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


# ─── Цветовая палитра (RGB 0-1) ───────────────────────────────────────────────

# ─── Цветовая палитра (RGB 0-1) ───────────────────────────────────────────────

C_HEADER_BLUE   = {"red": 0.365, "green": 0.600, "blue": 0.800}   # основная синяя шапка
C_HEADER_DARK   = {"red": 0.290, "green": 0.510, "blue": 0.710}   # более тёмный синий
C_PURPLE_TITLE  = {"red": 0.533, "green": 0.459, "blue": 0.761}   # фиолетовый блок месяца
C_SECTION_LITE  = {"red": 0.914, "green": 0.882, "blue": 0.961}   # светлый заголовок раздела
C_TOTAL_LITE    = {"red": 0.824, "green": 0.906, "blue": 0.980}   # итоговая строка
C_WEEKEND       = {"red": 0.992, "green": 0.929, "blue": 0.780}   # мягкий жёлтый
C_WEEKEND_HDR   = {"red": 0.902, "green": 0.792, "blue": 0.490}   # заголовок выходных
C_REPLACE       = {"red": 0.937, "green": 0.894, "blue": 0.988}   # строки замены
C_FIRED         = {"red": 1.000, "green": 0.800, "blue": 0.800}   # уволенные
C_WHITE         = {"red": 1.000, "green": 1.000, "blue": 1.000}
C_WHITE_TEXT    = {"red": 1.000, "green": 1.000, "blue": 1.000}
C_DARK_TEXT     = {"red": 0.133, "green": 0.133, "blue": 0.133}
C_GRID          = {"red": 0.780, "green": 0.780, "blue": 0.780}

# ─── Подключение ──────────────────────────────────────────────────────────────

def _get_spreadsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
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
    
    
def recreate_sheet(year: int, month: int):
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"
    sp = _get_spreadsheet()

    try:
        old_ws = sp.worksheet(sheet_name)
        sp.del_worksheet(old_ws)
    except gspread.WorksheetNotFound:
        pass

    ws = sp.add_worksheet(title=sheet_name, rows=300, cols=45)
    return ws
# ─── Колонки ──────────────────────────────────────────────────────────────────
# 1=A  ФИО
# 2=B  Номер
# 3=C  Должность
# 4=D  График
# 5=E  Кол-во раб.дн (план)  / пусто для раннеров
# 6=F  Кол-во отр.дн (факт)  / Кол-во отр.часов для раннеров
# 7=G  День 1 … 6+total_days = последний день
# 6+total_days+1 = Удержание
# 6+total_days+2 = Аванс

def col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result

def day_col(day: int) -> str:
    return col_letter(6 + day)

def deduction_col(total_days: int) -> str:
    return col_letter(6 + total_days + 1)

def advance_col(total_days: int) -> str:
    return col_letter(6 + total_days + 2)


# ─── Построение табеля ────────────────────────────────────────────────────────

def build_sheet(year: int, month: int):
    """
    Полностью пересобрать лист табеля с форматированием.
    Возвращает (worksheet, row_map).
    """
    ws = recreate_sheet(year, month)

    employees   = get_all_employees()
    total_days  = days_in_month(year, month)
    n_cols      = 6 + total_days + 2

    all_data    = []
    row_map     = {}
    current_row = 1

    layout = {
        "sheet_id":  ws.id,
        "total_cols": n_cols,
        "title_row": 1,
        "sections":  [],
    }

        # ── Заголовок листа (строка 1) ──
    title_row = [""] * n_cols
    title_start = max(8, 6 + total_days // 3)
    if title_start >= n_cols:
        title_start = 0
    title_row[title_start] = f"{MONTH_NAMES_RU[month]} {year}"
    all_data.append(title_row)
    all_data.append([""] * n_cols)   # пустая строка-отступ
    current_row += 2

    # ── Секции ──
    for section in SECTIONS:
        sec_emps = [
            e for e in employees
            if e["section"] == section and not e.get("is_replacement_for")
        ]
        if not sec_emps and section != SECTIONS[0]:
            continue

        sec_info = {
            "section":            section,
            "section_header_row": None,
            "col_header_row":     None,
            "dow_row":            None,
            "data_start":         None,
            "data_end":           None,
            "total_row":          None,
            "replacement_rows":   [],
        }

        # Заголовок раздела
        header_text = SECTION_SHEET_HEADER[section]
        if header_text:
            h_row = [""] * n_cols
            h_row[0] = header_text
            all_data.append(h_row)
            sec_info["section_header_row"] = current_row
            current_row += 1
            all_data.append([""] * n_cols)
            current_row += 1

        # Строка названий колонок
        if section == "runners":
            col_headers = ["Ф.И.О.", "Номер", "Должность", "", "", "Кол-во отр.часов"]
        else:
            col_headers = ["Ф.И.О.", "Номер", "Должность", "график",
                           "Кол-во раб.дн", "Кол-во отр.дн"]
        col_headers += list(range(1, total_days + 1)) + ["Удержание", "Аванс"]
        all_data.append(col_headers)
        sec_info["col_header_row"] = current_row
        current_row += 1

        # Строка дней недели
        dow_row = ["", "", "", "", "", ""]
        for d in range(1, total_days + 1):
            dow_row.append(weekday_name_ru(d, year, month))
        dow_row += ["", ""]
        all_data.append(dow_row)
        sec_info["dow_row"] = current_row
        current_row += 1

        # ── Строки сотрудников ──
        sec_info["data_start"] = current_row

        for emp in sec_emps:
            emp_row = _build_emp_row(emp, section, current_row, year, month, total_days)
            all_data.append(emp_row)
            row_map[emp["id"]] = current_row
            current_row += 1

            # Строки замен
            replacements = [
                e for e in employees
                if e.get("is_replacement_for") == emp["id"]
            ]
            for rep in replacements:
                rep_row = _build_replacement_row(rep, emp, current_row, total_days)
                all_data.append(rep_row)
                row_map[rep["id"]] = current_row
                sec_info["replacement_rows"].append(current_row)
                current_row += 1

        sec_info["data_end"] = current_row - 1

        # Итоговая строка
        total_row = _build_total_row(section, sec_info["data_start"],
                                     sec_info["data_end"], total_days, n_cols)
        all_data.append(total_row)
        sec_info["total_row"] = current_row
        current_row += 1

        # Разделитель
        all_data.append([""] * n_cols)
        current_row += 1

        layout["sections"].append(sec_info)

    # ── Запись данных ──
    ws.update("A1", all_data, value_input_option="USER_ENTERED")

    # ── Форматирование ──
    _format_sheet(ws, layout, total_days, year, month)

    _save_row_map(year, month, row_map)
    return ws, row_map


# ─── Построители строк ────────────────────────────────────────────────────────

def _build_emp_row(emp: dict, section: str, row: int,
                   year: int, month: int, total_days: int) -> list:
    plan = None if section == "runners" else calc_plan_shifts(emp, year, month)
    fired_str = f"Уволен с {emp.get('fired_date', '')}" if emp.get("fired") else ""

    if section == "runners":
        base = [emp["name"], emp.get("phone", ""), emp.get("position", "Раннер"),
                "", "", f"=SUM({day_col(1)}{row}:{day_col(total_days)}{row})"]
    else:
        base = [
            emp["name"],
            emp.get("phone", ""),
            emp.get("position", ""),
            emp.get("schedule", ""),
            plan if plan is not None else "",
            f"=SUM({day_col(1)}{row}:{day_col(total_days)}{row})",
        ]
    base += [0] * total_days
    base += [fired_str or "", ""]
    return base


def _build_replacement_row(rep: dict, main_emp: dict, row: int, total_days: int) -> list:
    base = [
        f"{rep['name']} (замена за {main_emp['name']})",
        rep.get("phone", ""),
        rep.get("position", main_emp.get("position", "")),
        "", "",
        f"=SUM({day_col(1)}{row}:{day_col(total_days)}{row})",
    ]
    base += [0] * total_days
    base += ["", ""]
    return base


def _build_total_row(section: str, start_row: int, end_row: int,
                     total_days: int, n_cols: int) -> list:
    total_row = [""] * n_cols
    if start_row > end_row:
        # Empty section — no employees, skip formulas to avoid #REF!
        return total_row
    if section == "runners":
        total_row[5] = f"=SUM(F{start_row}:F{end_row})"
    else:
        total_row[4] = f"=SUM(E{start_row}:E{end_row})"
        total_row[5] = f"=SUM(F{start_row}:F{end_row})"
    for d in range(1, total_days + 1):
        total_row[5 + d] = f"=SUM({day_col(d)}{start_row}:{day_col(d)}{end_row})"
    return total_row


# ─── Форматирование ───────────────────────────────────────────────────────────

def _format_sheet(ws, layout: dict, total_days: int, year: int, month: int):
    sid    = layout["sheet_id"]
    n_cols = layout["total_cols"]
    reqs   = []

    weekend_cols = [
        5 + d for d in range(1, total_days + 1)
        if date(year, month, d).weekday() in (5, 6)
    ]

    # Базовый фон
    reqs.append(
        _fmt(sid, 0, 300, 0, n_cols,
             bg=C_WHITE, fg=C_DARK_TEXT, halign="CENTER", valign="MIDDLE")
    )

    # Заголовок месяца
    tr = layout["title_row"] - 1
    title_start = max(8, 6 + total_days // 3)
    title_end = min(title_start + 4, n_cols)

    reqs += [
        _fmt(sid, tr, tr+1, 0, n_cols,
             bg=C_WHITE, fg=C_DARK_TEXT, halign="LEFT", valign="MIDDLE"),
        _row_height(sid, tr, tr+1, 34),
    ]

    if title_start < title_end:
        reqs += [
            _fmt(sid, tr, tr+1, title_start, title_end,
                 bg=C_PURPLE_TITLE, bold=True, font_size=12,
                 fg=C_WHITE_TEXT, halign="CENTER", valign="MIDDLE", wrap="WRAP"),
        ]

    for sec in layout["sections"]:

        # Заголовок раздела
        if sec["section_header_row"]:
            r = sec["section_header_row"] - 1
            reqs += [
                _fmt(sid, r, r+1, 0, n_cols,
                     bg=C_SECTION_LITE, bold=True, font_size=11,
                     fg=C_DARK_TEXT, halign="LEFT", valign="MIDDLE", wrap="WRAP"),
                _row_height(sid, r, r+1, 28),
            ]

        # Шапка колонок
        chr_ = sec["col_header_row"] - 1
        reqs += [
            _fmt(sid, chr_, chr_+1, 0, n_cols,
                 bg=C_HEADER_BLUE, bold=True, fg=C_WHITE_TEXT,
                 halign="CENTER", valign="MIDDLE", wrap="WRAP"),
            _fmt(sid, chr_, chr_+1, 0, 3,
                 bg=C_HEADER_BLUE, bold=True, fg=C_WHITE_TEXT,
                 halign="LEFT", valign="MIDDLE", wrap="WRAP"),
            _row_height(sid, chr_, chr_+1, 30),
        ]

        for wc in weekend_cols:
            reqs.append(
                _fmt(sid, chr_, chr_+1, wc, wc+1,
                     bg=C_WEEKEND_HDR, bold=True, fg=C_WHITE_TEXT,
                     halign="CENTER", valign="MIDDLE", wrap="WRAP")
            )

        # Строка дней недели
        dwr = sec["dow_row"] - 1
        reqs += [
            _fmt(sid, dwr, dwr+1, 0, n_cols,
                 bg=C_WHITE, bold=True, fg=C_DARK_TEXT,
                 halign="CENTER", valign="MIDDLE"),
            _row_height(sid, dwr, dwr+1, 22),
        ]

        for wc in weekend_cols:
            reqs.append(
                _fmt(sid, dwr, dwr+1, wc, wc+1,
                     bg=C_WEEKEND, bold=True, fg=C_DARK_TEXT,
                     halign="CENTER", valign="MIDDLE")
            )

        # Строки данных
        ds = sec["data_start"] - 1
        de = sec["data_end"]
        if ds < de:
            reqs += [
                _fmt(sid, ds, de, 0, n_cols,
                     bg=C_WHITE, bold=False, fg=C_DARK_TEXT,
                     halign="CENTER", valign="MIDDLE"),
                _fmt(sid, ds, de, 0, 1,
                     bg=C_WHITE, halign="LEFT", valign="MIDDLE"),
                _fmt(sid, ds, de, 2, 3,
                     bg=C_WHITE, halign="LEFT", valign="MIDDLE"),
                _row_height(sid, ds, de, 22),
            ]

            for wc in weekend_cols:
                reqs.append(_fmt(sid, ds, de, wc, wc+1, bg=C_WEEKEND))

        # Строки замены
        for rep_row in sec.get("replacement_rows", []):
            rr = rep_row - 1
            reqs += [
                _fmt(sid, rr, rr+1, 0, n_cols,
                     bg=C_REPLACE, fg=C_DARK_TEXT,
                     halign="CENTER", valign="MIDDLE"),
                _fmt(sid, rr, rr+1, 0, 1,
                     bg=C_REPLACE, halign="LEFT", valign="MIDDLE"),
                _fmt(sid, rr, rr+1, 2, 3,
                     bg=C_REPLACE, halign="LEFT", valign="MIDDLE"),
            ]

        # Итоговая строка
        totr = sec["total_row"] - 1
        reqs += [
            _fmt(sid, totr, totr+1, 0, n_cols,
                 bg=C_TOTAL_LITE, bold=True, fg=C_DARK_TEXT,
                 halign="CENTER", valign="MIDDLE"),
            _fmt(sid, totr, totr+1, 0, 3,
                 bg=C_TOTAL_LITE, bold=True, fg=C_DARK_TEXT,
                 halign="LEFT", valign="MIDDLE"),
            _row_height(sid, totr, totr+1, 24),
        ]

    # Границы
    last_row = max(
        (s["total_row"] for s in layout["sections"] if s.get("total_row")),
        default=50
    )
    reqs.append(_borders_light(sid, 0, last_row, 0, n_cols))

    # Ширины колонок
    col_widths = [
        (0, 1, 190),
        (1, 2, 120),
        (2, 3, 130),
        (3, 4, 60),
        (4, 5, 90),
        (5, 6, 90),
    ]
    for d in range(1, total_days + 1):
        col_widths.append((5 + d, 6 + d, 30))
    col_widths.append((6 + total_days, 6 + total_days + 1, 110))
    col_widths.append((6 + total_days + 1, 6 + total_days + 2, 95))

    for cs, ce, px in col_widths:
        reqs.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sid,
                    "dimension": "COLUMNS",
                    "startIndex": cs,
                    "endIndex": ce
                },
                "properties": {"pixelSize": px},
                "fields": "pixelSize",
            }
        })

    # Заморозка
    reqs.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sid,
                "gridProperties": {
                    "frozenColumnCount": 6,
                    "frozenRowCount": 6,
                },
            },
            "fields": "gridProperties.frozenColumnCount,gridProperties.frozenRowCount",
        }
    })

    print("FORMAT REQUESTS:", len(reqs))
    ws.spreadsheet.batch_update({"requests": reqs})
# ─── Хелперы для Sheets API requests ─────────────────────────────────────────

def _fmt(sid: int, r0: int, r1: int, c0: int, c1: int,
         bg=None, bold: bool = None, fg=None,
         font_size: int = None, halign: str = None,
         valign: str = None, wrap: str = None) -> dict:
    fmt = {}

    if bg is not None:
        fmt["backgroundColor"] = bg

    tf = {}
    if bold is not None:
        tf["bold"] = bold
    if fg is not None:
        tf["foregroundColor"] = fg
    if font_size is not None:
        tf["fontSize"] = font_size
    if tf:
        fmt["textFormat"] = tf

    if halign is not None:
        fmt["horizontalAlignment"] = halign
    if valign is not None:
        fmt["verticalAlignment"] = valign
    if wrap is not None:
        fmt["wrapStrategy"] = wrap

    return {
        "repeatCell": {
            "range": {
                "sheetId": sid,
                "startRowIndex": r0,
                "endRowIndex": r1,
                "startColumnIndex": c0,
                "endColumnIndex": c1
            },
            "cell": {"userEnteredFormat": fmt},
            "fields": "userEnteredFormat",
        }
    }
def _merge(sid: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    return {
        "mergeCells": {
            "range": {"sheetId": sid,
                      "startRowIndex": r0, "endRowIndex": r1,
                      "startColumnIndex": c0, "endColumnIndex": c1},
            "mergeType": "MERGE_ALL",
        }
    }


def _row_height(sid: int, r0: int, r1: int, px: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS",
                      "startIndex": r0, "endIndex": r1},
            "properties": {"pixelSize": px},
            "fields": "pixelSize",
        }
    }
def _borders_light(sid: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    b = {"style": "SOLID", "color": C_GRID}
    return {
        "updateBorders": {
            "range": {
                "sheetId": sid,
                "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1
            },
            "top": b, "bottom": b, "left": b, "right": b,
            "innerHorizontal": b, "innerVertical": b,
        }
    }


def _outline(sid: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    b = {"style": "SOLID_MEDIUM", "color": C_GRID}
    return {
        "updateBorders": {
            "range": {
                "sheetId": sid,
                "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1
            },
            "top": b, "bottom": b, "left": b, "right": b,
        }
    }

def _borders(sid: int, r0: int, r1: int, c0: int, c1: int) -> dict:
    b = {"style": "SOLID", "color": C_GRID}
    return {
        "updateBorders": {
            "range": {"sheetId": sid,
                      "startRowIndex": r0, "endRowIndex": r1,
                      "startColumnIndex": c0, "endColumnIndex": c1},
            "top": b, "bottom": b, "left": b, "right": b,
            "innerHorizontal": b, "innerVertical": b,
        }
    }

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
    return rm.get(str(emp_id)) or rm.get(emp_id)


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
        sid = ws.id
        ws.spreadsheet.batch_update({"requests": [
            _fmt(sid, row-1, row, 0, 6 + total_days + 2,
                 bg=C_FIRED, fg=C_DARK_TEXT, halign="CENTER", valign="MIDDLE"),
            _fmt(sid, row-1, row, 0, 1,
                 bg=C_FIRED, halign="LEFT", valign="MIDDLE"),
            _fmt(sid, row-1, row, 2, 3,
                 bg=C_FIRED, halign="LEFT", valign="MIDDLE"),
            _outline(sid, row-1, row, 0, 6 + total_days + 2),
        ]})
    except Exception:
        pass
    return True


def add_replacement_row_to_sheet(main_emp_id: int, replacer_emp_id: int,
                                  year: int, month: int) -> bool:
    from database import get_employee
    main_emp = get_employee(main_emp_id)
    rep_emp  = get_employee(replacer_emp_id)
    if not main_emp or not rep_emp:
        return False

    main_row = get_employee_row(main_emp_id, year, month)
    if main_row is None:
        return False

    ws         = get_or_create_sheet(year, month)
    total_days = days_in_month(year, month)
    n_cols     = 6 + total_days + 2
    new_row    = main_row + 1

    ws.insert_rows([[""]*n_cols], row=new_row)
    rep_row = _build_replacement_row(rep_emp, main_emp, new_row, total_days)
    ws.update(f"A{new_row}", [rep_row], value_input_option="USER_ENTERED")

    # Форматируем строку замены
    try:
        sid = ws.id
        ws.spreadsheet.batch_update({"requests": [
            _fmt(sid, new_row-1, new_row, 0, n_cols,
                 bg=C_REPLACE, fg=C_DARK_TEXT, halign="CENTER", valign="MIDDLE"),
            _fmt(sid, new_row-1, new_row, 0, 1,
                 bg=C_REPLACE, halign="LEFT", valign="MIDDLE"),
            _fmt(sid, new_row-1, new_row, 2, 3,
                 bg=C_REPLACE, halign="LEFT", valign="MIDDLE"),
            _borders_light(sid, new_row-1, new_row, 0, n_cols),
        ]})
    except Exception:
        pass

    # Обновляем row_map
    rm = _load_row_map(year, month)
    new_rm = {eid: (r + 1 if r >= new_row else r) for eid, r in rm.items()}
    new_rm[str(replacer_emp_id)] = new_row
    _save_row_map(year, month, new_rm)
    return True


def read_shift(emp_id: int, day: int, year: int, month: int):
    row = get_employee_row(emp_id, year, month)
    if row is None:
        return None
    ws = get_or_create_sheet(year, month)
    return ws.cell(row, 6 + day).value


# ─── Экспорт xlsx ─────────────────────────────────────────────────────────────

def export_to_xlsx(year: int, month: int) -> Optional[str]:
    sp = _get_spreadsheet()
    sheet_name = f"{MONTH_NAMES_RU[month]} {year}"
    try:
        ws  = sp.worksheet(sheet_name)
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
    creds  = Credentials.from_service_account_info(info, scopes=scopes)
    creds.refresh(google.auth.transport.requests.Request())

    resp = http_requests.get(url, headers={"Authorization": f"Bearer {creds.token}"})
    if resp.status_code == 200:
        filename = f"Табель_{sheet_name.replace(' ', '_')}.xlsx"
        with open(filename, "wb") as f:
            f.write(resp.content)
        return filename
    return None
