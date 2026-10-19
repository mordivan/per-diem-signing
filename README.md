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

## Home-PC deployment through Cloudflare Tunnel

The configured public address is **https://perdiem.inventorysolution.dev**.
The home PC remains the host; Cloudflare provides HTTPS and forwards only this
hostname to `http://127.0.0.1:8200`. Inventory routes are independent.
Keep the PC awake, online and signed into the Windows account that runs the tool.

After dependencies are installed, install or update automatic startup with:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-startup.ps1
```

The `PerDiemSigning` Windows task starts now and at **user sign-in**, with retries
after failures. It does not run before sign-in. Cloudflare's separate Windows
service starts at boot. The tool stays bound to loopback; no router port
forwarding is needed. Private data stays in `%LOCALAPPDATA%\PerDiemSigning`.
Diagnostics go to the rotating `server.log` there; the administrator key is not
logged. Task Scheduler runs the Python server directly so stopping or restarting
the task does not leave an orphaned server holding its port.
Do not run `start.ps1` concurrently with the installed task on the same port.

To stop and remove automatic startup without deleting data:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-startup.ps1 -Uninstall
```

The tunnel's DNS and ingress configuration are machine-specific and are not
stored in this repository. Cloning at work does not configure another tunnel.
If public access fails, check the home PC is awake, the `PerDiemSigning` task
is running, local port 8200 responds, and the `Cloudflared` service is running.
The administrator key remains required for request creation and downloads;
recipients use only their unique signing links. Never send the administrator
key to recipients. Do not apply a Cloudflare Access login wall to recipient
routes if they must sign without an account.

## Development and tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

If Windows' default pytest temp folder is inaccessible, add
`--basetemp "$env:LOCALAPPDATA\PerDiemSigning-TestTemp"` using a dedicated,
disposable test directory (pytest clears it).
