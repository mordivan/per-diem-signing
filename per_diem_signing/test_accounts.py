import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pyotp
from fastapi.testclient import TestClient

from per_diem_signing.auth import HASHER, SESSION_COOKIE, digest
from per_diem_signing.server import create_app
from per_diem_signing.test_auth import HEADERS, PASSWORD, enroll, environment
from per_diem_signing.test_server import DETAILS, KEY, image_bytes


def invite(client):
    result = client.post("/api/auth/invitations", headers=HEADERS)
    assert result.status_code == 201, result.text
    data = result.json()
    return data, data["link"].rsplit("/", 1)[-1]


def accept(client, token, username, clock):
    response = client.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                           json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    secret = response.json()["secret"]
    result = client.post("/api/auth/confirm", headers=HEADERS,
                         json={"code": pyotp.TOTP(secret).at(clock[0])})
    assert result.status_code == 200, result.text
    return secret, result.json()["recovery_codes"]


def request(client):
    result = client.post("/api/admin/requests", headers=HEADERS, json=DETAILS)
    assert result.status_code == 201, result.text
    return result.json()


def sign(client, record):
    token = record["link"].rsplit("/", 1)[-1]
    result = client.post(f"/api/sign/{token}/submit",
                         data={"name": "Recipient", "consent": "yes"},
                         files={"signature": ("signature.png", image_bytes(True), "image/png")})
    assert result.status_code == 200, result.text


def test_superuser_sees_all_colleagues_are_isolated_on_every_request_endpoint(environment):
    owner, folder, clock = environment
    enroll(owner, clock)
    owner_request = request(owner)
    _, token = invite(owner)
    app = create_app(folder, admin_key=KEY)
    with TestClient(app) as colleague, TestClient(app) as second:
        accept(colleague, token, "Alice", clock)
        assert colleague.get("/api/auth/status").json()["user"]["username"] == "alice"
        own_pending = request(colleague)
        own_signed = request(colleague)
        sign(colleague, own_signed)
        _, token2 = invite(owner)
        accept(second, token2, "bob", clock)
        other = request(second)
        sign(second, owner_request)
        assert {r["id"] for r in owner.get("/api/admin/requests").json()["requests"]} == {
            owner_request["id"], own_pending["id"], own_signed["id"], other["id"]}
        visible = colleague.get("/api/admin/requests").json()["requests"]
        assert {r["id"] for r in visible} == {own_pending["id"], own_signed["id"]}
        assert {r["owner"] for r in visible} == {"alice"}
        for record in (owner_request, other):
            assert colleague.get(f"/api/admin/requests/{record['id']}/download").status_code == 404
            assert colleague.post(f"/api/admin/requests/{record['id']}/revoke",
                                  headers=HEADERS).status_code == 404
        assert colleague.get(f"/api/admin/requests/{own_signed['id']}/download").status_code == 200
        assert owner.get(f"/api/admin/requests/{own_signed['id']}/download").status_code == 200
        assert owner.post(f"/api/admin/requests/{other['id']}/revoke", headers=HEADERS).status_code == 200
        assert colleague.post(f"/api/admin/requests/{own_pending['id']}/revoke",
                              headers=HEADERS).status_code == 200
        forged = colleague.post("/api/admin/requests", headers=HEADERS,
                                json={**DETAILS, "owner_id": 1, "is_superuser": True})
        assert forged.status_code == 201
        ids = {r["id"] for r in second.get("/api/admin/requests").json()["requests"]}
        assert ids == {other["id"]}
        assert colleague.get("/api/auth/team").status_code == 403
        assert colleague.post("/api/auth/invitations", headers=HEADERS).status_code == 403
        assert colleague.post("/api/auth/users/1/disable", headers=HEADERS).status_code == 403
        assert colleague.post("/api/auth/invitations/unknown/revoke", headers=HEADERS).status_code == 403
        # A superuser's role cannot be requested through a colleague invitation.
        with sqlite3.connect(folder / "authentication.db") as db:
            assert db.execute("SELECT is_superuser FROM users WHERE username='alice'").fetchone()[0] == 0
        alice = next(u for u in owner.get("/api/auth/team").json()["users"] if u["username"] == "alice")
        assert owner.post(f"/api/auth/users/{alice['id']}/disable", headers=HEADERS).status_code == 200
        assert colleague.get("/api/admin/requests").status_code == 401
        clock[0] += 30
        assert colleague.post("/api/auth/login", headers=HEADERS, json={
            "username": "alice", "password": PASSWORD, "code": "123456"}).status_code == 401
        assert owner.post("/api/auth/users/1/disable", headers=HEADERS).status_code == 404
        assert len(owner.get("/api/admin/requests").json()["requests"]) == 5


def test_invitation_expiry_revocation_and_single_use(environment):
    owner, folder, clock = environment
    enroll(owner, clock)
    app = create_app(folder, admin_key=KEY)
    with TestClient(app) as colleague:
        assert colleague.get("/api/auth/invitation/not-real").status_code == 404
        data, token = invite(owner)
        assert data["expires"] == clock[0] + 48 * 3600
        assert colleague.get(f"/join/{token}").status_code == 200
        response = colleague.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                                  json={"username": "alice", "password": PASSWORD})
        secret = response.json()["secret"]
        assert owner.post(f"/api/auth/invitations/{data['id']}/revoke", headers=HEADERS).status_code == 200
        assert colleague.post("/api/auth/confirm", headers=HEADERS,
                              json={"code": pyotp.TOTP(secret).at(clock[0])}).status_code == 410
        data, token = invite(owner)
        with sqlite3.connect(folder / "authentication.db") as db:
            db.execute("UPDATE invitations SET expires=? WHERE id=?", (clock[0] - 1, data["id"]))
        assert colleague.get(f"/api/auth/invitation/{token}").status_code == 410
        assert colleague.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                              json={"username": "alice", "password": PASSWORD}).status_code == 410
        data, token = invite(owner)
        accept(colleague, token, "alice", clock)
        assert colleague.get(f"/api/auth/invitation/{token}").status_code == 410
        assert colleague.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                              json={"username": "eve", "password": PASSWORD}).status_code == 410
        with sqlite3.connect(folder / "authentication.db") as db:
            saved = db.execute("SELECT token_hash, used_by FROM invitations WHERE id=?", (data["id"],)).fetchone()
            assert saved[0] == digest(token) and saved[1] == 2


def test_invitation_actions_require_session_and_same_origin(environment):
    owner, _, clock = environment
    enroll(owner, clock)
    assert owner.post("/api/auth/invitations").status_code == 403
    assert owner.post("/api/auth/invitations", headers={
        **HEADERS, "Origin": "https://attacker.example"}).status_code == 403
    owner.cookies.clear()
    assert owner.get("/api/auth/team").status_code == 401
    assert owner.post("/api/auth/invitations", headers=HEADERS).status_code == 401


def test_usernames_recovery_and_login_are_per_account(environment):
    owner, folder, clock = environment
    _, admin_codes = enroll(owner, clock)
    app = create_app(folder, admin_key=KEY)
    with TestClient(app) as alice, TestClient(app) as bob:
        _, token = invite(owner)
        alice_secret, alice_codes = accept(alice, token, "Alice", clock)
        _, token = invite(owner)
        for username, status in (("ADMIN", 409), ("ALICE", 409), ("bad name", 422)):
            assert bob.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                            json={"username": username, "password": PASSWORD}).status_code == status
        accept(bob, token, "bob", clock)
        bob.cookies.clear()
        assert bob.post("/api/auth/login", headers=HEADERS, json={
            "username": "bob", "password": PASSWORD, "code": alice_codes[0]}).status_code == 401
        alice.cookies.clear()
        assert alice.post("/api/auth/login", headers=HEADERS, json={
            "username": "ALICE", "password": PASSWORD, "code": alice_codes[0]}).status_code == 200
        owner.cookies.clear()
        assert owner.post("/api/auth/login", headers=HEADERS, json={
            "password": PASSWORD, "code": admin_codes[0]}).status_code == 200
        alice.cookies.clear()
        for _ in range(10):
            assert alice.post("/api/auth/login", headers=HEADERS, json={
                "username": "alice", "password": "wrong", "code": "123456"}).status_code == 401
        assert alice.post("/api/auth/login", headers=HEADERS, json={
            "username": "alice", "password": PASSWORD,
            "code": pyotp.TOTP(alice_secret).at(clock[0])}).status_code == 429
        owner.cookies.clear()
        assert owner.post("/api/auth/login", headers=HEADERS, json={
            "username": "admin", "password": PASSWORD, "code": admin_codes[1]}).status_code == 200


def test_concurrent_invitation_acceptance_creates_only_one_user(environment):
    owner, folder, clock = environment
    enroll(owner, clock)
    _, token = invite(owner)
    app = create_app(folder, admin_key=KEY)
    with TestClient(app) as alice, TestClient(app) as bob:
        pending = []
        for client, username in ((alice, "alice"), (bob, "bob")):
            response = client.post(f"/api/auth/invitation/{token}/enroll", headers=HEADERS,
                                   json={"username": username, "password": PASSWORD})
            assert response.status_code == 200
            pending.append((client, response.json()["secret"]))
        def confirm(pair):
            client, secret = pair
            return client.post("/api/auth/confirm", headers=HEADERS,
                               json={"code": pyotp.TOTP(secret).at(clock[0])}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(confirm, pending)) == [200, 410]
        assert len(owner.get("/api/auth/team").json()["users"]) == 2


def test_legacy_migration_keeps_existing_login_sessions_recovery_and_requests(tmp_path, monkeypatch):
    clock = 1_800_000_000.0
    monkeypatch.setattr("per_diem_signing.auth.time.time", lambda: clock)
    secret = pyotp.random_base32()
    with sqlite3.connect(tmp_path / "authentication.db") as db:
        db.executescript("""
            CREATE TABLE administrator (id INTEGER PRIMARY KEY CHECK(id=1),
                password_hash TEXT NOT NULL, secret TEXT NOT NULL, last_counter INTEGER NOT NULL);
            CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE recovery (code_hash TEXT PRIMARY KEY);
            CREATE TABLE pending (token_hash TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                secret TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE failures (occurred REAL NOT NULL);
        """)
        db.execute("INSERT INTO administrator VALUES (1, ?, ?, ?)",
                   (HASHER.hash(PASSWORD), secret, -1))
        db.execute("INSERT INTO sessions VALUES (?, ?)", (digest("legacy-session"), clock + 1000))
        db.execute("INSERT INTO recovery VALUES (?)", (digest("LEGACYRECOVERY"),))
    with sqlite3.connect(tmp_path / "requests.db") as db:
        db.executescript("""CREATE TABLE requests (
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, details TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, signed_at TEXT,
            revoked INTEGER NOT NULL DEFAULT 0, filename TEXT, signer_name TEXT,
            output_sha256 TEXT, consent_text TEXT);
            INSERT INTO requests (id, token_hash, details, created_at, expires_at)
            VALUES ('legacy', 'legacy-hash', '{}', '2026-10-19', '2027-10-19');""")
    for _ in range(2):
        with TestClient(create_app(tmp_path, admin_key=KEY)) as owner:
            owner.cookies.set(SESSION_COOKIE, "legacy-session")
            status = owner.get("/api/auth/status").json()
            assert status["user"] == {"id": 1, "username": "admin", "is_superuser": True}
            records = owner.get("/api/admin/requests").json()["requests"]
            assert records[0]["id"] == "legacy" and records[0]["owner"] == "admin"
    with TestClient(create_app(tmp_path, admin_key=KEY)) as owner:
        assert owner.post("/api/auth/login", headers=HEADERS, json={
            "username": "admin", "password": PASSWORD, "code": "LEGACYRECOVERY"}).status_code == 200
        data, token = invite(owner)
        with TestClient(owner.app) as colleague:
            accept(colleague, token, "alice", [clock])
            assert colleague.get("/api/admin/requests").json()["requests"] == []
            assert colleague.post("/api/admin/requests/legacy/revoke", headers=HEADERS).status_code == 404
