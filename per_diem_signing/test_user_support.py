import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest
from fastapi.testclient import TestClient

from per_diem_signing.auth import HASHER, digest
from per_diem_signing.server import create_app
from per_diem_signing.test_accounts import accept, invite, request, sign
from per_diem_signing.test_auth import HEADERS, PASSWORD, enroll, environment
from per_diem_signing.test_server import KEY
from per_diem_signing.user_support import RESET_COOKIE

NEW_PASSWORD = "Another-test-only-password!"


@pytest.fixture
def team(environment):
    owner, folder, clock = environment
    enroll(owner, clock)
    app = create_app(folder, admin_key=KEY)
    with TestClient(app) as colleague:
        _, token = invite(owner)
        secret, codes = accept(colleague, token, "alice", clock)
        yield owner, colleague, folder, clock, secret, codes


def issue(owner, kind="both", user_id=2):
    response = owner.post(f"/api/auth/users/{user_id}/recovery", headers=HEADERS, json={"kind": kind})
    assert response.status_code == 201, response.text
    result = response.json()
    token = result["link"].rsplit("/", 1)[-1]
    return result, f"/api/auth/reset/{token}"


def start(client, endpoint, password=NEW_PASSWORD):
    response = client.post(f"{endpoint}/start", headers=HEADERS, json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()


def finish(client, endpoint, secret, clock):
    return client.post(f"{endpoint}/confirm", headers=HEADERS,
                       json={"code": pyotp.TOTP(secret).at(clock[0])})


def login(client, secret, clock, password=PASSWORD, username="alice"):
    return client.post("/api/auth/login", headers=HEADERS,
                       json={"username": username, "password": password,
                             "code": pyotp.TOTP(secret).at(clock[0])})


def test_profile_email_and_rename_preserve_ownership_and_invalidate_session(team):
    owner, colleague, folder, clock, secret, _ = team
    record = request(colleague)
    _, endpoint = issue(owner)
    start(colleague, endpoint)
    for body, expected in [
        ({"username": "admin", "email": ""}, 409),
        ({"username": "bad name", "email": ""}, 422),
        ({"username": "ok-user", "email": "not-an-email"}, 422),
        ({"username": "ok-user", "email": "user @example.com"}, 422),
    ]:
        assert owner.post("/api/auth/users/2/profile", headers=HEADERS, json=body).status_code == expected
    result = owner.post("/api/auth/users/2/profile", headers=HEADERS,
                        json={"username": "Alice.New", "email": "alice@example.com"})
    assert result.status_code == 200, result.text
    assert result.json()["username"] == "alice.new"
    assert colleague.get("/api/admin/requests").status_code == 401
    assert colleague.get(endpoint).status_code == 410
    clock[0] += 30
    assert login(colleague, secret, clock).status_code == 401
    assert login(colleague, secret, clock, username="ALICE.NEW").status_code == 200
    assert colleague.get("/api/admin/requests").json()["requests"][0]["id"] == record["id"]
    assert owner.get("/api/admin/requests").json()["requests"][0]["owner"] == "alice.new"
    user = owner.get("/api/auth/team").json()["users"][1]
    assert user["email"] == "alice@example.com" and user["last_login"] == clock[0]
    # Contact-only edits neither revoke a valid login nor change ownership.
    assert owner.post("/api/auth/users/2/profile", headers=HEADERS,
                      json={"username": "alice.new", "email": ""}).status_code == 200
    assert colleague.get("/api/admin/requests").status_code == 200
    with sqlite3.connect(folder / "requests.db") as db:
        assert db.execute("SELECT owner_id FROM requests WHERE id=?", (record["id"],)).fetchone()[0] == 2


def test_invitation_accepts_optional_contact_email_and_duplicate_rename(team):
    owner, _, folder, clock, _, _ = team
    _, token = invite(owner)
    with TestClient(create_app(folder, admin_key=KEY)) as second:
        result = second.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                             json={"username": "bob", "email": "bob@example.com", "password": PASSWORD})
        assert result.status_code == 200
        assert finish(second, "/api/auth", result.json()["secret"], clock).status_code == 200
    assert owner.get("/api/auth/team").json()["users"][2]["email"] == "bob@example.com"
    assert owner.post("/api/auth/users/2/profile", headers=HEADERS,
                      json={"username": "BOB", "email": ""}).status_code == 409


@pytest.mark.parametrize("kind", ["password", "authenticator", "both"])
def test_recovery_exact_credential_changes_and_old_sessions_invalidated(team, kind):
    owner, colleague, folder, clock, old_secret, old_codes = team
    record = request(colleague)
    old_session = colleague.cookies.get("perdiem_session")
    with sqlite3.connect(folder / "authentication.db") as db:
        original = db.execute("SELECT password_hash,secret,last_counter FROM users WHERE id=2").fetchone()
    link, endpoint = issue(owner, kind)
    assert link["expires"] == clock[0] + 3600
    assert colleague.get("/api/admin/requests").status_code == 401
    assert colleague.get(link["link"].replace("http://127.0.0.1:8200", "")).status_code == 200
    assert colleague.get(endpoint).json()["kind"] == kind
    if kind == "authenticator":
        assert colleague.post(f"{endpoint}/start", headers=HEADERS,
                              json={"password": "wrong"}).status_code == 401
    else:
        assert colleague.post(f"{endpoint}/start", headers=HEADERS,
                              json={"password": "short"}).status_code == 422
    data = start(colleague, endpoint, PASSWORD if kind == "authenticator" else NEW_PASSWORD)
    secret = old_secret if kind == "password" else data["secret"]
    assert ("secret" in data) == (kind != "password")
    assert colleague.cookies.get(RESET_COOKIE)
    with sqlite3.connect(folder / "authentication.db") as db:
        assert db.execute("SELECT password_hash,secret,last_counter FROM users WHERE id=2").fetchone() == original
        stored = db.execute("SELECT token_hash FROM user_resets WHERE id=?", (link["id"],)).fetchone()[0]
        assert stored == digest(endpoint.rsplit("/", 1)[-1])
    assert colleague.post(f"{endpoint}/confirm", headers=HEADERS, json={"code": "abcdef"}).status_code == 401
    if kind == "password":
        assert finish(colleague, endpoint, secret, clock).status_code == 401  # Already used at enrollment.
    clock[0] += 30
    completed = finish(colleague, endpoint, secret, clock)
    assert completed.status_code == 200, completed.text
    codes = completed.json()["recovery_codes"]
    assert len(codes) == (0 if kind == "password" else 10)
    assert colleague.cookies.get(RESET_COOKIE) is None
    assert colleague.get(endpoint).status_code == 410
    assert finish(colleague, endpoint, secret, clock).status_code == 410
    assert colleague.get("/api/auth/status").json()["authenticated"] is False
    with sqlite3.connect(folder / "authentication.db") as db:
        row = db.execute("SELECT password_hash,secret FROM users WHERE id=2").fetchone()
        assert HASHER.verify(row[0], PASSWORD if kind == "authenticator" else NEW_PASSWORD)
        assert row[1] == secret
    colleague.cookies.set("perdiem_session", old_session)
    assert colleague.get("/api/admin/requests").status_code == 401
    colleague.cookies.clear()
    clock[0] += 30
    if kind != "authenticator":
        assert login(colleague, secret, clock).status_code == 401
    password = PASSWORD if kind == "authenticator" else NEW_PASSWORD
    if kind != "password":
        assert login(colleague, old_secret, clock, password=password).status_code == 401
        assert colleague.post("/api/auth/login", headers=HEADERS, json={
            "username": "alice", "password": password, "code": old_codes[0]}).status_code == 401
    assert login(colleague, secret, clock, password=password).status_code == 200
    assert colleague.get("/api/admin/requests").json()["requests"][0]["id"] == record["id"]
    assert colleague.post("/api/auth/login", headers=HEADERS, json={
        "username": "alice", "password": password,
        "code": old_codes[1] if kind == "password" else codes[0]}).status_code == 200
    history = owner.get("/api/auth/recoveries").json()["recoveries"][0]
    assert history["used_at"] == clock[0] - 30
    assert "token_hash" not in history and "link" not in history


def test_revocation_restoration_blocks_sessions_and_unfinished_recovery_not_recipient_links(team):
    owner, colleague, _, clock, secret, _ = team
    record = request(colleague)
    _, endpoint = issue(owner)
    new = start(colleague, endpoint)["secret"]
    assert owner.post("/api/auth/users/2/disable", headers=HEADERS).status_code == 200
    assert colleague.get("/api/admin/requests").status_code == 401
    assert colleague.get(endpoint).status_code == 410
    assert finish(colleague, endpoint, new, clock).status_code == 410
    assert owner.post("/api/auth/users/2/recovery", headers=HEADERS,
                      json={"kind": "both"}).status_code == 409
    clock[0] += 30
    assert login(colleague, secret, clock).status_code == 401
    sign(colleague, record)
    assert owner.get(f"/api/admin/requests/{record['id']}/download").status_code == 200
    assert owner.post("/api/auth/users/2/enable", headers=HEADERS).status_code == 200
    assert colleague.get("/api/admin/requests").status_code == 401
    assert colleague.get(endpoint).status_code == 410
    assert login(colleague, secret, clock).status_code == 200
    assert colleague.get(f"/api/admin/requests/{record['id']}/download").status_code == 200


def test_unlock_clears_only_target_login_scope_and_recovers_lockout(team):
    owner, colleague, folder, clock, secret, _ = team
    for _ in range(10):
        assert login(colleague, secret, clock, password="wrong").status_code == 401
    assert login(colleague, secret, clock).status_code == 429
    assert owner.get("/api/auth/team").json()["users"][1]["failed_attempts"] == 10
    with sqlite3.connect(folder / "authentication.db") as db:
        db.execute("INSERT INTO failures VALUES (?,?)", (clock[0], "login:bob"))
    assert owner.post("/api/auth/users/2/unlock", headers=HEADERS).status_code == 200
    assert owner.get("/api/auth/team").json()["users"][1]["failed_attempts"] == 0
    clock[0] += 30
    assert login(colleague, secret, clock).status_code == 200
    with sqlite3.connect(folder / "authentication.db") as db:
        assert db.execute("SELECT COUNT(*) FROM failures WHERE scope='login:bob'").fetchone()[0] == 1


def test_recovery_expiry_supersession_revocation_binding_and_pending_timeout(team):
    owner, colleague, folder, clock, _, _ = team
    assert colleague.get("/api/auth/reset/unknown").status_code == 404
    link, endpoint = issue(owner)
    secret = start(colleague, endpoint)["secret"]
    next_link, next_endpoint = issue(owner)
    assert colleague.get(endpoint).status_code == 410
    assert finish(colleague, endpoint, secret, clock).status_code == 410
    assert finish(colleague, next_endpoint, secret, clock).status_code == 410
    secret = start(colleague, next_endpoint)["secret"]
    assert owner.post(f"/api/auth/recoveries/{next_link['id']}/revoke", headers=HEADERS).status_code == 200
    assert finish(colleague, next_endpoint, secret, clock).status_code == 410
    link, endpoint = issue(owner)
    secret = start(colleague, endpoint)["secret"]
    clock[0] += 601
    assert finish(colleague, endpoint, secret, clock).status_code == 410
    assert colleague.get(endpoint).status_code == 200
    secret = start(colleague, endpoint)["secret"]
    old_cookie = colleague.cookies.get(RESET_COOKIE)
    replacement = start(colleague, endpoint)["secret"]
    with TestClient(create_app(folder, admin_key=KEY)) as stale:
        stale.cookies.set(RESET_COOKIE, old_cookie, path="/api/auth")
        assert finish(stale, endpoint, secret, clock).status_code == 410
    with sqlite3.connect(folder / "authentication.db") as db:
        db.execute("UPDATE user_resets SET expires=? WHERE id=?", (clock[0] - 1, link["id"]))
    assert colleague.get(endpoint).status_code == 410
    assert finish(colleague, endpoint, replacement, clock).status_code == 410


def test_management_requires_superuser_and_same_origin_and_cannot_change_superuser(team):
    owner, colleague, folder, _, _, _ = team
    actions = [
        ("/profile", {"username": "changed", "email": ""}),
        ("/unlock", {}), ("/enable", {}), ("/disable", {}), ("/recovery", {"kind": "both"}),
    ]
    for action, body in actions:
        url = f"/api/auth/users/2{action}"
        assert colleague.post(url, headers=HEADERS, json=body).status_code == 403
        assert owner.post(url, json=body).status_code == 403
        assert owner.post(url, headers={**HEADERS, "Origin": "https://attacker.example"},
                          json=body).status_code == 403
        assert owner.post(f"/api/auth/users/1{action}", headers=HEADERS, json=body).status_code == 404
    assert colleague.get("/api/auth/recoveries").status_code == 403
    assert colleague.post("/api/auth/recoveries/unknown/revoke", headers=HEADERS).status_code == 403
    with TestClient(create_app(folder, admin_key=KEY)) as anonymous:
        assert anonymous.get("/api/auth/recoveries").status_code == 401
        for action, body in actions:
            assert anonymous.post(f"/api/auth/users/2{action}", headers=HEADERS, json=body).status_code == 401
    _, endpoint = issue(owner)
    assert colleague.post(f"{endpoint}/start", json={"password": NEW_PASSWORD}).status_code == 403
    assert colleague.post(f"{endpoint}/confirm", json={"code": "123456"}).status_code == 403


def test_confirmation_single_use_under_concurrency_and_throttled(team):
    owner, colleague, folder, clock, _, _ = team
    _, endpoint = issue(owner)
    data = start(colleague, endpoint)
    cookie = colleague.cookies.get(RESET_COOKIE)
    app = create_app(folder, admin_key=KEY)

    def complete(_):
        with TestClient(app) as contender:
            contender.cookies.set(RESET_COOKIE, cookie, path="/api/auth")
            return finish(contender, endpoint, data["secret"], clock).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(complete, range(2))) == [200, 410]
    _, endpoint = issue(owner)
    start(colleague, endpoint)
    for _ in range(10):
        assert colleague.post(f"{endpoint}/confirm", headers=HEADERS,
                              json={"code": "abcdef"}).status_code == 401
    assert colleague.post(f"{endpoint}/confirm", headers=HEADERS,
                          json={"code": "abcdef"}).status_code == 429


def test_schema_restart_preserves_credentials_codes_sessions_and_notices(team):
    owner, colleague, folder, _, _, _ = team
    record = request(colleague)
    with sqlite3.connect(folder / "authentication.db") as db:
        users = db.execute("SELECT * FROM users").fetchall()
        codes = db.execute("SELECT * FROM recovery").fetchall()
        sessions = db.execute("SELECT * FROM sessions").fetchall()
    for _ in range(2):
        with TestClient(create_app(folder, admin_key=KEY)) as restarted:
            assert "RSI-Industries" in restarted.get("/").text
            assert "RSI-Industries" in restarted.get("/recover/example").text
            assert "RSI-Industries" in restarted.get("/sign/example").text
    with sqlite3.connect(folder / "authentication.db") as db:
        assert db.execute("SELECT * FROM users").fetchall() == users
        assert db.execute("SELECT * FROM recovery").fetchall() == codes
        assert db.execute("SELECT * FROM sessions").fetchall() == sessions
    assert colleague.get("/api/admin/requests").json()["requests"][0]["id"] == record["id"]
    assert owner.get("/api/admin/requests").status_code == 200
