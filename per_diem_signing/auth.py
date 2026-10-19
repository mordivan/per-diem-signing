"""Invited accounts, TOTP, recovery codes and owner-scoped browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pyotp
import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

SESSION_COOKIE = "perdiem_session"
PENDING_COOKIE = "perdiem_enrollment"
SESSION_SECONDS = 8 * 60 * 60
PENDING_SECONDS = 10 * 60
HASHER = PasswordHasher()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Enrollment(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=128)


class Confirmation(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class Login(Confirmation):
    password: str = Field(min_length=1, max_length=128)
    username: str = Field(default="admin", min_length=1, max_length=40)


class InviteEnrollment(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    password: str = Field(min_length=12, max_length=128)


@dataclass(frozen=True)
class Account:
    id: int
    username: str
    is_superuser: bool

    def public(self) -> dict[str, str | int | bool]:
        return {"id": self.id, "username": self.username, "is_superuser": self.is_superuser}


class Authentication:
    def __init__(self, directory: Path, key: str, public_url: str):
        self.path = directory / "authentication.db"
        self.key = key
        self.secure = public_url.startswith("https://")
        self.public_url = public_url.rstrip("/")
        # A dummy hash makes unknown/unconfigured logins take normal password-check time.
        self.dummy_hash = HASHER.hash(secrets.token_urlsafe(32))
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, secret TEXT NOT NULL,
                    last_counter INTEGER NOT NULL, is_superuser INTEGER NOT NULL DEFAULT 0,
                    disabled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pending (
                    token_hash TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                    secret TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS recovery (
                    code_hash TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS failures (
                    occurred REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    created_by INTEGER NOT NULL, expires REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, used_by INTEGER);
            """)
            db.execute("BEGIN IMMEDIATE")
            for table, columns in {
                "pending": {"username": "TEXT NOT NULL DEFAULT 'admin'", "invitation_id": "TEXT"},
                "sessions": {"user_id": "INTEGER NOT NULL DEFAULT 1"},
                "recovery": {"user_id": "INTEGER NOT NULL DEFAULT 1"},
                "failures": {"scope": "TEXT NOT NULL DEFAULT 'setup'"},
            }.items():
                existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for column, declaration in columns.items():
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='administrator'").fetchone():
                db.execute("""INSERT INTO users
                    (id, username, password_hash, secret, last_counter, is_superuser)
                    SELECT id, 'admin', password_hash, secret, last_counter, 1 FROM administrator""")
                db.execute("DROP TABLE administrator")
        self.router = APIRouter(prefix="/api/auth")
        self.routes()

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def configured(self) -> bool:
        with self.db() as db:
            return db.execute("SELECT 1 FROM users WHERE is_superuser=1").fetchone() is not None

    def browser_request(self, request: Request) -> None:
        if request.headers.get("X-PerDiem-Request") != "1":
            raise HTTPException(403, "This action must be made from the signing website.")
        origin = request.headers.get("origin")
        allowed = {self.public_url}
        if not self.secure:
            allowed.add(str(request.base_url).rstrip("/"))
        if origin and origin not in allowed:
            raise HTTPException(403, "Request origin is not allowed.")

    def account_for_session(self, request: Request) -> Account | None:
        token = request.cookies.get(SESSION_COOKIE, "")
        with self.db() as db:
            row = db.execute("""SELECT u.id, u.username, u.is_superuser FROM users u
                JOIN sessions s ON s.user_id=u.id
                WHERE s.token_hash=? AND s.expires>? AND u.disabled=0""",
                (digest(token), time.time())).fetchone()
        return Account(row["id"], row["username"], bool(row["is_superuser"])) if row else None

    def require_admin(self, request: Request) -> Account:
        if not self.configured():
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if hmac.compare_digest(supplied.encode(), self.key.encode()):
                return Account(1, "admin", True)
            raise HTTPException(401, "Set up password and authenticator login first.")
        account = self.account_for_session(request)
        if account is None:
            raise HTTPException(401, "Your session has expired. Please sign in again.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.browser_request(request)
        return account

    def require_superuser(self, request: Request) -> Account:
        account = self.require_admin(request)
        if not self.configured() or not account.is_superuser:
            raise HTTPException(403, "Only the superuser can manage colleagues and invitations.")
        return account

    def throttle(self, scope: str = "setup") -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM failures WHERE occurred <= ?", (time.time() - 900,))
            if db.execute("SELECT COUNT(*) FROM failures WHERE scope=?", (scope,)).fetchone()[0] >= 10:
                raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.",
                                    headers={"Retry-After": "900"})
            # Reserve an attempt before doing expensive verification; remove it on success.
            db.execute("INSERT INTO failures (occurred, scope) VALUES (?, ?)", (time.time(), scope))

    def success(self, db: sqlite3.Connection, scope: str = "setup") -> None:
        db.execute("DELETE FROM failures WHERE scope=?", (scope,))

    @staticmethod
    def password_matches(encoded: str, password: str) -> bool:
        try:
            return HASHER.verify(encoded, password)
        except VerificationError:
            return False

    @staticmethod
    def counter(secret: str, code: str, last: int = -1) -> int | None:
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            return None
        current = int(time.time()) // 30
        otp = pyotp.TOTP(secret)
        for value in (current, current - 1, current + 1):
            if value > last and hmac.compare_digest(otp.generate_otp(value), code):
                return value
        return None

    def session(self, db: sqlite3.Connection, response: Response, user_id: int) -> None:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        db.execute("INSERT INTO sessions (token_hash, expires, user_id) VALUES (?, ?, ?)",
                   (digest(token), now + SESSION_SECONDS, user_id))
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS,
                            secure=self.secure, httponly=True, samesite="strict", path="/")
        response.delete_cookie(PENDING_COOKIE, path="/api/auth",
                               secure=self.secure, httponly=True, samesite="strict")

    @staticmethod
    def recovery_codes(db: sqlite3.Connection, user_id: int) -> list[str]:
        codes = [secrets.token_hex(10).upper() for _ in range(10)]
        db.execute("DELETE FROM recovery WHERE user_id=?", (user_id,))
        for code in codes:
            db.execute("INSERT INTO recovery (code_hash, user_id) VALUES (?, ?)", (digest(code), user_id))
        return ["-".join(code[i:i + 5] for i in range(0, 20, 5)) for code in codes]

    @staticmethod
    def invitation(db: sqlite3.Connection, *, token: str | None = None, invitation_id: str | None = None):
        if token is not None:
            row = db.execute("SELECT * FROM invitations WHERE token_hash=?", (digest(token),)).fetchone()
        else:
            row = db.execute("SELECT * FROM invitations WHERE id=?", (invitation_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Invitation not found.")
        if row["revoked"] or row["used_by"] is not None or row["expires"] <= time.time():
            raise HTTPException(410, "This invitation has expired, been revoked, or already been used.")
        return row

    def begin(self, password: str, response: Response, username: str = "admin",
              invitation_id: str | None = None) -> dict[str, str | int]:
        token = secrets.token_urlsafe(32)
        secret = pyotp.random_base32()
        encoded = HASHER.hash(password)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if invitation_id:
                self.invitation(db, invitation_id=invitation_id)
            elif db.execute("SELECT 1 FROM users WHERE is_superuser=1").fetchone():
                raise HTTPException(409, "Authenticator login is already configured.")
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise HTTPException(409, "That username is already in use.")
            db.execute("DELETE FROM pending WHERE expires<=?", (time.time(),))
            db.execute("""INSERT INTO pending
                (token_hash, password_hash, secret, expires, username, invitation_id)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (digest(token), encoded, secret, time.time() + PENDING_SECONDS, username, invitation_id))
            self.success(db, f"invite:{invitation_id}" if invitation_id else "setup")
        response.set_cookie(PENDING_COOKIE, token, max_age=PENDING_SECONDS,
                            secure=self.secure, httponly=True, samesite="strict", path="/api/auth")
        uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Per Diem Signing")
        image = qrcode.make(uri)
        data = io.BytesIO()
        image.save(data, format="PNG")
        return {"secret": secret, "qr": base64.b64encode(data.getvalue()).decode("ascii"),
                "expires_seconds": PENDING_SECONDS}

    def routes(self) -> None:
        router = self.router

        @router.get("/status")
        def status(request: Request):
            configured = self.configured()
            account = self.account_for_session(request) if configured else None
            result = {"configured": configured, "authenticated": account is not None}
            if account:
                result["user"] = account.public()
            return result

        @router.post("/enroll", dependencies=[Depends(self.browser_request)])
        def enroll(body: Enrollment, response: Response):
            self.throttle()
            if self.configured():
                raise HTTPException(409, "Authenticator login is already configured.")
            if not hmac.compare_digest(body.key.encode(), self.key.encode()):
                raise HTTPException(401, "Incorrect setup key.")
            return self.begin(body.password, response)

        @router.post("/confirm", dependencies=[Depends(self.browser_request)])
        def confirm(body: Confirmation, request: Request, response: Response):
            pending_hash = digest(request.cookies.get(PENDING_COOKIE, ""))
            scope = f"confirm:{pending_hash}"
            self.throttle(scope)
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                pending = db.execute("SELECT * FROM pending WHERE token_hash=?",
                                     (pending_hash,)).fetchone()
                if pending is None or pending["expires"] <= time.time():
                    raise HTTPException(410, "Setup expired. Start setup again.")
                invitation_id = pending["invitation_id"]
                if invitation_id:
                    self.invitation(db, invitation_id=invitation_id)
                elif db.execute("SELECT 1 FROM users WHERE is_superuser=1").fetchone():
                    raise HTTPException(409, "Authenticator login is already configured.")
                if db.execute("SELECT 1 FROM users WHERE username=?", (pending["username"],)).fetchone():
                    raise HTTPException(409, "That username is already in use. Start setup again.")
                counter = self.counter(pending["secret"], body.code)
                if counter is None:
                    raise HTTPException(401, "Incorrect authenticator code. Check your phone's time.")
                result = db.execute("""INSERT INTO users
                    (username, password_hash, secret, last_counter, is_superuser) VALUES (?, ?, ?, ?, ?)""",
                    (pending["username"], pending["password_hash"], pending["secret"], counter,
                     0 if invitation_id else 1))
                user_id = result.lastrowid
                if invitation_id:
                    db.execute("UPDATE invitations SET used_by=? WHERE id=?", (user_id, invitation_id))
                db.execute("DELETE FROM pending WHERE token_hash=?", (pending_hash,))
                codes = self.recovery_codes(db, user_id)
                self.session(db, response, user_id)
                self.success(db, scope)
            return {"recovery_codes": codes}

        @router.post("/login", dependencies=[Depends(self.browser_request)])
        def login(body: Login, response: Response):
            username = body.username.strip().lower()
            scope = f"login:{username}"
            self.throttle(scope)
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                admin = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                encoded = admin["password_hash"] if admin else self.dummy_hash
                matched = self.password_matches(encoded, body.password)
                if admin is None or not matched or admin["disabled"]:
                    raise HTTPException(401, "Incorrect password or code.")
                counter = self.counter(admin["secret"], body.code, admin["last_counter"])
                recovery = False
                if counter is None:
                    normalized = body.code.replace("-", "").replace(" ", "").upper()
                    result = db.execute("DELETE FROM recovery WHERE code_hash=? AND user_id=?",
                                        (digest(normalized), admin["id"]))
                    if not result.rowcount:
                        raise HTTPException(401, "Incorrect password or code. Each code can be used only once.")
                    recovery = True
                else:
                    db.execute("UPDATE users SET last_counter=? WHERE id=?", (counter, admin["id"]))
                if HASHER.check_needs_rehash(admin["password_hash"]):
                    db.execute("UPDATE users SET password_hash=? WHERE id=?",
                               (HASHER.hash(body.password), admin["id"]))
                self.session(db, response, admin["id"])
                self.success(db, scope)
            return {"authenticated": True, "used_recovery_code": recovery}

        @router.post("/logout", dependencies=[Depends(self.browser_request)])
        def logout(request: Request, response: Response):
            with self.db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?",
                           (digest(request.cookies.get(SESSION_COOKIE, "")),))
            response.delete_cookie(SESSION_COOKIE, path="/", secure=self.secure,
                                   httponly=True, samesite="strict")
            return {"authenticated": False}

        @router.get("/team")
        def team(account: Account = Depends(self.require_superuser)):
            with self.db() as db:
                users = db.execute("SELECT id, username, is_superuser, disabled FROM users ORDER BY id").fetchall()
                invitations = db.execute("SELECT id, expires, revoked, used_by FROM invitations ORDER BY rowid DESC").fetchall()
            return {"users": [dict(row) for row in users],
                    "invitations": [dict(row) for row in invitations]}

        @router.post("/invitations", dependencies=[Depends(self.browser_request)], status_code=201)
        def invite(account: Account = Depends(self.require_superuser)):
            invitation_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            expires = time.time() + 48 * 60 * 60
            with self.db() as db:
                db.execute("""INSERT INTO invitations (id, token_hash, created_by, expires)
                    VALUES (?, ?, ?, ?)""", (invitation_id, digest(token), account.id, expires))
            return {"id": invitation_id, "expires": expires,
                    "link": f"{self.public_url}/join/{token}"}

        @router.post("/invitations/{invitation_id}/revoke", dependencies=[Depends(self.browser_request)])
        def revoke(invitation_id: str, account: Account = Depends(self.require_superuser)):
            with self.db() as db:
                result = db.execute("UPDATE invitations SET revoked=1 WHERE id=? AND used_by IS NULL",
                                    (invitation_id,))
                if not result.rowcount:
                    raise HTTPException(404, "No unused invitation found.")
            return {"revoked": True}

        @router.post("/users/{user_id}/disable", dependencies=[Depends(self.browser_request)])
        def disable(user_id: int, account: Account = Depends(self.require_superuser)):
            with self.db() as db:
                result = db.execute("UPDATE users SET disabled=1 WHERE id=? AND is_superuser=0", (user_id,))
                if not result.rowcount:
                    raise HTTPException(404, "Colleague account not found.")
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return {"disabled": True}

        @router.get("/invitation/{token}")
        def invitation_status(token: str):
            with self.db() as db:
                row = self.invitation(db, token=token)
            return {"expires": row["expires"]}

        @router.post("/invitation/{token}/enroll", dependencies=[Depends(self.browser_request)])
        def enroll_invitation(token: str, body: InviteEnrollment, response: Response):
            scope = f"invite-token:{digest(token)}"
            self.throttle(scope)
            username = body.username.lower()
            if username == "admin":
                raise HTTPException(409, "The username admin is reserved for the superuser.")
            with self.db() as db:
                invitation = self.invitation(db, token=token)
            result = self.begin(body.password, response, username, invitation["id"])
            with self.db() as db:
                self.success(db, scope)
            return result
