# Per-diem signing

A standalone, phone-friendly browser tool for reviewing per-diem requests,
signing with a finger, and attaching receipt photos. Completed documents stay
in **Excel (.xlsx)** format, with one file per request:

`Jane-Smith_Job-1042_2026-10-19_unique-request-id.xlsx`

No inventory application, employee account or identity verification is required.
This is a working local tool, not a certified electronic-signature service.

## Quick start on Windows

Install Python 3.11 or newer, open PowerShell in this folder, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

On first run this creates a virtual environment and installs this project's
dependencies. Open **http://127.0.0.1:8200** and enter the administrator key shown
in the terminal. Press Ctrl+C in that terminal to stop the server.

A blank per-diem template is generated automatically. To use your own original
**Per Diem Form.xlsx** on first start instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Template "C:\Forms\Per Diem Form.xlsx"
```

The original is copied, never modified. The template must use the expected
`Sheet1` and `reciepts` layout described in the
[detailed documentation](per_diem_signing/README.md).
Each installation stores its template, administrator key and requests privately
in `%LOCALAPPDATA%\PerDiemSigning`. Subsequent starts reuse that template.
To change templates without disturbing old requests, use a separate data folder:

```powershell
.\.venv\Scripts\python.exe -m per_diem_signing --data-dir "$env:LOCALAPPDATA\PerDiemSigning-New" --template "C:\Forms\New Form.xlsx"
```

## Sender workflow

1. Enter payee, job number, daily amount, days, travel place and purpose.
2. Create a request and copy its private signing link.
3. The recipient reviews the form, attaches photos, draws a signature and submits.
4. Refresh the sender page and download the completed workbook.

The employee signature and entered name are added to the form. Photos are
embedded on **reciepts**, grouped with payee, job number and signing date.
The **Approved By** line is left untouched.

## Access from a work computer

Clone this private repository using your GitHub account, then use the same
startup command. A fresh installation starts with the blank template.
**GitHub contains the application code, not your home PC's requests, secret
administrator key, receipt photos, company logo or original Outlook attachment.**

Running another copy at work does not synchronize requests with home.
To access one shared set of real requests, deploy one HTTPS instance on an
approved host, or use approved remote access to the home PC.
Check your employer's policy before installing or hosting software.

## Phone links require HTTPS hosting

The default is **local testing only**. A localhost link cannot be opened by
another phone. An approved HTTPS deployment must route to this server:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -PublicUrl "https://your-approved-host.example"
```

`-PublicUrl` only sets the link address: it does not create hosting, tunnels,
firewall rules or certificates. No router ports are opened automatically.
Keep the hosting PC awake and test a signing link on cellular before rollout.

## Important limits

- Anyone with the link can sign; the name is self-declared, not verified.
- The Excel signature is an image, not a certificate-backed digital signature.
- Links expire and are single-use after success; the sender can revoke them.
- Up to 10 JPG/PNG/WebP photos, 10 MB each; HEIC needs conversion first.
- The preview supports this known template, not arbitrary Excel workbooks.
- The saved workbook can still be edited; an output hash is retained locally,
  but this is not an immutable audit or legal-compliance service.
- Protect and back up the private data folder. No automatic retention policy,
  hosting, startup task or email/text delivery service is installed.

See [detailed operation and privacy notes](per_diem_signing/README.md).

## Development and tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

If Windows' default pytest temp folder is inaccessible, add
`--basetemp "$env:LOCALAPPDATA\PerDiemSigning-TestTemp"` using a dedicated,
disposable test directory (pytest clears it).
