from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_schedule_workspace_contains_generation_and_matrix_flows():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "scheduleGenerationPreview" in source
    assert "scheduleGenerationApply" in source
    assert "scheduleStopMatrix" in source
    assert "scheduleStopTimeEdit" in source
    assert "scheduleStopOverridesReset" in source
    assert "schedule-generation/preview" in source
    assert "schedule-generation/apply" in source


def test_matrix_styles_and_cache_key_are_present():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert ".schedule-stop-matrix" in styles
    assert ".schedule-stop-time-manual" in styles
    assert ".schedule-generation-diff" in styles
    assert "route=3.8" in index
