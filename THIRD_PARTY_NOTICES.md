# Third-party software and materials

The proprietary [RSI-Industries notice](LICENSE) covers this project's original
materials only. It does not replace, restrict, or supersede any third-party
license. There is no claim that RSI-Industries owns the dependencies below.

Direct runtime dependencies are declared in [pyproject.toml](pyproject.toml)
and [requirements.txt](per_diem_signing/requirements.txt):

- FastAPI
- Uvicorn
- python-multipart
- openpyxl
- Pillow
- argon2-cffi
- PyOTP
- qrcode (with Pillow support)

These packages also install transitive dependencies. Test and build tools have
their own licenses. Exact versions and license texts depend on the installed
environment; consult each distribution's metadata and included license files.
This list is not a complete redistribution notice bundle or a license
compatibility determination.

Before shipping a packaged application, container, installer, or source bundle,
inventory every included distribution and asset, review its license obligations,
and include all required copyright notices, license texts, and other required
materials. Preserve the license files shipped with dependencies. Do not present
those packages as being covered by the proprietary project license.

Customer-supplied workbook templates, logos, photos, signatures, and other data
retain their respective rights. Obtain permission to use and distribute them
separately. The private company workbook is not included in this repository.
