import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest
from fastapi.testclient import TestClient

from per_diem_signing.auth import PENDING_COOKIE, SESSION_COOKIE
from per_diem_signing.server import create_app
from per_diem_signing.test_server import DETAILS, KEY, AUTH, image_bytes

HEADERS = {"X-PerDiem-Request": "1"}
PASSWORD = "Test-only-long-password!"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr("per_diem_signing.auth.time.time", lambda: clock[0])
    app = create_app(tmp_path, admin_key=KEY)
    with TestClient(app) as client:
        yield client, tmp_path, clock


def begin(client):
    response = client.post("/api/auth/enroll", headers=HEADERS,
                           json={"key": KEY, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def enroll(client, clock):
    data = begin(client)
    response = client.post("/api/auth/confirm", headers=HEADERS,
                           json={"code": pyotp.TOTP(data["secret"]).at(clock[0])})
    assert response.status_code == 200, response.text
    return data["secret"], response.json()["recovery_codes"]


def login(client, code, password=PASSWORD):
    return client.post("/api/auth/login", headers=HEADERS,
                       json={"password": password, "code": code})


def test_enrollment_hashes_password_disables_key_and_uses_session(environment):
    client, folder, clock = environment
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
    secret, codes = enroll(client, clock)
    assert len(set(codes)) == 10
    assert client.get("/api/admin/requests").status_code == 200
    assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}
    db = sqlite3.connect(folder / "authentication.db")
    try:
        row = db.execute("SELECT password_hash, secret FROM administrator").fetchone()
        assert row[0].startswith("$argon2id$")
        assert PASSWORD not in row[0]
        assert row[1] == secret
        assert db.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0
        hashes = [row[0] for row in db.execute("SELECT code_hash FROM recovery")]
        assert all(code.replace("-", "") not in hashes for code in codes)
    finally:
        db.close()
    client.cookies.clear()
    assert client.get("/api/admin/requests", headers=AUTH).status_code == 401
    assert client.post("/api/auth/enroll", headers=HEADERS,
                       json={"key": KEY, "password": PASSWORD}).status_code == 409


def test_login_replay_password_logout_and_expiry(environment):
    client, _, clock = environment
    secret, _ = enroll(client, clock)
    original_session = client.cookies[SESSION_COOKIE]
    assert client.post("/api/auth/logout", headers=HEADERS).status_code == 200
    assert client.get("/api/admin/requests").status_code == 401
    assert login(client, pyotp.TOTP(secret).at(clock[0])).status_code == 401
    clock[0] += 30
    assert login(client, pyotp.TOTP(secret).at(clock[0]), "wrong").status_code == 401
    assert login(client, pyotp.TOTP(secret).at(clock[0])).status_code == 200
    assert client.cookies[SESSION_COOKIE] != original_session
    clock[0] += 8 * 60 * 60 + 1
    assert client.get("/api/admin/requests").status_code == 401


def test_recovery_codes_one_use_and_require_password(environment):
    client, _, clock = environment
    _, codes = enroll(client, clock)
    client.cookies.clear()
    assert login(client, codes[0], "wrong").status_code == 401
    response = login(client, codes[0])
    assert response.status_code == 200
    assert response.json()["used_recovery_code"] is True
    client.cookies.clear()
    assert login(client, codes[0]).status_code == 401
    assert login(client, codes[1].lower().replace("-", "")).status_code == 200


def test_expired_pending_does_not_disable_key(environment):
    client, _, clock = environment
    data = begin(client)
    clock[0] += 601
    assert client.post("/api/auth/confirm", headers=HEADERS,
                       json={"code": pyotp.TOTP(data["secret"]).at(clock[0])}).status_code == 410
    assert not client.get("/api/auth/status").json()["configured"]
    assert client.get("/api/admin/requests", headers=AUTH).status_code == 200


def test_failed_confirmation_can_retry_and_no_secret_public(environment):
    client, _, clock = environment
    data = begin(client)
    bad = client.post("/api/auth/confirm", headers=HEADERS, json={"code": "abcdef"})
    assert bad.status_code == 401
    assert data["secret"] not in client.get("/api/auth/status").text
    response = client.post("/api/auth/confirm", headers=HEADERS,
                           json={"code": pyotp.TOTP(data["secret"]).at(clock[0])})
    assert response.status_code == 200
    assert client.post("/api/auth/confirm", headers=HEADERS,
                       json={"code": "123456"}).status_code == 409


def test_bruteforce_limit_persists_and_expires(environment):
    client, folder, clock = environment
    for _ in range(10):
        assert client.post("/api/auth/enroll", headers=HEADERS,
                           json={"key": "wrong", "password": PASSWORD}).status_code == 401
    with TestClient(create_app(folder, admin_key=KEY)) as other:
        limited = other.post("/api/auth/enroll", headers=HEADERS,
                             json={"key": KEY, "password": PASSWORD})
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "900"
    clock[0] += 901
    assert begin(client)["secret"]


def test_csrf_and_cross_origin_are_blocked(environment):
    client, _, clock = environment
    assert client.post("/api/auth/enroll", json={"key": KEY, "password": PASSWORD}).status_code == 403
    assert client.post("/api/auth/enroll",
                       headers={**HEADERS, "Origin": "https://attacker.example"},
                       json={"key": KEY, "password": PASSWORD}).status_code == 403
    enroll(client, clock)
    assert client.post("/api/admin/requests", json=DETAILS).status_code == 403
    assert client.post("/api/admin/requests", headers=HEADERS, json=DETAILS).status_code == 201
    assert client.post("/api/auth/logout",
                       headers={**HEADERS, "Origin": "https://attacker.example"}).status_code == 403


def test_secure_cookies_public_origin_and_restart(tmp_path):
    clock = time.time()
    app = create_app(tmp_path, admin_key=KEY, public_url="https://perdiem.example.test")
    with TestClient(app, base_url="https://perdiem.example.test") as client:
        data = begin(client)
        response = client.post("/api/auth/confirm", headers=HEADERS,
                               json={"code": pyotp.TOTP(data["secret"]).at(clock)})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        session = client.cookies[SESSION_COOKIE]
    with TestClient(create_app(tmp_path, admin_key=KEY, public_url="https://perdiem.example.test"),
                    base_url="https://perdiem.example.test") as client:
        client.cookies.set(SESSION_COOKIE, session)
        assert client.get("/api/admin/requests").status_code == 200
        assert client.post("/api/auth/logout", headers=HEADERS).status_code == 200
        client.cookies.set(SESSION_COOKIE, session)
        assert client.get("/api/admin/requests").status_code == 401


def test_recipient_workflow_unchanged_after_enrollment(environment):
    client, _, clock = environment
    enroll(client, clock)
    response = client.post("/api/admin/requests", headers=HEADERS, json=DETAILS)
    token = response.json()["link"].rsplit("/", 1)[-1]
    client.cookies.clear()
    assert client.get(f"/api/sign/{token}").status_code == 200
    result = client.post(f"/api/sign/{token}/submit",
                         data={"name": "Recipient", "consent": "yes"},
                         files={"signature": ("signature.png", image_bytes(True), "image/png")})
    assert result.status_code == 200


def test_concurrent_totp_login_only_one_success(environment):
    client, folder, clock = environment
    secret, _ = enroll(client, clock)
    clock[0] += 30
    code = pyotp.TOTP(secret).at(clock[0])

    def attempt(_):
        with TestClient(create_app(folder, admin_key=KEY)) as other:
            return login(other, code).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 401]


def test_short_password_and_forged_cookies(environment):
    client, _, clock = environment
    assert client.post("/api/auth/enroll", headers=HEADERS,
                       json={"key": KEY, "password": "short"}).status_code == 422
    begin(client)
    client.cookies.set(PENDING_COOKIE, "forged", path="/api/auth")
    assert client.post("/api/auth/confirm", headers=HEADERS,
                       json={"code": "123456"}).status_code == 410
    client.cookies.clear()
    enroll(client, clock)
    client.cookies.set(SESSION_COOKIE, "forged")
    assert client.get("/api/admin/requests").status_code == 401
