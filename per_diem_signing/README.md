# Standalone per-diem signing

Independent of the inventory application's routes, accounts, database and ports.
Uses the supplied **Per Diem Form.xlsx** layout (`Sheet1` and `reciepts`).
The original Outlook attachment is never modified. Without a supplied template,
a blank version is generated. On first start a private
template copy is saved outside the repository in `%LOCALAPPDATA%\PerDiemSigning`.

## Start on Windows

From the repository root, using its existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m per_diem_signing --template "C:\path\to\Per Diem Form.xlsx"
```

Subsequent starts do not need `--template`. Open http://127.0.0.1:8200 and complete
one-time password and authenticator enrollment using the setup key printed in
the terminal (also in `admin-key.txt` in the private data directory).
After confirmation the setup key stops authorizing access. Use your password
and a Microsoft/Google Authenticator code from any device. Save the one-time
recovery codes. See [login and recovery details](../README.md#password-and-authenticator-login).
Stop the server with Ctrl+C.

If dependencies are missing, install this tool's own manifest:

```powershell
.\.venv\Scripts\python.exe -m pip install -r per_diem_signing\requirements.txt
```

## Workflow

1. The sender enters payee, job number, daily amount, days, travel place,
   purpose and coverage. Those details cannot be changed by the recipient.
2. Create a request and copy its unique link immediately. Links expire after
   1-30 days, default 7. Lost links cannot be recovered; revoke and create anew.
3. The recipient reviews a read-only HTML rendering of the populated form,
   adds up to 10 photos, draws a signature, enters their name and agrees.
4. Submission embeds the signature at B31 and photos only on `reciepts`,
   starting at A1 without metadata or captions. Without photos it stays blank.
   The separate **Approved By** line remains untouched.
5. The sender refreshes the request list and downloads the completed `.xlsx`.

Names follow `Payee_Job-Number_YYYY-MM-DD_unique-request-id.xlsx`.
The date is the UTC submission date (shown in the workbook with its timezone).
Each request has a separate snapshot; identical names or simultaneous
submissions cannot overwrite another request. Links are single-use on success.
Revoke unused links from the administrator page. Photos are optional.

JPG, PNG and WebP photos are supported, at most 10 MB each and 50 MB combined
including the signature. The browser reserves 1 MB for the signature.
HEIC must be converted to JPG before attaching. Images are decoded, oriented,
resized to at most 1800 pixels and re-encoded without original photo metadata.
The form preview covers the known template, not arbitrary workbooks or Excel
print fidelity; verify the supplied template's preview and exported file before
using real requests. The total formula is retained for recalculation in Excel.

## Phone access and deployment

The default is **local testing only**. `127.0.0.1` links do not reach the home PC
from a phone. Do not send localhost signing links to recipients.

An approved HTTPS hosting/reverse-proxy setup must forward to this standalone
server. Start with `--public-url https://your-approved-host.example` to generate
external links. This setting does **not** create hosting, a certificate, a
tunnel, firewall rules, automatic startup or remote access. The server binds to
loopback unless explicitly changed with `--host`; keep loopback when a local
reverse proxy is used. Test using a real phone on cellular before rollout.
Use the same origin for the preview and signing APIs.

## Privacy and limitations

- There is **no identity verification**, certificate signing or legal-compliance
  certification. Anyone holding a link can sign it. Entered names are unverified.
- Requests, workbooks and normalized photos stay on the hosting computer.
  No third-party service, analytics, fonts or image storage is used.
- The database stores timestamps, consent text,
  entered name and the output SHA-256. This is not an immutable audit service
  or a digital signature on the Excel file.
- Protect the data directory with Windows account permissions and encrypted
  backups. Data is retained until the operator removes it; no automatic
  deletion/retention policy is configured. Never put that directory in Git.
- Treat signing URLs as secrets. Reverse proxies must redact `/sign/` and
  `/api/sign/` paths in access logs; server request logging should be disabled.
- This is a local working implementation, not a production-hardened public
  signing service. Public deployment, backup/retention policy and real-device
  acceptance testing are required before handling real employee data.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest per_diem_signing\test_server.py -q -p no:cacheprovider
```
