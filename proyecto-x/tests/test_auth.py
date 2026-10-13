from fastapi.testclient import TestClient

from app.auth import AuthStore
from app.config.settings import get_settings
from app.main import create_app


def test_owner_can_login_and_hash_is_argon2id(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_BOOTSTRAP_PASSWORD", "NuevaClaveUnica123!")
    get_settings.cache_clear()
    db = tmp_path / "app.db"
    app = create_app(database_path=db, enable_web=False, auth_required=True)
    client = TestClient(app)

    response = client.post(
        "/api/auth/login",
        json={"email": "kevynnquintero32@gmail.com", "password": "NuevaClaveUnica123!"},
    )

    assert response.status_code == 200
    row = AuthStore(db)._user_row_by_email("kevynnquintero32@gmail.com")
    assert row["password_hash"].startswith("$argon2id$")
    assert "NuevaClaveUnica123!" not in row["password_hash"]


def test_first_run_bootstrap_creates_owner_and_logs_in(tmp_path):
    get_settings.cache_clear()
    db = tmp_path / "app.db"
    app = create_app(database_path=db, enable_web=False, auth_required=True)
    client = TestClient(app)

    status = client.get("/api/auth/bootstrap").json()
    response = client.post(
        "/api/auth/bootstrap",
        json={"email": status["email"], "password": "ClaveInicial123!"},
        headers={"Origin": "http://127.0.0.1:8765"},
    )

    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    row = AuthStore(db)._user_row_by_email(status["email"])
    assert row["role"] == "owner"


def test_private_endpoints_require_auth(tmp_path):
    app = create_app(database_path=tmp_path / "app.db", enable_web=False, auth_required=True)
    client = TestClient(app)

    response = client.get("/api/conversations")

    assert response.status_code == 401


def test_owner_can_create_user_but_normal_user_cannot(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_BOOTSTRAP_PASSWORD", "NuevaClaveUnica123!")
    get_settings.cache_clear()
    db = tmp_path / "app.db"
    app = create_app(database_path=db, enable_web=False, auth_required=True)
    owner = TestClient(app)
    owner.post(
        "/api/auth/login",
        json={"email": "kevynnquintero32@gmail.com", "password": "NuevaClaveUnica123!"},
    )
    csrf = owner.get("/api/auth/me").json()["csrf_token"]

    created = owner.post(
        "/api/users",
        json={
            "email": "otro@example.com",
            "password": "OtraClaveUnica123!",
            "confirm_password": "OtraClaveUnica123!",
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert created.status_code == 200
    user = TestClient(app)
    user.post(
        "/api/auth/login",
        json={"email": "otro@example.com", "password": "OtraClaveUnica123!"},
    )
    user_csrf = user.get("/api/auth/me").json()["csrf_token"]
    denied = user.post(
        "/api/users",
        json={
            "email": "nope@example.com",
            "password": "OtraClaveUnica123!",
            "confirm_password": "OtraClaveUnica123!",
        },
        headers={"X-CSRF-Token": user_csrf},
    )
    assert denied.status_code == 403


def test_password_like_chat_content_is_redacted_before_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_BOOTSTRAP_PASSWORD", "NuevaClaveUnica123!")
    get_settings.cache_clear()
    app = create_app(database_path=tmp_path / "app.db", enable_web=False, auth_required=True)
    client = TestClient(app)
    client.post(
        "/api/auth/login",
        json={"email": "kevynnquintero32@gmail.com", "password": "NuevaClaveUnica123!"},
    )
    csrf = client.get("/api/auth/me").json()["csrf_token"]

    response = client.post(
        "/api/chat",
        json={"message": "mi password: SuperSecreta123"},
        headers={"X-CSRF-Token": csrf},
    )
    history = client.get("/api/history").json()

    assert response.status_code == 200
    assert all("SuperSecreta123" not in item["content"] for item in history)
