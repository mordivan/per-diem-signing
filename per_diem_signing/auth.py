"""Single-administrator enrollment, TOTP, recovery codes and browser sessions."""

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
                CREATE TABLE IF NOT EXISTS administrator (
                    id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL,
                    secret TEXT NOT NULL, last_counter INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pending (
                    token_hash TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                    secret TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS recovery (
                    code_hash TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS failures (
                    occurred REAL NOT NULL);
            """)
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
            return db.execute("SELECT 1 FROM administrator").fetchone() is not None

    def browser_request(self, request: Request) -> None:
        if request.headers.get("X-PerDiem-Request") != "1":
            raise HTTPException(403, "This action must be made from the signing website.")
        origin = request.headers.get("origin")
        allowed = {self.public_url}
        if not self.secure:
            allowed.add(str(request.base_url).rstrip("/"))
        if origin and origin not in allowed:
            raise HTTPException(403, "Request origin is not allowed.")

    def require_admin(self, request: Request) -> None:
        if not self.configured():
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if hmac.compare_digest(supplied.encode(), self.key.encode()):
                return
            raise HTTPException(401, "Set up password and authenticator login first.")
        token = request.cookies.get(SESSION_COOKIE, "")
        with self.db() as db:
            row = db.execute("SELECT expires FROM sessions WHERE token_hash=?",
                             (digest(token),)).fetchone()
        if row is None or row["expires"] <= time.time():
            raise HTTPException(401, "Your session has expired. Please sign in again.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.browser_request(request)

    def throttle(self) -> None:
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM failures WHERE occurred <= ?", (time.time() - 900,))
            if db.execute("SELECT COUNT(*) FROM failures").fetchone()[0] >= 10:
                raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.",
                                    headers={"Retry-After": "900"})
            # Reserve an attempt before doing expensive verification; remove it on success.
            db.execute("INSERT INTO failures VALUES (?)", (time.time(),))

    def success(self, db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM failures")

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

    def session(self, db: sqlite3.Connection, response: Response) -> None:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?)", (digest(token), now + SESSION_SECONDS))
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS,
                            secure=self.secure, httponly=True, samesite="strict", path="/")
        response.delete_cookie(PENDING_COOKIE, path="/api/auth",
                               secure=self.secure, httponly=True, samesite="strict")

    @staticmethod
    def recovery_codes(db: sqlite3.Connection) -> list[str]:
        codes = [secrets.token_hex(10).upper() for _ in range(10)]
        db.execute("DELETE FROM recovery")
        for code in codes:
            db.execute("INSERT INTO recovery VALUES (?)", (digest(code),))
        return ["-".join(code[i:i + 5] for i in range(0, 20, 5)) for code in codes]

    def begin(self, password: str, response: Response) -> dict[str, str | int]:
        token = secrets.token_urlsafe(32)
        secret = pyotp.random_base32()
        encoded = HASHER.hash(password)
        with self.db() as db:
            db.execute("DELETE FROM pending")
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)",
                       (digest(token), encoded, secret, time.time() + PENDING_SECONDS))
            self.success(db)
        response.set_cookie(PENDING_COOKIE, token, max_age=PENDING_SECONDS,
                            secure=self.secure, httponly=True, samesite="strict", path="/api/auth")
        uri = pyotp.TOTP(secret).provisioning_uri(name="Administrator", issuer_name="Per Diem Signing")
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
            authenticated = False
            if configured:
                with self.db() as db:
                    token = request.cookies.get(SESSION_COOKIE, "")
                    row = db.execute("SELECT expires FROM sessions WHERE token_hash=?",
                                     (digest(token),)).fetchone()
                    authenticated = row is not None and row["expires"] > time.time()
            return {"configured": configured, "authenticated": authenticated}

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
            self.throttle()
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM administrator").fetchone():
                    raise HTTPException(409, "Authenticator login is already configured.")
                pending = db.execute("SELECT * FROM pending WHERE token_hash=?",
                                     (digest(request.cookies.get(PENDING_COOKIE, "")),)).fetchone()
                if pending is None or pending["expires"] <= time.time():
                    raise HTTPException(410, "Setup expired. Start setup again.")
                counter = self.counter(pending["secret"], body.code)
                if counter is None:
                    raise HTTPException(401, "Incorrect authenticator code. Check your phone's time.")
                db.execute("INSERT INTO administrator VALUES (1, ?, ?, ?)",
                           (pending["password_hash"], pending["secret"], counter))
                db.execute("DELETE FROM pending")
                codes = self.recovery_codes(db)
                self.session(db, response)
                self.success(db)
            return {"recovery_codes": codes}

        @router.post("/login", dependencies=[Depends(self.browser_request)])
        def login(body: Login, response: Response):
            self.throttle()
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                admin = db.execute("SELECT * FROM administrator").fetchone()
                encoded = admin["password_hash"] if admin else self.dummy_hash
                matched = self.password_matches(encoded, body.password)
                if admin is None or not matched:
                    raise HTTPException(401, "Incorrect password or code.")
                counter = self.counter(admin["secret"], body.code, admin["last_counter"])
                recovery = False
                if counter is None:
                    normalized = body.code.replace("-", "").replace(" ", "").upper()
                    result = db.execute("DELETE FROM recovery WHERE code_hash=?", (digest(normalized),))
                    if not result.rowcount:
                        raise HTTPException(401, "Incorrect password or code. Each code can be used only once.")
                    recovery = True
                else:
                    db.execute("UPDATE administrator SET last_counter=? WHERE id=1", (counter,))
                if HASHER.check_needs_rehash(admin["password_hash"]):
                    db.execute("UPDATE administrator SET password_hash=? WHERE id=1",
                               (HASHER.hash(body.password),))
                self.session(db, response)
                self.success(db)
            return {"authenticated": True, "used_recovery_code": recovery}

        @router.post("/logout", dependencies=[Depends(self.browser_request)])
        def logout(request: Request, response: Response):
            with self.db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?",
                           (digest(request.cookies.get(SESSION_COOKIE, "")),))
            response.delete_cookie(SESSION_COOKIE, path="/", secure=self.secure,
                                   httponly=True, samesite="strict")
            return {"authenticated": False}
