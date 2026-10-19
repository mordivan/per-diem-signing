from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import logging
import re
import secrets
import shutil
import sqlite3
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from .template import create_blank_template
from .auth import Account, Authentication

LOG = logging.getLogger(__name__)
MAX_FILE = 10 * 1024 * 1024
MAX_BODY = 52 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000
CONSENT = "I have reviewed this per-diem request and agree to apply my signature to it."


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > MAX_BODY:
                raise HTTPException(413, "Submission exceeds the 52 MB upload limit.")
            return message

        await self.app(scope, limited_receive, send)


class FormDetails(BaseModel):
    payee: str = Field(min_length=1, max_length=100, pattern=r"\S")
    job_number: str = Field(min_length=1, max_length=60, pattern=r"\S")
    daily_amount: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    days: int = Field(ge=1, le=366)
    travel_place: str = Field(min_length=1, max_length=120, pattern=r"\S")
    purpose: str = Field(min_length=1, max_length=200, pattern=r"\S")
    hotel: bool = False
    meals: bool = True
    expires_days: int = Field(default=7, ge=1, le=30)


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def set_text(cell, value: str) -> None:
    cell.value = value
    cell.data_type = "s"


def format_amounts(sheet) -> None:
    for row in (11, 12):
        for region in list(sheet.merged_cells.ranges):
            if region.min_row == region.max_row == row and 2 <= region.min_col <= region.max_col <= 5:
                sheet.unmerge_cells(str(region))
        sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
        sheet.cell(row, 2).alignment = Alignment(horizontal="left", vertical="center")
    sheet["B11"].number_format = sheet["B14"].number_format = '"$"#,##0.00'
    sheet["B12"].number_format = "0"


def save_with_total(book, output: Path) -> None:
    sheet = book["Sheet1"]
    total = Decimal(str(sheet["B11"].value)) * Decimal(str(sheet["B12"].value))
    sheet["B14"] = "=B11*B12"
    buffer = io.BytesIO()
    book.save(buffer)
    # openpyxl writes formulas without cached results; phone viewers may not recalculate.
    worksheet_path = f"xl/worksheets/sheet{book.index(sheet) + 1}.xml"
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(buffer) as source, ZipFile(output, "w") as destination:
        for entry in source.infolist():
            data = source.read(entry.filename)
            if entry.filename == worksheet_path:
                root = ElementTree.fromstring(data)
                cell = root.find(f".//{namespace}c[@r='B14']")
                if cell is None:
                    raise ValueError("The amount cell is missing from the generated workbook.")
                value = cell.find(f"{namespace}v")
                if value is None:
                    value = ElementTree.SubElement(cell, f"{namespace}v")
                value.text = format(total, ".2f")
                data = ElementTree.tostring(root, encoding="utf-8")
            destination.writestr(entry, data)


def populate(template: Path, output: Path, details: FormDetails) -> None:
    book = load_workbook(template)
    sheet = book["Sheet1"]
    for address, value in {
        "B8": details.payee,
        "B17": details.job_number,
        "B19": details.travel_place,
        "B21": details.purpose,
        "B40": "Yes" if details.hotel else "No",
        "B41": "Yes" if details.meals else "No",
    }.items():
        set_text(sheet[address], value)
        sheet[address].alignment = Alignment(wrap_text=True, vertical="center")
    sheet["B11"] = float(details.daily_amount)
    sheet["B12"] = details.days
    sheet["B14"] = "=B11*B12"
    format_amounts(sheet)
    sheet.row_dimensions[21].height = 90
    save_with_total(book, output)
    book.close()


def normalize_image(data: bytes, *, signature: bool = False) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise HTTPException(422, "Use JPG, PNG, or WebP images; convert HEIC photos first.")
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                background.alpha_composite(image)
                image = background.convert("RGB")
                if signature:
                    if image.width < 40 or image.height < 20:
                        raise HTTPException(422, "Please draw a signature before submitting.")
                    extrema = ImageOps.grayscale(image).getextrema()
                    if extrema[0] > 220 or extrema[1] - extrema[0] < 20:
                        raise HTTPException(422, "Please draw a visible signature before submitting.")
                image.thumbnail((1800, 1800))
                output = io.BytesIO()
                image.save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "An image is invalid or too large to process safely.") from exc


def safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")[:70] or "unnamed"


def finish_workbook(source: Path, target: Path, signature: bytes,
                    photos: list[bytes], name: str, signed_at: str, request_id: str) -> None:
    book = load_workbook(source)
    sheet = book["Sheet1"]
    format_amounts(sheet)
    sheet.row_dimensions[31].height = 55
    sign = ExcelImage(io.BytesIO(signature))
    scale = min(260 / sign.width, 68 / sign.height)
    sign.width, sign.height = int(sign.width * scale), int(sign.height * scale)
    sheet.add_image(sign, "B31")
    sheet.merge_cells("B32:E33")
    set_text(sheet["B32"], f"{name} | {signed_at}")
    sheet["B32"].alignment = Alignment(wrap_text=True, vertical="center")
    receipts = book["reciepts"]
    receipts.column_dimensions["A"].width = 28
    row = 1
    for data in photos:
        photo = ExcelImage(io.BytesIO(data))
        scale = min(650 / photo.width, 850 / photo.height, 1)
        photo.width, photo.height = int(photo.width * scale), int(photo.height * scale)
        receipts.add_image(photo, f"A{row}")
        for line in range(row, row + 1 + int(photo.height / 20)):
            receipts.row_dimensions[line].height = 15
        row += 4 + int(photo.height / 20)
    save_with_total(book, target)
    book.close()


def preview_workbook(path: Path) -> str:
    book = load_workbook(path)
    sheet = book["Sheet1"]
    merged = {}
    hidden = set()
    for region in sheet.merged_cells.ranges:
        merged[(region.min_row, region.min_col)] = (
            region.max_row - region.min_row + 1, region.max_col - region.min_col + 1)
        hidden.update((r, c) for r in range(region.min_row, region.max_row + 1)
                      for c in range(region.min_col, region.max_col + 1)
                      if (r, c) != (region.min_row, region.min_col))
    parts = ['<!doctype html><html><head><meta name="viewport" content="width=device-width">'
             '<style>body{font-family:Arial;margin:12px}table{border-collapse:collapse;'
             'table-layout:fixed;width:650px}td{overflow-wrap:anywhere;padding:3px;'
             'white-space:pre-wrap;box-sizing:border-box}img{max-width:100%}</style>'
             '</head><body>']
    for image in sheet._images:
        encoded = base64.b64encode(image._data()).decode("ascii")
        parts.append(f'<img alt="Workbook logo" width="{image.width}" '
                     f'src="data:image/png;base64,{encoded}">')
    parts.append("<table><colgroup>")
    for column in "ABCDE":
        width = sheet.column_dimensions[column].width if column in sheet.column_dimensions else 13
        parts.append(f'<col style="width:{int(width * 7)}px">')
    parts.append("</colgroup>")
    for row in sheet.iter_rows(min_row=5, max_row=41, max_col=5):
        height = sheet.row_dimensions[row[0].row].height or 15
        parts.append(f'<tr style="height:{height * 1.333}px">')
        for cell in row:
            if (cell.row, cell.column) in hidden:
                continue
            value = cell.value
            if cell.coordinate == "B14":
                value = f"${Decimal(str(sheet['B11'].value)) * sheet['B12'].value:,.2f}"
            elif cell.coordinate == "B11":
                value = f"${Decimal(str(value)):,.2f}"
            elif cell.coordinate == "B31":
                value = "[Your finger signature will be placed here]"
            rowspan, colspan = merged.get((cell.row, cell.column), (1, 1))
            style = f"font-size:{cell.font.sz or 11}pt;"
            if cell.font.bold:
                style += "font-weight:bold;"
            if cell.alignment.horizontal in {"left", "right", "center"}:
                style += f"text-align:{cell.alignment.horizontal};"
            if any(edge is not None and edge.style for edge in (cell.border.top, cell.border.bottom,
                                          cell.border.left, cell.border.right)):
                style += "border:1px solid #999;"
            parts.append(f'<td rowspan="{rowspan}" colspan="{colspan}" style="{style}">'
                         f'{html.escape(str(value if value is not None else ""))}</td>')
        parts.append("</tr>")
    parts.append("</table><p>Read-only form preview. Identity is not verified. "
                 "The Approved By line is not signed by this workflow.</p></body></html>")
    book.close()
    return "".join(parts)


def create_app(data_dir: Path, template: Path | None = None, *,
               admin_key: str | None = None, public_url: str = "http://127.0.0.1:8200") -> FastAPI:
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    stored_template = data_dir / "template.xlsx"
    if not stored_template.exists():
        if template is None:
            create_blank_template(stored_template)
        else:
            with template.open("rb") as stream:
                book = load_workbook(io.BytesIO(stream.read()))
            if not {"Sheet1", "reciepts"}.issubset(book.sheetnames):
                book.close()
                raise ValueError("Template must have Sheet1 and reciepts worksheets.")
            book.close()
            shutil.copyfile(template, stored_template)
    parsed = urlsplit(public_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query
            or parsed.fragment or parsed.username or parsed.path not in {"", "/"}):
        raise ValueError("Public URL must be an HTTP(S) origin without a path, query or credentials.")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Non-local signing links require HTTPS.")
    key_path = data_dir / "admin-key.txt"
    if admin_key is None:
        if not key_path.exists():
            key_path.write_text(secrets.token_urlsafe(32), encoding="ascii")
        admin_key = key_path.read_text(encoding="ascii").strip()
    database = data_dir / "requests.db"
    with connect(database) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, details TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, signed_at TEXT,
            revoked INTEGER NOT NULL DEFAULT 0, filename TEXT, signer_name TEXT,
            output_sha256 TEXT, consent_text TEXT)""")
        if "owner_id" not in {row["name"] for row in db.execute("PRAGMA table_info(requests)")}:
            db.execute("ALTER TABLE requests ADD COLUMN owner_id INTEGER NOT NULL DEFAULT 1")
        db.execute("CREATE INDEX IF NOT EXISTS requests_owner ON requests(owner_id)")
    app = FastAPI(title="Per-diem signing", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.admin_key = admin_key
    authentication = Authentication(data_dir, admin_key, public_url)
    app.include_router(authentication.router)
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; frame-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        return response

    admin = authentication.require_admin

    def get_request(token: str, db: sqlite3.Connection):
        digest = hashlib.sha256(token.encode()).hexdigest()
        record = db.execute("SELECT * FROM requests WHERE token_hash = ?", (digest,)).fetchone()
        if record is None:
            raise HTTPException(404, "Signing link not found.")
        if record["revoked"]:
            raise HTTPException(410, "This signing link has been revoked.")
        if record["signed_at"]:
            raise HTTPException(409, "This request has already been signed.")
        if datetime.now(timezone.utc) >= datetime.fromisoformat(record["expires_at"]):
            raise HTTPException(410, "This signing link has expired.")
        return record

    @app.get("/", response_class=HTMLResponse)
    def home():
        return (Path(__file__).parent / "static" / "admin.html").read_text(encoding="utf-8")

    @app.get("/sign/{token}", response_class=HTMLResponse)
    def signing_page(token: str):
        return (Path(__file__).parent / "static" / "sign.html").read_text(encoding="utf-8")

    @app.get("/join/{token}", response_class=HTMLResponse)
    def join_page(token: str):
        return (Path(__file__).parent / "static" / "admin.html").read_text(encoding="utf-8")

    @app.get("/api/admin/requests")
    def list_requests(account: Account = Depends(admin)):
        with connect(database) as db:
            rows = db.execute("""SELECT * FROM requests WHERE (?=1 OR owner_id=?)
                ORDER BY created_at DESC""", (int(account.is_superuser), account.id)).fetchall()
        owners = {}
        if account.is_superuser:
            with authentication.db() as db:
                owners = {row["id"]: row["username"] for row in db.execute("SELECT id, username FROM users")}
        return {"requests": [
            {"id": row["id"], "details": json.loads(row["details"]),
             "created_at": row["created_at"], "expires_at": row["expires_at"],
             "signed_at": row["signed_at"], "revoked": bool(row["revoked"]),
             "filename": row["filename"], "signer_name": row["signer_name"],
             "owner": owners.get(row["owner_id"], account.username)}
            for row in rows], "public_url": public_url,
            "user": account.public(),
            "local_only": parsed.hostname in {"localhost", "127.0.0.1", "::1"}}

    @app.post("/api/admin/requests", status_code=201)
    def create_request(details: FormDetails, account: Account = Depends(admin)):
        request_id = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=details.expires_days)
        snapshot = data_dir / f"{request_id}.xlsx"
        populate(stored_template, snapshot, details)
        with connect(database) as db:
            db.execute("INSERT INTO requests (id, token_hash, details, created_at, expires_at, owner_id)"
                       " VALUES (?, ?, ?, ?, ?, ?)",
                       (request_id, hashlib.sha256(token.encode()).hexdigest(),
                        details.model_dump_json(), now.isoformat(), expires.isoformat(), account.id))
        return {"id": request_id, "link": f"{public_url.rstrip('/')}/sign/{token}",
                "expires_at": expires.isoformat()}

    @app.post("/api/admin/requests/{request_id}/revoke")
    def revoke(request_id: str, account: Account = Depends(admin)):
        with connect(database) as db:
            result = db.execute("""UPDATE requests SET revoked = 1 WHERE id = ? AND signed_at IS NULL
                AND (?=1 OR owner_id=?)""", (request_id, int(account.is_superuser), account.id))
            if not result.rowcount:
                raise HTTPException(404, "No pending request found.")
        return {"revoked": True}

    @app.get("/api/admin/requests/{request_id}/download")
    def download(request_id: str, account: Account = Depends(admin)):
        with connect(database) as db:
            record = db.execute("SELECT * FROM requests WHERE id = ? AND (?=1 OR owner_id=?)",
                                (request_id, int(account.is_superuser), account.id)).fetchone()
        if record is None or not record["filename"]:
            raise HTTPException(404, "No signed workbook is available.")
        return FileResponse(data_dir / record["filename"], filename=record["filename"],
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/api/sign/{token}")
    def signing_details(token: str):
        with connect(database) as db:
            record = get_request(token, db)
        return {"details": json.loads(record["details"]), "expires_at": record["expires_at"],
                "consent": CONSENT}

    @app.get("/api/sign/{token}/preview", response_class=HTMLResponse)
    def preview(token: str):
        with connect(database) as db:
            record = get_request(token, db)
        return preview_workbook(data_dir / f"{record['id']}.xlsx")

    @app.post("/api/sign/{token}/submit")
    async def submit(token: str, request: Request):
        with connect(database) as db:
            get_request(token, db)
        async with request.form(max_files=11, max_fields=2, max_part_size=4096) as form:
            name = form.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise HTTPException(422, "Enter your name (up to 100 characters).")
            if form.get("consent") != "yes":
                raise HTTPException(422, "Confirm your agreement before submitting.")
            signature_file = form.get("signature")
            if not isinstance(signature_file, UploadFile):
                raise HTTPException(422, "A finger-drawn signature is required.")
            uploads = form.getlist("photos")
            if len(uploads) > 10:
                raise HTTPException(422, "Attach at most 10 receipt photos.")
            total = 0

            async def read_image(upload, *, signature=False):
                nonlocal total
                if not isinstance(upload, UploadFile):
                    raise HTTPException(422, "Invalid photo upload.")
                data = await upload.read(MAX_FILE + 1)
                total += len(data)
                if len(data) > MAX_FILE or total > 50 * 1024 * 1024:
                    raise HTTPException(413, "Photos must be at most 10 MB each and 50 MB combined.")
                return normalize_image(data, signature=signature)

            signature = await read_image(signature_file, signature=True)
            photos = [await read_image(photo) for photo in uploads]
        # Serialize completion so two submissions cannot overwrite one another.
        with connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            record = get_request(token, db)
            details = json.loads(record["details"])
            signed_at = datetime.now(timezone.utc).isoformat()
            filename = (f"{safe_part(details['payee'])}_Job-{safe_part(details['job_number'])}_"
                        f"{signed_at[:10]}_{record['id']}.xlsx")
            target = data_dir / filename
            temporary = target.with_suffix(".tmp.xlsx")
            try:
                finish_workbook(data_dir / f"{record['id']}.xlsx", temporary, signature,
                                photos, name.strip(), signed_at, record["id"])
                digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
                temporary.replace(target)
                db.execute("UPDATE requests SET signed_at=?, filename=?, signer_name=?, "
                           "output_sha256=?, consent_text=? WHERE id=?",
                           (signed_at, filename, name.strip(), digest, CONSENT, record["id"]))
            except (OSError, ValueError, sqlite3.Error):
                LOG.exception("Could not save per-diem submission %s", record["id"])
                raise HTTPException(500, "Could not save your submission. Please retry.")
            finally:
                temporary.unlink(missing_ok=True)
        return JSONResponse({"signed": True, "filename": filename})

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app
