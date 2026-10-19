from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image, ImageDraw

from per_diem_signing.server import connect, create_app, normalize_image

KEY = "test-admin-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
DETAILS = {
    "payee": "Jane Smith", "job_number": "1042", "daily_amount": "125.50",
    "days": 3, "travel_place": "Austin", "purpose": "Site installation",
    "hotel": True, "meals": True,
}


def image_bytes(signature=False):
    image = Image.new("RGB", (400, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(10, 100), (50, 30), (90, 110), (180, 60), (300, 80)], fill="black", width=4)
    output = BytesIO()
    image.save(output, format="PNG" if signature else "JPEG")
    return output.getvalue()


@pytest.fixture
def client(tmp_path):
    template = tmp_path / "template.xlsx"
    book = Workbook()
    book.active.title = "Sheet1"
    book.active["A8"] = "Payee"
    book.active["A31"] = "Employee Signature:"
    book.active["A34"] = "Approved By:"
    book.active.merge_cells("B8:E8")
    book.active.merge_cells("B31:E31")
    book.active.merge_cells("B34:E34")
    book.active.column_dimensions["B"].width = 9.140625
    book.active["B12"].number_format = '"$"#,##0.00'
    book.active.merge_cells("C11:D11")
    book.active.merge_cells("C12:D12")
    book.active.add_image(ExcelImage(BytesIO(image_bytes())), "A1")
    book.create_sheet("reciepts")
    book.save(template)
    with TestClient(create_app(tmp_path / "data", template, admin_key=KEY)) as test:
        yield test, tmp_path / "data", template


def create(client, details=None):
    response = client.post("/api/admin/requests", headers=AUTH, json=details or DETAILS)
    assert response.status_code == 201, response.text
    data = response.json()
    return data, data["link"].rsplit("/", 1)[-1]


def submit(client, token, *, name="Jane Smith", consent="yes", photos=True):
    files = [("signature", ("signature.png", image_bytes(True), "image/png"))]
    if photos:
        files.extend([
            ("photos", ("receipt.jpg", image_bytes(), "image/jpeg")),
            ("photos", ("receipt2.jpg", image_bytes(), "image/jpeg")),
        ])
    return client.post(f"/api/sign/{token}/submit",
                       data={"name": name, "consent": consent}, files=files)


def test_full_workflow_preserves_excel_and_embeds_images(client):
    test, directory, template = client
    original = template.read_bytes()
    request, token = create(test)
    assert test.get("/api/admin/requests").status_code == 401
    assert test.get(f"/api/sign/{token}").json()["details"]["payee"] == "Jane Smith"
    preview = test.get(f"/api/sign/{token}/preview")
    assert preview.status_code == 200
    assert "Jane Smith" in preview.text
    assert "$376.50" in preview.text
    assert "Workbook logo" in preview.text
    assert preview.headers["referrer-policy"] == "no-referrer"
    result = submit(test, token)
    assert result.status_code == 200, result.text
    assert result.json()["filename"].startswith("Jane-Smith_Job-1042_")
    response = test.get(f"/api/admin/requests/{request['id']}/download", headers=AUTH)
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.content))
    sheet = book["Sheet1"]
    assert sheet["B8"].value == "Jane Smith"
    assert sheet["B11"].value == 125.5
    assert sheet["B12"].value == 3
    assert sheet["B12"].number_format == "0"
    assert sheet["B11"].number_format == '"$"#,##0.00'
    assert "B11:E11" in sheet.merged_cells
    assert "B12:E12" in sheet.merged_cells
    assert sheet["B14"].value == "=B11*B12"
    assert sheet["B34"].value is None
    assert len(sheet._images) == 2
    assert len(book["reciepts"]._images) == 2
    assert all(cell.value is None for row in book["reciepts"] for cell in row)
    assert book["reciepts"]._images[0].anchor._from.row == 0
    cached = load_workbook(BytesIO(response.content), data_only=True)
    assert cached["Sheet1"]["B14"].value == 376.5
    cached.close()
    assert submit(test, token).status_code == 409
    assert template.read_bytes() == original
    assert test.get(f"/api/admin/requests/{request['id']}/download").status_code == 401
    with connect(directory / "requests.db") as db:
        saved = db.execute("SELECT * FROM requests").fetchone()
        assert saved["output_sha256"]
        assert saved["consent_text"]
        assert token not in saved["token_hash"]


def test_expired_revoked_unknown_and_invalid_forms(client):
    test, directory, _ = client
    assert test.get("/api/sign/not-real").status_code == 404
    assert test.post("/api/admin/requests", headers=AUTH,
                     json={**DETAILS, "days": 0}).status_code == 422
    request, token = create(test)
    assert submit(test, token, consent="no").status_code == 422
    assert submit(test, token, name=" ").status_code == 422
    with connect(directory / "requests.db") as db:
        db.execute("UPDATE requests SET expires_at = ? WHERE id = ?",
                   ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), request["id"]))
    assert test.get(f"/api/sign/{token}").status_code == 410
    request, token = create(test)
    assert test.post(f"/api/admin/requests/{request['id']}/revoke", headers=AUTH).status_code == 200
    assert submit(test, token).status_code == 410


def test_duplicate_names_separate_requests_and_no_photo_submission(client):
    test, _, _ = client
    first, token1 = create(test)
    second, token2 = create(test)
    assert first["id"] != second["id"]
    result1 = submit(test, token1, photos=False)
    result2 = submit(test, token2, photos=False)
    assert result1.status_code == result2.status_code == 200
    assert result1.json()["filename"] != result2.json()["filename"]
    response = test.get(f"/api/admin/requests/{first['id']}/download", headers=AUTH)
    book = load_workbook(BytesIO(response.content))
    assert not book["reciepts"]._images
    assert all(cell.value is None for row in book["reciepts"] for cell in row)
    book.close()


def test_concurrent_submission_only_one_succeeds(client):
    test, _, _ = client
    _, token = create(test)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(test, token), range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]


def test_existing_pending_snapshot_gets_export_format_fix(client):
    test, directory, _ = client
    request, token = create(test, {**DETAILS, "daily_amount": "1000000.00", "days": 7})
    path = directory / f"{request['id']}.xlsx"
    book = load_workbook(path)
    sheet = book["Sheet1"]
    sheet.unmerge_cells("B11:E11")
    sheet.unmerge_cells("B12:E12")
    sheet.merge_cells("C11:D11")
    sheet.merge_cells("C12:D12")
    sheet["B12"].number_format = '"$"#,##0.00'
    book.save(path)
    book.close()
    assert submit(test, token).status_code == 200
    response = test.get(f"/api/admin/requests/{request['id']}/download", headers=AUTH)
    book = load_workbook(BytesIO(response.content))
    sheet = book["Sheet1"]
    assert sheet["B11"].value == 1000000
    assert sheet["B12"].value == 7
    assert sheet["B12"].number_format == "0"
    assert "B11:E11" in sheet.merged_cells
    assert "B12:E12" in sheet.merged_cells
    assert all(cell.value is None for row in book["reciepts"] for cell in row)
    assert len(book["reciepts"]._images) == 2
    book.close()


@pytest.mark.parametrize(("daily", "days", "total"), [
    ("200.00", 7, 1400), ("125.50", 3, 376.5), ("0.01", 3, 0.03),
    ("1000000.00", 366, 366000000),
])
def test_total_is_cached_for_viewers_without_recalculation(client, daily, days, total):
    test, directory, _ = client
    request, token = create(test, {**DETAILS, "daily_amount": daily, "days": days})
    cached = load_workbook(directory / f"{request['id']}.xlsx", data_only=True)
    assert cached["Sheet1"]["B14"].value == total
    cached.close()
    assert submit(test, token, photos=False).status_code == 200
    response = test.get(f"/api/admin/requests/{request['id']}/download", headers=AUTH)
    cached = load_workbook(BytesIO(response.content), data_only=True)
    assert cached["Sheet1"]["B14"].value == total
    assert cached["Sheet1"]["B11"].value == float(daily)
    assert cached["Sheet1"]["B12"].value == days
    cached.close()
    formulas = load_workbook(BytesIO(response.content))
    assert formulas["Sheet1"]["B14"].value == "=B11*B12"
    formulas.close()


def test_formula_injection_is_literal_and_html_escaped(client):
    test, directory, _ = client
    request, token = create(test, {**DETAILS, "payee": "=1+1",
                                  "purpose": "<script>alert(1)</script>"})
    book = load_workbook(directory / f"{request['id']}.xlsx")
    assert book["Sheet1"]["B8"].data_type == "s"
    preview = test.get(f"/api/sign/{token}/preview").text
    assert "<script>alert(1)</script>" not in preview
    assert "&lt;script&gt;" in preview


def test_bad_images_and_blank_signature_are_rejected(client):
    test, _, _ = client
    _, token = create(test)
    response = test.post(f"/api/sign/{token}/submit",
                         data={"name": "Jane", "consent": "yes"},
                         files={"signature": ("fake.png", b"invalid", "image/png")})
    assert response.status_code == 422
    blank = BytesIO()
    Image.new("RGB", (300, 100), "white").save(blank, format="PNG")
    with pytest.raises(HTTPException) as error:
        normalize_image(blank.getvalue(), signature=True)
    assert error.value.status_code == 422


def test_public_url_requires_https(client, tmp_path):
    _, _, template = client
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(tmp_path / "insecure", template, public_url="http://192.168.1.2:8200")


def test_generated_template_and_persistent_restart(tmp_path):
    directory = tmp_path / "fresh"
    with TestClient(create_app(directory)) as test:
        key = test.app.state.admin_key
        request = test.post("/api/admin/requests",
                            headers={"Authorization": f"Bearer {key}"}, json=DETAILS).json()
        token = request["link"].rsplit("/", 1)[-1]
        assert test.get(f"/api/sign/{token}/preview").status_code == 200
        assert submit(test, token).status_code == 200
    with TestClient(create_app(directory)) as test:
        assert test.app.state.admin_key == key
        result = test.get("/api/admin/requests", headers={"Authorization": f"Bearer {key}"})
        assert len(result.json()["requests"]) == 1
        assert result.json()["requests"][0]["signed_at"]


def test_upload_limits(client):
    test, _, _ = client
    _, token = create(test)
    files = [("signature", ("sign.png", image_bytes(True), "image/png"))]
    files += [("photos", (f"{i}.jpg", image_bytes(), "image/jpeg")) for i in range(11)]
    response = test.post(f"/api/sign/{token}/submit",
                         data={"name": "Jane", "consent": "yes"}, files=files)
    assert response.status_code == 400
    response = test.post(f"/api/sign/{token}/submit",
                         data={"name": "Jane", "consent": "yes"},
                         files={"signature": ("large.png", b"x" * (10 * 1024 * 1024 + 1),
                                              "image/png")})
    assert response.status_code == 413
    response = test.post("/api/admin/requests", headers={**AUTH, "Content-Type": "application/json"},
                         content=b" " * (52 * 1024 * 1024 + 1))
    assert response.status_code == 413


def test_background_cli_keeps_key_out_of_logs(tmp_path, monkeypatch, capsys):
    from per_diem_signing import __main__

    options = {}
    monkeypatch.setattr("sys.argv", [
        "per-diem-signing", "--data-dir", str(tmp_path / "private"),
        "--public-url", "https://perdiem.example.test",
        "--no-key-output", "--log-file", str(tmp_path / "private" / "server.log"),
    ])
    monkeypatch.setattr(__main__.uvicorn, "run",
                        lambda app, **kwargs: options.update(kwargs))
    __main__.main()
    output = capsys.readouterr().out
    key = (tmp_path / "private" / "admin-key.txt").read_text()
    assert key not in output
    assert "Administrator key:" not in output
    assert options["host"] == "127.0.0.1"
    assert options["access_log"] is False
    assert options["log_config"]["handlers"]["file"]["maxBytes"] == 5 * 1024 * 1024
