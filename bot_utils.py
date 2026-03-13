"""Общие утилиты бота (дата/время, авторизация)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import database as db
from config import TIMEZONE, ADMIN_CHAT_ID, MONTH_NAMES_RU

TZ = ZoneInfo(TIMEZONE)


def now_tz() -> datetime:
    return datetime.now(tz=TZ)


def today_tz() -> date:
    return now_tz().date()


def is_authorized(chat_id: int) -> bool:
    return chat_id == ADMIN_CHAT_ID or chat_id in db.get_bot_admins()


def month_label(year: int, month: int) -> str:
    return f"{MONTH_NAMES_RU[month]} {year}"
