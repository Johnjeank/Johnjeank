"""
claude_helper.py – Feature 2: Claude vision API integration.

Sends one or more product photos to claude-sonnet-4-6 and returns a
structured VintedListing dataclass ready to be previewed and posted.

Prompt caching is applied to the static system prompt so repeated calls
(e.g. user sends another photo) hit the cache and reduce latency + cost.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import anthropic

from config import settings

logger = logging.getLogger(__name__)

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class VintedListing:
    title: str
    description: str
    category: str
    condition: str          # new_with_tags | new_without_tags | very_good | good | satisfactory
    price_eur: float
    brand: Optional[str] = None

    def condition_label(self) -> str:
        labels = {
            "new_with_tags": "New with tags",
            "new_without_tags": "New without tags",
            "very_good": "Very good",
            "good": "Good",
            "satisfactory": "Satisfactory",
        }
        return labels.get(self.condition, self.condition)

    def telegram_preview(self) -> str:
        brand_line = f"🏷 *Brand:* {self.brand}\n" if self.brand else ""
        return (
            f"📦 *Title:* {self.title}\n"
            f"{brand_line}"
            f"📂 *Category:* {self.category}\n"
            f"✨ *Condition:* {self.condition_label()}\n"
            f"💰 *Price:* €{self.price_eur:.2f}\n\n"
            f"📝 *Description:*\n{self.description}"
        )


# ── System prompt (cached) ─────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are an expert at identifying second-hand items and writing \
compelling, accurate Vinted listings. When given one or more product photos, \
you return ONLY a JSON object — no markdown, no explanation, no extra text — \
with exactly these keys:

{
  "title": "<concise, keyword-rich title, max 60 characters>",
  "description": "<2-4 sentence description: brand if visible, colour, material, \
notable features, any visible flaws or wear — be honest>",
  "category": "<most accurate Vinted category, e.g. Women's Tops, Men's Sneakers, \
Electronics, Kids' Clothing, Home & Garden, etc.>",
  "condition": "<exactly one of: new_with_tags | new_without_tags | very_good | good | satisfactory>",
  "price_eur": <number — realistic second-hand EUR price based on condition and typical resale value>,
  "brand": "<brand name as a string, or null if not identifiable>"
}

Condition guide:
- new_with_tags: unworn, original tags still attached
- new_without_tags: unworn but tags removed
- very_good: worn once or twice, no visible defects
- good: normal wear, minor signs of use
- satisfactory: clearly used, visible marks but still functional
"""


# ── Client singleton ──────────────────────────────────────────────────────────

def _get_client() -> anthropic.Anthropic:
    if not settings.anthropic_api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


# ── Photo helpers ─────────────────────────────────────────────────────────────

def _encode_image(path_or_bytes: str | bytes | Path) -> tuple[str, str]:
    """
    Return (base64_data, media_type) for an image file or raw bytes.
    Supports JPEG, PNG, GIF, WEBP.
    """
    if isinstance(path_or_bytes, (str, Path)):
        data = Path(path_or_bytes).read_bytes()
    else:
        data = path_or_bytes

    # Sniff media type from magic bytes
    if data[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        media_type = "image/png"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        media_type = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"  # fallback

    return base64.standard_b64encode(data).decode(), media_type


# ── Core API call ─────────────────────────────────────────────────────────────

async def analyse_photos(photo_bytes_list: list[bytes]) -> VintedListing:
    """
    Send photos to Claude and parse the JSON response into a VintedListing.

    Parameters
    ----------
    photo_bytes_list:
        Raw bytes for each image the user sent (Telegram file download).

    Returns
    -------
    VintedListing with title, description, category, condition, price, brand.
    """
    import asyncio

    client = _get_client()

    # Build the content list: each image + a trailing text prompt
    content: list[dict] = []
    for photo_bytes in photo_bytes_list:
        b64_data, media_type = _encode_image(photo_bytes)
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64_data,
            },
        })

    content.append({
        "type": "text",
        "text": (
            f"I have sent you {len(photo_bytes_list)} photo(s) of an item I want to sell on Vinted. "
            "Identify the item and return the JSON listing object as instructed."
        ),
    })

    def _call() -> anthropic.types.Message:
        return client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    # Cache the static system prompt – saves tokens on every call
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content}],
        )

    response = await asyncio.to_thread(_call)
    raw_text = response.content[0].text.strip()
    logger.debug("Claude raw response: %s", raw_text)

    return _parse_listing(raw_text)


def _parse_listing(raw: str) -> VintedListing:
    """Extract JSON from Claude's response and map it to VintedListing."""
    # Strip any accidental markdown fences
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned invalid JSON: {exc}\nRaw: {raw}") from exc

    valid_conditions = {
        "new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"
    }
    condition = data.get("condition", "good").lower().replace(" ", "_")
    if condition not in valid_conditions:
        condition = "good"

    return VintedListing(
        title=str(data.get("title", "Item for sale"))[:60],
        description=str(data.get("description", "")),
        category=str(data.get("category", "")),
        condition=condition,
        price_eur=float(data.get("price_eur", 10.0)),
        brand=data.get("brand") or None,
    )
