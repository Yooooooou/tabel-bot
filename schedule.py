"""
Расчёт плановых смен и определение рабочих дней.
"""
import calendar
from datetime import date, datetime
from typing import Optional


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def calc_plan_shifts(employee: dict, year: int, month: int) -> Optional[int]:
    """
    Рассчитать плановое количество смен для сотрудника в данном месяце.
    Возвращает None для раннеров (свободный график).
    """
    schedule = employee.get("schedule", "")
    total_days = days_in_month(year, month)

    if schedule == "7/0":
        # Работает каждый день
        return total_days

    elif schedule == "2/2":
        start_date_str = employee.get("start_date", "")
        if not start_date_str:
            return None
        try:
            start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            return None
        # Считаем рабочие дни в месяце по циклу 2/2
        count = 0
        for day in range(1, total_days + 1):
            d = date(year, month, day)
            delta = (d - start).days
            # В цикле 2/2: дни 0,1 = рабочие, 2,3 = выходные
            if delta >= 0 and (delta % 4) < 2:
                count += 1
            elif delta < 0:
                # до стартовой даты — обратный счёт
                back_delta = (start - d).days
                if (back_delta % 4) < 2:
                    count += 1
        return count

    elif schedule == "5/2":
        days_off = employee.get("days_off", [])  # [0..6], 0=пн, 6=вс
        if not days_off:
            return None
        count = 0
        for day in range(1, total_days + 1):
            weekday = date(year, month, day).weekday()  # 0=пн, 6=вс
            if weekday not in days_off:
                count += 1
        return count

    elif schedule == "свободный":
        return None  # раннеры — без плана

    return None


def is_work_day_2_2(employee: dict, check_date: date) -> bool:
    """Проверить является ли день рабочим для сотрудника 2/2."""
    start_date_str = employee.get("start_date", "")
    if not start_date_str:
        return False
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    delta = (check_date - start).days
    if delta >= 0:
        return (delta % 4) < 2
    else:
        back = (start - check_date).days
        return (back % 4) < 2


def is_work_day_5_2(employee: dict, check_date: date) -> bool:
    """Проверить является ли день рабочим для сотрудника 5/2."""
    days_off = employee.get("days_off", [])
    return check_date.weekday() not in days_off


def get_work_schedule_for_month(employee: dict, year: int, month: int) -> list:
    """
    Вернуть список булевых значений [True/False] для каждого дня месяца.
    True = рабочий день по графику.
    """
    schedule = employee.get("schedule", "")
    total = days_in_month(year, month)
    result = []

    for day in range(1, total + 1):
        d = date(year, month, day)
        if schedule == "2/2":
            result.append(is_work_day_2_2(employee, d))
        elif schedule == "5/2":
            result.append(is_work_day_5_2(employee, d))
        elif schedule == "7/0":
            result.append(True)
        else:
            result.append(False)  # свободный

    return result


def weekday_name_ru(day: int, year: int, month: int) -> str:
    """Вернуть краткое название дня недели на русском."""
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    return names[date(year, month, day).weekday()]
