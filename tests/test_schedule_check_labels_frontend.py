# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


KIND_LABELS = {
    "missing_fields": "не заполнены поля рейса",
    "missing_stop_times": "нет времени по остановкам",
    "invalid_stop_sequence": "нарушен порядок остановок",
    "duplicate_trip_number": "повтор номера рейса",
    "break_gap": "рейс раньше окончания перерыва",
    "overlap": "наложение рейсов",
    "short_rest": "малый межрейсовый отстой",
    "long_output_without_shift_split": "длинный выход без деления на смены",
    "missing_lunch": "нет обеденного перерыва",
    "large_interval_gap": "большой интервал движения",
}


def test_check_kind_labels_are_russian_and_complete():
    src = _src("app.js")
    assert "function checkKindLabel" in src
    for code, label in KIND_LABELS.items():
        assert code in src, f"kind code {code} missing from label map"
        assert label in src, f"russian label for {code} missing"


def test_check_displays_use_localized_label_not_raw_kind():
    src = _src("app.js")
    # both the trips «Проверка» badge and the «Ошибки и рекомендации» panel
    assert "checkKindLabel(p.kind)" in src
    # the raw English kind code must no longer be shown directly
    assert "esc(p.kind)" not in src


def test_app_asset_version_bumped():
    index = _src("index.html")
    assert "app.js?v=3.5" in index
