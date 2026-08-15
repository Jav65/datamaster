#!/usr/bin/env python3
"""
Entry point. Reads HOST/PORT from .env so the bind address is never
hardcoded in a shell command or a README.

    python run.py
"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise SystemExit(
            "OPENAI_API_KEY is missing.\n"
            "Copy .env.example to .env and fill it in."
        )
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"DataMaster    → http://{host}:{port}")
    print(f"Agency APIs   → {os.getenv('GOV_API_BASE', 'http://localhost:9001')}")
    uvicorn.run("app.main:app", host=host, port=port, reload=True)
