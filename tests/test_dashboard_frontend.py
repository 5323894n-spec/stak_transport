from pathlib import Path


def test_dashboard_loads_maintenance_before_rendering_plans():
    text = Path("static/app.js").read_text(encoding="utf-8")
    dashboard = text.split("VIEWS.dashboard = async function () {", 1)[1]
    dashboard = dashboard.split("/* ================= НАРЯД", 1)[0]

    assert 'api("/api/repairs/maintenance/plans")' in dashboard
    assert "maintenanceData.items || []" in dashboard
