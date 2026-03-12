"""Конфиг и константы проекта."""
import os

BOT_TOKEN         = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID     = int(os.environ["ADMIN_CHAT_ID"])
SPREADSHEET_ID    = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "credentials.json")

# Public URL вашего Railway-сервиса, например:
# https://tabel-bot-production.up.railway.app
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

# Часовой пояс (Алматы UTC+5)
TIMEZONE = "Asia/Almaty"

# Время напоминаний (по Алматы)
REMINDER_MORNING = (8, 0)
REMINDER_EVENING = (21, 0)

# ─── Разделы табеля ───────────────────────────────────────────────────────────
# Порядок важен — именно в этом порядке строится лист
SECTIONS = [
    "admins",       # Администраторы + Кассиры (2/2)
    "waiters_day",  # Утренние официанты     (5/2, 7:00-15:30)
    "waiters_eve",  # Вечерние официанты     (5/2, 15:30-22:00)
    "runners",      # Раннеры                (свободный, часы)
    "tech",         # Тех. отдел             (Сторожа 2/2 + Развозка 7/0)
]

SECTION_LABELS = {
    "admins":      "Администраторы и кассиры",
    "waiters_day": "Утренние официанты",
    "waiters_eve": "Вечерние официанты",
    "runners":     "Раннеры",
    "tech":        "Тех. отдел (Сторожа + Развозка)",
}

# Заголовки-разделители в листе (None = нет заголовка, строка = текст заголовка)
SECTION_SHEET_HEADER = {
    "admins":      None,
    "waiters_day": "Подразделение: Зал",
    "waiters_eve": "Вечерняя смена",
    "runners":     "Раннеры",
    "tech":        "Подразделение: Тех.отдел",
}

# Допустимые значения смены (кроме раннеров)
SHIFT_VALUES = ["1", "0.7", "0.5", "0.3", "0"]

# Дни недели на русском
WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}
