# -*- coding: utf-8 -*-
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"


def test_summary_schedule_menu_and_actions_are_present():
    text = APP_JS.read_text(encoding="utf-8")
    assert '["summarySchedule", "Сводное расписание"]' in text
    assert "VIEWS.summarySchedule" in text
    assert "/api/summary-schedules/generate" in text
    assert "Сформировать сводное расписание" in text
    assert "Проверка ошибок" in text
    assert "Выгрузить в Excel" in text
    assert "Сформировать наряд" in text