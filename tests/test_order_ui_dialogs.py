from pathlib import Path


def test_order_ui_does_not_use_native_prompt_dialogs():
    app_js = Path("static/app.js").read_text(encoding="utf-8")

    assert "prompt(" not in app_js
