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
dependencies. Open **http://127.0.0.1:8200** and complete one-time administrator
enrollment using the setup key shown in the terminal. Press Ctrl+C to stop.

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
embedded on **reciepts**, with photos only: no names, dates, labels or other
metadata printed on that sheet. Without photos the sheet remains blank.
Daily amount and total use currency formatting; number of days is an integer.
The total includes a saved calculated value for phone and preview viewers that
do not recalculate formulas; the Excel formula is preserved.
The **Approved By** line is left untouched.

## Access from a work computer

For the existing home-hosted deployment, open
**https://perdiem.inventorysolution.dev** from any phone or computer. Sign in with
your username, password and an authenticator code. Your existing account's
username is **admin**. No installation needed.

To run an independent installation, clone this private repository using your
GitHub account, then use the startup command. A fresh installation starts with
the blank template and its own administrator setup.
**GitHub contains the application code, not your home PC's requests, secret
administrator key, receipt photos, company logo or original Outlook attachment.**

Running another copy at work does not synchronize requests with home.
To access one shared set of real requests, deploy one HTTPS instance on an
approved host, or use approved remote access to the home PC.
Check your employer's policy before installing or hosting software.

## Password and authenticator login

1. Open the administrator page. On first enrollment only, enter the existing
   setup key from `%LOCALAPPDATA%\PerDiemSigning\admin-key.txt`.
2. Choose a password of at least 12 characters and confirm it.
3. Scan the QR code in Microsoft Authenticator (**Other account**) or Google
   Authenticator. On the same phone, add the displayed secret manually as a
   **time-based** account. Never share the QR code or secret.
4. Enter the generated six-digit code within 10 minutes to activate login.
5. Save the ten one-time recovery codes somewhere safe, separate from the phone.
   They are displayed once; each works only with your password.

After confirmation, the old setup key no longer authorizes access or enrollment.
From any device use **username + password + current authenticator code**, or
**username + password + an unused recovery code**. Codes cannot be replayed; wait for the
next code if you just used one. Keep the phone's date/time set automatically.
The issuer in your authenticator is **Per Diem Signing**, account your username.
Existing authenticator entries named Administrator continue working unchanged.
This is TOTP, not a Microsoft/Google account sign-in or push notification.

Sessions last eight hours, survive server restarts, and are revoked on Sign out.
On HTTPS, session cookies are Secure, HttpOnly and SameSite=Strict. No password,
setup key or session token is stored in browser local storage. After ten failed
authentication attempts within 15 minutes, new attempts are blocked for up to
15 minutes. Login limits are per username, so a colleague's failed attempts do
not lock out the superuser. Setup and invitation attempts have separate limits.
Cookies must be enabled. Local HTTP testing uses a separate non-Secure cookie.

Passwords are Argon2id hashes. Recovery codes and session tokens are stored only
as hashes. The private `authentication.db` stores the TOTP secret so the server
can verify codes; protect that directory and its backups with OS permissions
and encryption. There is no email delivery or self-service identity recovery.
If a colleague loses credentials, the superuser can issue a private recovery
link from the Users tab (below). The superuser cannot reset itself with these
controls: keep its password, authenticator and recovery codes safe. Losing the
superuser's required credentials needs deliberate local administrator recovery.
Do not delete the database as a casual troubleshooting step.

Recipients still use only their private request links; this administrator
authentication does not verify recipient identity.

## Superuser and colleague accounts

The original account is the **superuser**, username **admin**. Its password,
authenticator, recovery codes and active sessions are preserved during upgrade.
All existing requests are owned by this account; no signed documents are changed.

After signing in, open **Users -> Create colleague invitation**.
Copy the private invitation immediately and send it to your intended colleague.
It expires in **48 hours** and can create **one ordinary account**, never another
superuser. The app does not send an email or text automatically. Anyone holding
the invitation can accept it, so share it privately; revoke it if misdirected.

Your colleague opens the link on any device, chooses a username (3-40 letters,
digits, dots, underscores or hyphens), optionally supplies a contact email,
chooses a password, pairs their own
authenticator and saves their own recovery codes. Usernames are case-insensitive,
must start with a letter or digit, and `admin` is reserved. Enrollment setup
expires after ten minutes; restart it from the invitation if needed. The
invitation is consumed only after successful authenticator confirmation.

- **Superuser:** sees, downloads and revokes everyone's requests, with the owner
  shown in the list; manages invitations and colleague access from **Users**.
- **Colleague:** creates requests and sees/downloads/revokes only their own.
  Ownership is assigned by the server, not chosen in the browser.
- **Recipient:** unchanged link-only review/sign/upload, without account creation.
  A colleague's request link still allows its intended recipient to sign.

**Revoke access** immediately invalidates the colleague's sessions, revokes their
unused account-recovery links and prevents further login. Their requests and completed workbooks are retained for the
superuser. Existing recipient signing links remain usable until their normal
expiry or revocation; revoke those requests separately if needed.
Use **Restore access** to permit login again. Old sessions and revoked recovery
links do not become valid again. The superuser cannot revoke its own access.
There is no user deletion or role reassignment.

### Helping colleagues from any device

Sign in as `admin` and open **Users**. It lists existing accounts and
pending/accepted/revoked/expired invitations.

- **Save username/email:** changes the colleague's username or optional contact
  email. Email is unverified contact information, not a login or recovery factor.
  Renaming preserves the account ID and request ownership, signs the colleague
  out, clears their old/new username login limits and revokes unused recovery
  links. Tell them the new username. Their existing authenticator still works,
  even if its displayed account label has the old username.
- **Unlock failed logins:** clears that user's 15-minute failed-login limit.
  This does not restore revoked access or replace forgotten credentials.
- **Create recovery link:** choose password only (requires the existing
  authenticator), authenticator only (requires the current password), or both
  (full account reset). Verify the person's identity before issuing any link.
  A full reset link alone authorizes replacement of both credentials: treat it
  as highly sensitive and send it only through a trusted private channel.

Recovery links expire in **one hour**, work once, and are stored only as hashes.
Copy them immediately; the app cannot retrieve them again or send an email.
Issuing a link signs out the colleague's existing sessions and supersedes all
earlier recovery links for that account. It does not change their credentials
until successful confirmation, or block them from signing in with still-valid
credentials while the link remains unused. Revoke a misdirected link from the
recovery-links list. That list shows the latest 100 links.

The colleague opens the link on their phone or computer. Password-only recovery
requires a new password (at least 12 characters) and a fresh code from the
existing authenticator. Authenticator-only recovery verifies the current
password before showing new QR/manual setup. Full recovery sets a new password
and new authenticator. Authenticator setup must be confirmed within ten minutes
and before the link expires. Starting again replaces any earlier unfinished
setup for that same link.

Successful completion revokes every existing session and clears the user's
failed-login limit. Authenticator replacement invalidates the old authenticator
and all previous recovery codes, displays ten new recovery codes once, and
requires signing in again. Password-only recovery leaves the authenticator and
unused recovery codes unchanged. Recovery does not change request ownership
or modify workbooks. Revoking access, renaming, or revoking/superseding a link
also prevents an unfinished recovery from completing.

Successful profile/access/recovery management actions are recorded locally
with actor/target IDs and timestamps in `authentication.db`. This is an
operational history, not a tamper-proof audit system. Last-login information
starts with logins performed after this upgrade; older history is unavailable.

User management requires a fully enrolled superuser session, not the legacy
setup key. Account authentication stays in `authentication.db`; ownership stays
in `requests.db`. Back up both databases together before deployment. Schema
migration runs automatically at startup and preserves original request ownership.

## Commercial licensing

Copyright (c) 2026 **RSI-Industries**. All rights reserved.
This is proprietary software, not an open-source grant. Use, redistribution,
modification, or offering copies as a service requires separate written
authorization from RSI-Industries. See [LICENSE](LICENSE) and
[third-party notices](THIRD_PARTY_NOTICES.md).

This notice does **not** implement customer license keys, subscription billing,
entitlements, or multi-tenant SaaS isolation. Colleague accounts belong to one
installation; they are not separate customer tenants. A commercial rollout
still needs appropriate customer terms, privacy/security/retention policies,
dependency-license review and operational safeguards. Have a qualified lawyer
review the notice and customer agreements before selling; the notice alone
does not establish rights to employer-owned or third-party materials.

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
logged. Task Scheduler runs windowless `pythonw.exe` directly, so no terminal
opens at sign-in. Stopping or restarting
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
Password-and-authenticator login protects request creation and downloads after
one-time enrollment; recipients use only their unique signing links. Never send
administrator credentials to recipients. Do not apply a Cloudflare Access login wall to recipient
routes if they must sign without an account.

## Development and tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

If Windows' default pytest temp folder is inaccessible, add
`--basetemp "$env:LOCALAPPDATA\PerDiemSigning-TestTemp"` using a dedicated,
disposable test directory (pytest clears it).
