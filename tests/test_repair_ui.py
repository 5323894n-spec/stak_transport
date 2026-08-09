# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repair_menu_and_working_request_screen_are_present():
    text = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert '["repairs", "Ремонт и ТО"]' in text
    assert "VIEWS.repairs" in text
    assert "/api/repairs/requests" in text
    assert "Новая заявка на ремонт" in text
    assert "Создать заявку" in text
    assert "Активные ремонты" in text
    assert "Создать заказ-наряд" in text
    assert "/api/repairs/orders" in text
    assert "Заказ-наряды" in text
    assert "Добавить операцию" in text
    assert "/operations" in text
    assert "На контроль" in text
    assert "Контрольный осмотр" in text
    assert "Закрыть ремонт" in text
    assert "Запчасти заказ-наряда" in text
    assert "/api/repairs/stock/parts" in text
    assert "Плановое ТО" in text
    assert "Проверить сроки ТО" in text
    assert "/api/repairs/maintenance/evaluate" in text
    assert "Канбан ремонта" in text
    assert "Карточка автобуса" in text
    assert "/api/repairs/dashboard" in text
    assert "Отчёт ремонта Excel" in text
    assert "/api/repairs/reports/export.xlsx" in text
    assert "Вложения заказ-наряда" in text
    assert "/attachments" in text
    assert "Исполнители заказ-наряда" in text
    assert "Назначить исполнителя" in text
    assert "/workers/available" in text
    assert "Операции заказ-наряда" in text
    assert "Завершить операцию" in text
    assert "/complete" in text
    assert "orders/${o.id}/print" in text
    assert "Повторная неисправность" in text
    assert "Резервирование" in text
    assert "Запросить запчасть" in text
    assert "Возврат" in text
    assert "/api/repairs/metrics/downtime" in text
    assert "Коэффициент технической готовности" in text
    assert "Простой по этапам" in text
    assert "Ремонтный пост" in text
    assert "refs.repair_posts" in text
    assert "repairEditOrder" in text
    assert "Внешние расходы" in text
    assert "Прочие расходы" in text
    assert "Редактировать" in text
    assert "Период повторной неисправности" in text
    assert 'data-set="repair_repeat_days"' in text
    assert "/api/repairs/alerts/evaluate" in text
    assert "Проверить уведомления" in text
    assert "/api/repairs/calendar" in text
    assert "Календарь ремонтов и ТО" in text
    assert "repairReportExport" in text
    assert "Фильтры отчёта ремонта" in text


def test_index_uses_current_app_cache_version():
    text = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '/static/app.js?v=3.4' in text
