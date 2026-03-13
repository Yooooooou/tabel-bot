"""
Базовые тесты для функций очистки.
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock


class TestClearAllEmployees(unittest.TestCase):

    def setUp(self):
        """Использовать временный файл БД для каждого теста."""
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        self.tmp_path = self.tmp.name
        # Инициализировать файл структурой по умолчанию
        json.dump({"employees": [], "admins": [], "settings": {}}, self.tmp)
        self.tmp.close()

        import database
        self._orig_db = database.DB_FILE
        database.DB_FILE = self.tmp_path

    def tearDown(self):
        import database
        database.DB_FILE = self._orig_db
        os.unlink(self.tmp_path)

    def test_clear_all_employees_empties_list(self):
        import database
        database.add_employee({"name": "Иван", "section": "admins", "schedule": "2/2"})
        database.add_employee({"name": "Мария", "section": "waiters_day", "schedule": "5/2"})
        self.assertEqual(len(database.get_all_employees()), 2)

        database.clear_all_employees()
        self.assertEqual(database.get_all_employees(), [])

    def test_clear_all_employees_preserves_admins(self):
        import database
        database.add_employee({"name": "Пётр", "section": "tech", "schedule": "7/0"})
        database.add_bot_admin(12345)

        database.clear_all_employees()

        self.assertEqual(database.get_all_employees(), [])
        self.assertIn(12345, database.get_bot_admins())

    def test_clear_empty_db_is_safe(self):
        import database
        database.clear_all_employees()
        self.assertEqual(database.get_all_employees(), [])

    def test_add_after_clear_resets_ids(self):
        import database
        database.add_employee({"name": "А", "section": "admins", "schedule": "2/2"})
        database.clear_all_employees()
        emp = database.add_employee({"name": "Б", "section": "runners", "schedule": "свободный"})
        # После очистки новый id должен начинаться с 1
        self.assertEqual(emp["id"], 1)


class TestDeleteSheet(unittest.TestCase):

    def _import_sheets(self):
        """Импортировать sheets с замоканным gspread."""
        gspread_mock = mock.MagicMock()
        gspread_mock.WorksheetNotFound = Exception
        with mock.patch.dict("sys.modules", {
            "gspread": gspread_mock,
            "google": mock.MagicMock(),
            "google.oauth2": mock.MagicMock(),
            "google.oauth2.service_account": mock.MagicMock(),
            "google.auth": mock.MagicMock(),
            "google.auth.transport": mock.MagicMock(),
            "google.auth.transport.requests": mock.MagicMock(),
        }):
            if "sheets" in sys.modules:
                sys.modules.pop("sheets", None)
            import sheets as s
        return s, gspread_mock

    def test_delete_sheet_removes_row_map(self):
        """delete_sheet удаляет кэш-файл маппинга строк."""
        gspread_mock = mock.MagicMock()
        gspread_mock.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})

        with mock.patch.dict("sys.modules", {
            "gspread": gspread_mock,
            "google": mock.MagicMock(),
            "google.oauth2": mock.MagicMock(),
            "google.oauth2.service_account": mock.MagicMock(),
            "google.auth": mock.MagicMock(),
            "google.auth.transport": mock.MagicMock(),
            "google.auth.transport.requests": mock.MagicMock(),
        }):
            if "sheets" in sys.modules:
                sys.modules.pop("sheets", None)
            import sheets

            row_map_file = ".row_map_2026_03.json"
            with open(row_map_file, "w") as f:
                json.dump({}, f)
            self.assertTrue(os.path.exists(row_map_file))

            mock_ws = mock.MagicMock()
            mock_sp = mock.MagicMock()
            mock_sp.worksheet.return_value = mock_ws

            with mock.patch("sheets._get_spreadsheet", return_value=mock_sp):
                result = sheets.delete_sheet(2026, 3)

            self.assertTrue(result)
            self.assertFalse(os.path.exists(row_map_file))

        sys.modules.pop("sheets", None)

    def test_delete_sheet_returns_false_if_not_found(self):
        """delete_sheet возвращает False, если листа нет."""
        WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
        gspread_mock = mock.MagicMock()
        gspread_mock.WorksheetNotFound = WorksheetNotFound

        with mock.patch.dict("sys.modules", {
            "gspread": gspread_mock,
            "google": mock.MagicMock(),
            "google.oauth2": mock.MagicMock(),
            "google.oauth2.service_account": mock.MagicMock(),
            "google.auth": mock.MagicMock(),
            "google.auth.transport": mock.MagicMock(),
            "google.auth.transport.requests": mock.MagicMock(),
        }):
            if "sheets" in sys.modules:
                sys.modules.pop("sheets", None)
            import sheets

            mock_sp = mock.MagicMock()
            mock_sp.worksheet.side_effect = WorksheetNotFound

            with mock.patch("sheets._get_spreadsheet", return_value=mock_sp):
                result = sheets.delete_sheet(2026, 3)

            self.assertFalse(result)

        sys.modules.pop("sheets", None)


if __name__ == "__main__":
    unittest.main()
