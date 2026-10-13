from pathlib import Path


def test_martes_branding_replaces_project_x_home_texts():
    html = Path("app/ui/index.html").read_text(encoding="utf-8")

    assert "MARTES" in html
    assert "Personal AI Assistant" in html
    assert "Tu asistente, tus proyectos y tus ideas en un solo lugar" not in html
    assert "JARVIS DAILY BRIEFING" not in html
    assert "Pensar un proyecto" not in html


def test_activity_indicator_and_streaming_client_exist():
    js = Path("app/ui/app.js").read_text(encoding="utf-8")
    css = Path("app/ui/styles.css").read_text(encoding="utf-8")

    assert "/api/chat/stream" in js
    assert "activity-indicator" in js
    assert ".activity-indicator" in css
