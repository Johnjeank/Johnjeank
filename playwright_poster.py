"""
playwright_poster.py – Feature 2: Playwright automation for posting a
listing to Vinted.

Flow
----
1. Launch a Chromium browser (headless by default).
2. Log in to Vinted with VINTED_EMAIL / VINTED_PASSWORD.
3. Navigate to the "Sell" page.
4. Fill in: photos, category search, title, description, condition, price.
5. Submit and return the URL of the new listing.

Run playwright install chromium once before first use.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from claude_helper import VintedListing
from config import settings

logger = logging.getLogger(__name__)

# How long (ms) to wait for elements before giving up
TIMEOUT = 20_000


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _wait_and_fill(page: Page, selector: str, value: str) -> None:
    await page.wait_for_selector(selector, timeout=TIMEOUT)
    await page.fill(selector, value)


async def _wait_and_click(page: Page, selector: str) -> None:
    await page.wait_for_selector(selector, timeout=TIMEOUT)
    await page.click(selector)


# ── Login ─────────────────────────────────────────────────────────────────────

async def _login(page: Page) -> None:
    logger.info("Navigating to Vinted login…")
    await page.goto(f"{settings.vinted_base_url}/login", wait_until="domcontentloaded")

    # Accept cookies if banner appears
    try:
        await page.click('[data-testid="onetrust-accept-btn-handler"]', timeout=5_000)
    except PWTimeout:
        pass

    # Fill credentials
    await _wait_and_fill(page, 'input[name="username"]', settings.vinted_email)
    await _wait_and_fill(page, 'input[name="password"]', settings.vinted_password)
    await _wait_and_click(page, 'button[type="submit"]')

    # Wait for post-login redirect
    await page.wait_for_url(f"{settings.vinted_base_url}/**", timeout=TIMEOUT)
    logger.info("Logged in successfully.")


# ── Photo upload ──────────────────────────────────────────────────────────────

async def _upload_photos(page: Page, photo_bytes_list: list[bytes]) -> None:
    """Write photos to temp files and hand them to the file input."""
    tmp_paths: list[str] = []
    try:
        for i, data in enumerate(photo_bytes_list):
            suffix = ".jpg"
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                suffix = ".png"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(data)
            tmp.close()
            tmp_paths.append(tmp.name)

        # Vinted uses a hidden file input for photo upload
        file_input = await page.query_selector('input[type="file"]')
        if file_input:
            await file_input.set_input_files(tmp_paths)
            # Wait for thumbnails to appear
            await page.wait_for_selector(
                '[data-testid="upload-photo-input-thumbnail"]', timeout=30_000
            )
            logger.info("Uploaded %d photo(s).", len(tmp_paths))
        else:
            logger.warning("Could not find file input for photos.")
    finally:
        for p in tmp_paths:
            Path(p).unlink(missing_ok=True)


# ── Category selection ────────────────────────────────────────────────────────

async def _select_category(page: Page, category: str) -> None:
    """
    Type the category into the search box on the listing form and pick the
    first matching suggestion.
    """
    try:
        # Click the category field / "What are you selling?" prompt
        cat_trigger = await page.query_selector('[data-testid="item-upload-category-select"]')
        if cat_trigger:
            await cat_trigger.click()
        else:
            # Fallback: look for any visible category button
            await page.click('text="Choose a category"', timeout=5_000)

        # Type into the search box that appears
        await page.wait_for_selector('[data-testid="catalog-search-input"]', timeout=TIMEOUT)
        await page.fill('[data-testid="catalog-search-input"]', category)
        await asyncio.sleep(1)  # wait for suggestions

        # Click the first suggestion
        first_suggestion = await page.query_selector('[data-testid="catalog-search-item"]')
        if first_suggestion:
            await first_suggestion.click()
            logger.info("Selected category: %s", category)
        else:
            logger.warning("No category suggestion found for '%s'", category)
    except PWTimeout:
        logger.warning("Category selection timed out – skipping.")


# ── Condition dropdown ────────────────────────────────────────────────────────

_CONDITION_SELECTORS = {
    "new_with_tags":    '[data-testid="item-upload-status-new_with_tags"]',
    "new_without_tags": '[data-testid="item-upload-status-new_without_tags"]',
    "very_good":        '[data-testid="item-upload-status-very_good"]',
    "good":             '[data-testid="item-upload-status-good"]',
    "satisfactory":     '[data-testid="item-upload-status-satisfactory"]',
}


async def _select_condition(page: Page, condition: str) -> None:
    selector = _CONDITION_SELECTORS.get(condition)
    if not selector:
        logger.warning("Unknown condition '%s'", condition)
        return
    try:
        await _wait_and_click(page, selector)
        logger.info("Set condition: %s", condition)
    except PWTimeout:
        # Fallback: try clicking by visible text
        label = VintedListing.__new__(VintedListing)
        label.condition = condition
        label_text = label.condition_label() if hasattr(label, "condition_label") else condition
        try:
            await page.click(f'text="{label_text}"', timeout=5_000)
        except PWTimeout:
            logger.warning("Could not set condition '%s'", condition)


# ── Main poster ───────────────────────────────────────────────────────────────

async def post_listing(
    listing: VintedListing,
    photo_bytes_list: list[bytes],
    headless: bool = True,
) -> str:
    """
    Log in and post *listing* to Vinted using Playwright.

    Returns
    -------
    str
        URL of the newly created listing, or an empty string on failure.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            await _login(page)

            # Navigate to the sell / upload page
            logger.info("Opening sell page…")
            await page.goto(
                f"{settings.vinted_base_url}/items/new",
                wait_until="domcontentloaded",
                timeout=30_000,
            )

            # ── Photos ────────────────────────────────────────────────────────
            await _upload_photos(page, photo_bytes_list)

            # ── Category ──────────────────────────────────────────────────────
            await _select_category(page, listing.category)

            # ── Title ─────────────────────────────────────────────────────────
            await _wait_and_fill(
                page,
                '[data-testid="item-upload-title-input"], input[name="title"]',
                listing.title,
            )

            # ── Description ───────────────────────────────────────────────────
            await _wait_and_fill(
                page,
                '[data-testid="item-upload-description-input"], textarea[name="description"]',
                listing.description,
            )

            # ── Condition ─────────────────────────────────────────────────────
            await _select_condition(page, listing.condition)

            # ── Price ─────────────────────────────────────────────────────────
            await _wait_and_fill(
                page,
                '[data-testid="item-upload-price-input"], input[name="price"]',
                str(listing.price_eur),
            )

            # ── Submit ────────────────────────────────────────────────────────
            logger.info("Submitting listing…")
            await _wait_and_click(
                page,
                '[data-testid="item-upload-submit-button"], button[type="submit"]',
            )

            # Wait for redirect to the new listing page
            await page.wait_for_url(
                f"{settings.vinted_base_url}/items/**",
                timeout=30_000,
            )
            listing_url = page.url
            logger.info("Listing posted: %s", listing_url)
            return listing_url

        except Exception as exc:
            logger.error("Playwright posting failed: %s", exc, exc_info=True)
            # Capture screenshot for debugging
            try:
                screenshot_path = Path("playwright_error.png")
                await page.screenshot(path=str(screenshot_path))
                logger.info("Screenshot saved to %s", screenshot_path)
            except Exception:
                pass
            return ""

        finally:
            await browser.close()
