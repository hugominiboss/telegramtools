"""BotCashGain v1.0 — inicializador do servidor FastAPI.

Uso:
    python main.py                # 0.0.0.0:8000
    python main.py --port 9000
    python main.py --reload        # hot-reload em desenvolvimento
"""

from __future__ import annotations

import argparse
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="BotCashGain v1.0 server")
    parser.add_argument("--host", default=os.getenv("API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="hot-reload (dev)")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
