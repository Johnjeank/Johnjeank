"""
monitor.py – Feature 1: Search Monitor + Telegram Notifications.

Polls a Vinted search URL every POLL_INTERVAL seconds, applies local filters,
deduplicates against a JSON file of already-seen IDs, and sends Telegram
alerts for new matching listings.

Run standalone:
    python monitor.py

Or import run_monitor() and schedule it as an asyncio task (see main.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from telegram import Bot, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import settings
from vinted_client import VintedClient
from filters import has_excluded_keyword, model_price_cap

logger = logging.getLogger(__name__)

SEEN_IDS_FILE = Path(__file__).parent / "seen_ids.json"


# ── Persistence helpers ───────────────────────────────────────────────────────

def load_seen_ids() -> set[str]:
    if SEEN_IDS_FILE.exists():
        try:
            return set(json.loads(SEEN_IDS_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_seen_ids(ids: set[str]) -> None:
    SEEN_IDS_FILE.write_text(json.dumps(sorted(ids)))


# ── Filter logic ─────────────────────────────────────────────────────────────

def _str(value: Any) -> str:
    return (value or "").strip().lower()


def matches_filters(item: Any) -> bool:
    """Return True if *item* passes all configured + advanced filters."""
    title = getattr(item, "title", "") or ""
    description = getattr(item, "description", "") or ""
    search_text = f"{title} {description}"

    # ── 1. Keyword exclusions (cracked, broken, iCloud lock, scam payments) ──
    if has_excluded_keyword(search_text):
        logger.debug("Rejected '%s' – excluded keyword matched.", title)
        return False

    # ── 2. Per-model price cap ────────────────────────────────────────────────
    try:
        price = float(item.price)
        cap = model_price_cap(title)
        if cap is not None and price > cap:
            logger.debug(
                "Rejected '%s' – €%.0f exceeds model cap €%.0f.", title, price, cap
            )
            return False
    except (TypeError, ValueError):
        pass

    # ── 3. Global max price (fallback when model not recognised) ─────────────
    if settings.max_price is not None:
        try:
            if float(item.price) > settings.max_price:
                return False
        except (TypeError, ValueError):
            pass

    # ── 4. Brand ──────────────────────────────────────────────────────────────
    if settings.brands:
        brand = _str(getattr(item, "brand_title", None))
        if not any(_str(b) in brand for b in settings.brands):
            return False

    # ── 5. Size ───────────────────────────────────────────────────────────────
    if settings.sizes:
        size = _str(getattr(item, "size_title", None))
        if not any(_str(s) in size for s in settings.sizes):
            return False

    # ── 6. Condition / status ─────────────────────────────────────────────────
    if settings.conditions:
        status = _str(getattr(item, "status", None))
        if not any(_str(c) in status for c in settings.conditions):
            return False

    # ── 7. Minimum seller rating ──────────────────────────────────────────────
    if settings.min_seller_rating is not None:
        try:
            user = getattr(item, "user", None)
            rating = float(getattr(user, "feedback_reputation", 0) or 0)
            if rating < settings.min_seller_rating:
                return False
        except (TypeError, ValueError):
            pass

    return True


# ── Telegram notification ─────────────────────────────────────────────────────

def _item_caption(item: Any) -> str:
    price = getattr(item, "price", "?")
    currency = getattr(item, "currency", "")
    brand = getattr(item, "brand_title", None) or "N/A"
    size = getattr(item, "size_title", None) or "N/A"
    status = getattr(item, "status", None) or "N/A"
    user = getattr(item, "user", None)
    rating = getattr(user, "feedback_reputation", None)
    rating_str = f"{float(rating):.0%}" if rating is not None else "N/A"
    url = getattr(item, "url", "")
    title = getattr(item, "title", "No title")

    # Show budget headroom vs model cap (e.g. "35 / cap 50 = 15 under")
    cap = model_price_cap(title)
    try:
        headroom = cap - float(price)
        cap_line = f"📊 Cap: €{cap:.0f}  •  {_escape(f'€{headroom:.0f} under')}\n"
    except (TypeError, ValueError):
        cap_line = ""

    return (
        f"*{_escape(title)}*\n"
        f"💰 {_escape(str(price))} {_escape(currency)}\n"
        f"{cap_line}"
        f"🏷 Brand: {_escape(brand)}\n"
        f"📐 Size: {_escape(size)}\n"
        f"✨ Condition: {_escape(status)}\n"
        f"⭐ Seller: {_escape(rating_str)}\n"
        f"[View on Vinted]({url})"
    )


def _escape(text: str) -> str:
    """Minimal MarkdownV2 escaping for dynamic values."""
    # We use regular Markdown (not V2) so only * and [ need care.
    return text.replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")


async def send_notification(bot: Bot, item: Any) -> None:
    caption = _item_caption(item)

    # Try to get a photo URL
    photo_url: str | None = None
    photos = getattr(item, "photos", None)
    if photos:
        first = photos[0] if isinstance(photos, list) else photos
        photo_url = getattr(first, "url", None) or getattr(first, "full_size_url", None)
    if photo_url is None:
        # Some versions expose a single `photo` attribute
        photo_obj = getattr(item, "photo", None)
        if photo_obj:
            photo_url = getattr(photo_obj, "url", None) or getattr(photo_obj, "full_size_url", None)

    try:
        if photo_url:
            await bot.send_photo(
                chat_id=settings.telegram_chat_id,
                photo=photo_url,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=caption,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )
    except TelegramError as exc:
        logger.error("Telegram send failed for item %s: %s", getattr(item, "id", "?"), exc)


# ── Main polling loop ─────────────────────────────────────────────────────────

async def run_monitor() -> None:
    """Persistent async loop – runs forever until cancelled."""
    bot = Bot(token=settings.telegram_token)
    client = VintedClient(settings.vinted_session_cookie, settings.search_url)
    seen_ids = load_seen_ids()

    logger.info("Monitor started. Search URL: %s", settings.search_url)
    logger.info("Poll interval: %ds | Filters: max_price=%s brands=%s sizes=%s conditions=%s min_rating=%s",
                settings.poll_interval, settings.max_price, settings.brands,
                settings.sizes, settings.conditions, settings.min_seller_rating)

    # Send startup ping so user knows the bot is alive
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=f"🟢 Vinted monitor started.\nPolling every {settings.poll_interval}s for: `{settings.search_url}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as exc:
        logger.warning("Could not send startup ping: %s", exc)

    while True:
        logger.debug("Polling Vinted…")
        items = await client.search()

        new_matches: list[Any] = []
        for item in items:
            item_id = str(getattr(item, "id", ""))
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            if matches_filters(item):
                new_matches.append(item)

        if new_matches:
            save_seen_ids(seen_ids)
            logger.info("Found %d new matching item(s)", len(new_matches))
            for item in new_matches:
                await send_notification(bot, item)
                await asyncio.sleep(0.5)  # avoid Telegram rate-limit
        else:
            logger.debug("No new matches (checked %d items)", len(items))

        await asyncio.sleep(settings.poll_interval)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    try:
        asyncio.run(run_monitor())
    except KeyboardInterrupt:
        logger.info("Monitor stopped by user.")
