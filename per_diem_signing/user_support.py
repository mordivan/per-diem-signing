"""Superuser-assisted account changes and single-use credential recovery."""

from __future__ import annotations

import base64
import io
import secrets
import sqlite3
import time
from typing import Literal

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import Account, AccountDetails, Authentication, HASHER, digest

RESET_COOKIE = "perdiem_reset"
RESET_SECONDS = 3600


class ResetKind(BaseModel):
    kind: Literal["password", "authenticator", "both"]


class ResetStart(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ResetConfirm(BaseModel):
    code: str = Field(min_length=6, max_length=32)


def support_routes(auth: Authentication) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    with auth.db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS user_resets (
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL, created_by INTEGER NOT NULL,
                kind TEXT NOT NULL, expires REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0, used_at REAL);
            CREATE TABLE IF NOT EXISTS reset_pending (
                token_hash TEXT PRIMARY KEY, reset_id TEXT NOT NULL,
                password_hash TEXT NOT NULL, secret TEXT NOT NULL,
                expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS account_actions (
                id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, action TEXT NOT NULL, occurred REAL NOT NULL);
        """)

    def colleague(db: sqlite3.Connection, user_id: int):
        row = db.execute("SELECT * FROM users WHERE id=? AND is_superuser=0", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Colleague account not found. These controls cannot change the superuser.")
        return row

    def audit(db: sqlite3.Connection, actor_id: int, user_id: int, action: str) -> None:
        db.execute("INSERT INTO account_actions (actor_id,user_id,action,occurred) VALUES (?,?,?,?)",
                   (actor_id, user_id, action, time.time()))

    def reset_record(db: sqlite3.Connection, *, token: str | None = None, reset_id: str | None = None):
        if token is not None:
            row = db.execute("SELECT * FROM user_resets WHERE token_hash=?", (digest(token),)).fetchone()
        else:
            row = db.execute("SELECT * FROM user_resets WHERE id=?", (reset_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Recovery link not found.")
        if row["revoked"] or row["used_at"] is not None or row["expires"] <= time.time():
            raise HTTPException(410, "Recovery link expired, revoked, or already used. Ask the superuser for a new one.")
        user = colleague(db, row["user_id"])
        if user["disabled"]:
            raise HTTPException(410, "This account is disabled. Contact the superuser.")
        return row, user

    @router.post("/users/{user_id}/profile", dependencies=[Depends(auth.browser_request)])
    def update_profile(user_id: int, body: AccountDetails, actor: Account = Depends(auth.require_superuser)):
        username = body.username.lower()
        if username == "admin":
            raise HTTPException(409, "The username admin is reserved for the superuser.")
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = colleague(db, user_id)
            if db.execute("SELECT 1 FROM users WHERE username=? AND id!=?", (username, user_id)).fetchone():
                raise HTTPException(409, "That username is already in use.")
            db.execute("UPDATE users SET username=?,email=? WHERE id=?", (username, body.email, user_id))
            if username != old["username"]:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                db.execute("DELETE FROM failures WHERE scope IN (?,?)",
                           (f"login:{old['username']}", f"login:{username}"))
                db.execute("UPDATE user_resets SET revoked=1 WHERE user_id=? AND used_at IS NULL", (user_id,))
            audit(db, actor.id, user_id, "profile_updated")
        return {"updated": True, "username": username, "email": body.email}

    @router.post("/users/{user_id}/unlock", dependencies=[Depends(auth.browser_request)])
    def unlock(user_id: int, actor: Account = Depends(auth.require_superuser)):
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            user = colleague(db, user_id)
            auth.success(db, f"login:{user['username']}")
            audit(db, actor.id, user_id, "login_unlocked")
        return {"unlocked": True}

    @router.post("/users/{user_id}/enable", dependencies=[Depends(auth.browser_request)])
    def enable(user_id: int, actor: Account = Depends(auth.require_superuser)):
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            user = colleague(db, user_id)
            db.execute("UPDATE users SET disabled=0 WHERE id=?", (user_id,))
            auth.success(db, f"login:{user['username']}")
            audit(db, actor.id, user_id, "account_enabled")
        return {"enabled": True}

    @router.post("/users/{user_id}/recovery", dependencies=[Depends(auth.browser_request)], status_code=201)
    def create_recovery(user_id: int, body: ResetKind, actor: Account = Depends(auth.require_superuser)):
        token, reset_id = secrets.token_urlsafe(32), secrets.token_hex(16)
        expires = time.time() + RESET_SECONDS
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            user = colleague(db, user_id)
            if user["disabled"]:
                raise HTTPException(409, "Enable the account before issuing a recovery link.")
            db.execute("UPDATE user_resets SET revoked=1 WHERE user_id=? AND used_at IS NULL", (user_id,))
            db.execute("""INSERT INTO user_resets
                (id,token_hash,user_id,created_by,kind,expires) VALUES (?,?,?,?,?,?)""",
                (reset_id, digest(token), user_id, actor.id, body.kind, expires))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(db, actor.id, user_id, f"recovery_issued:{body.kind}")
        return {"id": reset_id, "expires": expires, "link": f"{auth.public_url}/recover/{token}"}

    @router.get("/recoveries")
    def recoveries(actor: Account = Depends(auth.require_superuser)):
        with auth.db() as db:
            rows = db.execute("""SELECT r.id,r.user_id,u.username,r.kind,r.expires,r.revoked,r.used_at
                FROM user_resets r JOIN users u ON r.user_id=u.id ORDER BY r.rowid DESC LIMIT 100""").fetchall()
        return {"recoveries": [dict(row) for row in rows]}

    @router.post("/recoveries/{reset_id}/revoke", dependencies=[Depends(auth.browser_request)])
    def revoke(reset_id: str, actor: Account = Depends(auth.require_superuser)):
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM user_resets WHERE id=? AND used_at IS NULL", (reset_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Unused recovery link not found.")
            db.execute("UPDATE user_resets SET revoked=1 WHERE id=?", (reset_id,))
            audit(db, actor.id, row["user_id"], "recovery_revoked")
        return {"revoked": True}

    @router.get("/reset/{token}")
    def reset_status(token: str):
        with auth.db() as db:
            record, user = reset_record(db, token=token)
        return {"username": user["username"], "kind": record["kind"], "expires": record["expires"]}

    @router.post("/reset/{token}/start", dependencies=[Depends(auth.browser_request)])
    def start(token: str, body: ResetStart, response: Response):
        scope = f"reset:{digest(token)}"
        auth.throttle(scope)
        pending_token = secrets.token_urlsafe(32)
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            record, user = reset_record(db, token=token)
            if record["kind"] == "authenticator":
                if not auth.password_matches(user["password_hash"], body.password):
                    raise HTTPException(401, "Incorrect current password.")
                password_hash = user["password_hash"]
            else:
                if len(body.password) < 12:
                    raise HTTPException(422, "New passwords must contain at least 12 characters.")
                password_hash = HASHER.hash(body.password)
            secret = user["secret"] if record["kind"] == "password" else pyotp.random_base32()
            expires = min(record["expires"], time.time() + 600)
            db.execute("DELETE FROM reset_pending WHERE expires<=? OR reset_id=?", (time.time(), record["id"]))
            db.execute("INSERT INTO reset_pending VALUES (?,?,?,?,?)",
                       (digest(pending_token), record["id"], password_hash, secret, expires))
            auth.success(db, scope)
        response.set_cookie(RESET_COOKIE, pending_token, max_age=600, secure=auth.secure,
                            httponly=True, samesite="strict", path="/api/auth")
        result = {"kind": record["kind"], "username": user["username"]}
        if record["kind"] != "password":
            uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name="Per Diem Signing")
            image = qrcode.make(uri)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            result.update(secret=secret, qr=base64.b64encode(buffer.getvalue()).decode("ascii"))
        return result

    @router.post("/reset/{token}/confirm", dependencies=[Depends(auth.browser_request)])
    def finish(token: str, body: ResetConfirm, request: Request, response: Response):
        scope = f"reset-confirm:{digest(token)}"
        auth.throttle(scope)
        with auth.db() as db:
            db.execute("BEGIN IMMEDIATE")
            record, user = reset_record(db, token=token)
            pending = db.execute("SELECT * FROM reset_pending WHERE token_hash=? AND reset_id=?",
                                 (digest(request.cookies.get(RESET_COOKIE, "")), record["id"])).fetchone()
            if pending is None or pending["expires"] <= time.time():
                raise HTTPException(410, "Recovery setup expired. Open your recovery link and start again.")
            last = user["last_counter"] if record["kind"] == "password" else -1
            counter = auth.counter(pending["secret"], body.code, last)
            if counter is None:
                raise HTTPException(401, "Incorrect or already-used authenticator code. Wait for a new code.")
            db.execute("""UPDATE users SET password_hash=?,secret=?,last_counter=? WHERE id=?""",
                       (pending["password_hash"], pending["secret"], counter, user["id"]))
            db.execute("UPDATE user_resets SET used_at=? WHERE id=?", (time.time(), record["id"]))
            db.execute("DELETE FROM reset_pending WHERE reset_id=?", (record["id"],))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            codes = auth.recovery_codes(db, user["id"]) if record["kind"] != "password" else []
            auth.success(db, f"login:{user['username']}")
            auth.success(db, scope)
            audit(db, record["created_by"], user["id"], f"recovery_completed:{record['kind']}")
        response.delete_cookie(RESET_COOKIE, path="/api/auth", secure=auth.secure,
                               httponly=True, samesite="strict")
        return {"completed": True, "recovery_codes": codes, "username": user["username"]}

    return router
