"""
config.py – Load and validate all settings from the .env file.

Every other module imports `settings` from here; nothing else reads .env
directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _require(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is missing or empty. "
            "Copy .env.example to .env and fill it in."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _csv(key: str) -> list[str]:
    raw = _optional(key)
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass
class Config:
    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_token: str
    telegram_chat_id: str

    # ── Vinted session ────────────────────────────────────────────────────────
    vinted_session_cookie: str

    # ── Feature 1: monitor ────────────────────────────────────────────────────
    search_url: str
    poll_interval: int
    max_price: Optional[float]
    brands: list[str]
    sizes: list[str]
    conditions: list[str]
    min_seller_rating: Optional[float]

    # ── Feature 2: AI listing bot ─────────────────────────────────────────────
    anthropic_api_key: str
    vinted_email: str
    vinted_password: str
    vinted_base_url: str


def load_config() -> Config:
    max_price_raw = _optional("MAX_PRICE")
    max_price = float(max_price_raw) if max_price_raw else None

    min_rating_raw = _optional("MIN_SELLER_RATING")
    min_seller_rating = float(min_rating_raw) if min_rating_raw else None

    poll_raw = _optional("POLL_INTERVAL", "60")
    poll_interval = int(poll_raw) if poll_raw else 60

    return Config(
        telegram_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        vinted_session_cookie=_require("VINTED_SESSION_COOKIE"),
        search_url=_require("SEARCH_URL"),
        poll_interval=poll_interval,
        max_price=max_price,
        brands=_csv("BRANDS"),
        sizes=_csv("SIZES"),
        conditions=_csv("CONDITIONS"),
        min_seller_rating=min_seller_rating,
        anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
        vinted_email=_optional("VINTED_EMAIL"),
        vinted_password=_optional("VINTED_PASSWORD"),
        vinted_base_url=_optional("VINTED_BASE_URL", "https://www.vinted.fr"),
    )


# Module-level singleton – imported by all other modules.
settings = load_config()
