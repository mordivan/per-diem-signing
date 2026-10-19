import argparse
import os
from pathlib import Path

import uvicorn

from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone per-diem signing")
    parser.add_argument("--template", type=Path)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        / "PerDiemSigning",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--public-url", default="")
    args = parser.parse_args()
    local_url = f"http://127.0.0.1:{args.port}"
    app = create_app(args.data_dir, args.template, public_url=args.public_url or local_url)
    print(f"Administrator page: {local_url}", flush=True)
    print(f"Administrator key: {app.state.admin_key}", flush=True)
    print(f"Private data: {args.data_dir}", flush=True)
    if not args.public_url:
        print("LOCAL ONLY: phone links need a configured HTTPS public URL.", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
