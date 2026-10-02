"""python -m web  →  servidor en http://0.0.0.0:9797 (WEB_HOST / WEB_PORT)."""

import os

import uvicorn

from utils.logger import setup_logger


def main():
    setup_logger()
    from web.app import create_app

    uvicorn.run(
        create_app(),
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "9797")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
