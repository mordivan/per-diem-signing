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
    parser.add_argument("--no-key-output", action="store_true",
                        help="keep the administrator key out of background logs")
    parser.add_argument("--log-file", type=Path, help="write rotating server diagnostics to a private file")
    args = parser.parse_args()
    local_url = f"http://127.0.0.1:{args.port}"
    app = create_app(args.data_dir, args.template, public_url=args.public_url or local_url)
    print(f"Administrator page: {local_url}", flush=True)
    if not args.no_key_output:
        print(f"Administrator key: {app.state.admin_key}", flush=True)
    print(f"Private data: {args.data_dir}", flush=True)
    if not args.public_url:
        print("LOCAL ONLY: phone links need a configured HTTPS public URL.", flush=True)
    options = {}
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        options["log_config"] = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"server": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
            "handlers": {"file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(args.log_file), "maxBytes": 5 * 1024 * 1024,
                "backupCount": 2, "encoding": "utf-8", "formatter": "server",
            }},
            "loggers": {
                "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": [], "propagate": False},
            },
            "root": {"handlers": ["file"], "level": "INFO"},
        }
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, **options)


if __name__ == "__main__":
    main()
