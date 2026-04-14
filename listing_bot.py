"""
listing_bot.py – Feature 2: Telegram bot for AI-powered auto-listing.

Conversation flow
-----------------
1. User sends /sell (or just sends photos directly).
2. Bot collects photos (up to MAX_PHOTOS) until user sends /done or a non-photo.
3. Bot calls Claude to analyse the photos → VintedListing.
4. Bot sends a formatted preview and asks for confirmation.
5. User replies /confirm (or /edit <field> <value> to adjust individual fields).
6. Bot uses Playwright to post the listing and returns the live Vinted URL.

State machine
-------------
IDLE → COLLECTING_PHOTOS → AWAITING_CONFIRMATION → (back to IDLE)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from io import BytesIO
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from claude_helper import VintedListing, analyse_photos
from playwright_poster import post_listing
from config import settings

logger = logging.getLogger(__name__)

# Conversation states
COLLECTING_PHOTOS = 0
AWAITING_CONFIRMATION = 1

MAX_PHOTOS = 8  # Telegram groups up to 10; Claude handles up to 8 comfortably

# Context keys
CTX_PHOTOS = "photos"       # list[bytes]
CTX_LISTING = "listing"     # VintedListing


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _download_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bytes:
    """Download the best-quality version of a Telegram photo."""
    photo = update.message.photo[-1]  # last = largest
    file = await context.bot.get_file(photo.file_id)
    buf = BytesIO()
    await file.download_to_memory(buf)
    return buf.getvalue()


def _confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Post it!", callback_data="confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="edit"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]
    ])


def _preview_text(listing: VintedListing) -> str:
    return (
        "Here's your listing preview:\n\n"
        + listing.telegram_preview()
        + "\n\n"
        "Does this look good? You can post it, edit a field, or cancel."
    )


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Welcome to the Vinted Auto-Lister!\n\n"
        "• Send /sell then send your product photos.\n"
        "• I'll use AI to generate a Vinted listing for you.\n"
        "• Confirm and I'll post it automatically.\n\n"
        "Send /sell to begin."
    )


# ── /sell → start collecting photos ──────────────────────────────────────────

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[CTX_PHOTOS] = []
    await update.message.reply_text(
        "📸 Send me your product photos (up to 8).\n"
        "When done, send /done or just type anything."
    )
    return COLLECTING_PHOTOS


# ── Collect photos ────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photos: list[bytes] = context.user_data.setdefault(CTX_PHOTOS, [])

    if len(photos) >= MAX_PHOTOS:
        await update.message.reply_text(
            f"Maximum {MAX_PHOTOS} photos reached. Send /done to analyse."
        )
        return COLLECTING_PHOTOS

    photo_bytes = await _download_photo(update, context)
    photos.append(photo_bytes)
    count = len(photos)

    if count == 1:
        await update.message.reply_text(
            f"Got photo 1. Send more or /done when finished."
        )
    else:
        await update.message.reply_text(
            f"Got photo {count}. Send more or /done when finished."
        )
    return COLLECTING_PHOTOS


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _analyse_and_preview(update, context)


async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Any non-photo message while collecting triggers analysis."""
    photos: list[bytes] = context.user_data.get(CTX_PHOTOS, [])
    if not photos:
        await update.message.reply_text(
            "Please send at least one photo first, then /done."
        )
        return COLLECTING_PHOTOS
    return await _analyse_and_preview(update, context)


async def _analyse_and_preview(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    photos: list[bytes] = context.user_data.get(CTX_PHOTOS, [])
    if not photos:
        await update.message.reply_text("No photos received. Send /sell to start over.")
        return ConversationHandler.END

    thinking_msg = await update.message.reply_text(
        f"🔍 Analysing {len(photos)} photo(s) with Claude… please wait."
    )

    try:
        listing = await analyse_photos(photos)
        context.user_data[CTX_LISTING] = listing
    except Exception as exc:
        logger.error("Claude analysis failed: %s", exc)
        await thinking_msg.edit_text(
            f"❌ Analysis failed: {exc}\nSend /sell to try again."
        )
        return ConversationHandler.END

    await thinking_msg.delete()
    await update.message.reply_text(
        _preview_text(listing),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirmation_keyboard(),
    )
    return AWAITING_CONFIRMATION


# ── Confirmation callbacks ────────────────────────────────────────────────────

async def callback_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    listing: Optional[VintedListing] = context.user_data.get(CTX_LISTING)
    photos: list[bytes] = context.user_data.get(CTX_PHOTOS, [])

    if not listing or not photos:
        await query.edit_message_text("Session expired. Send /sell to start over.")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Posting your listing to Vinted…")

    try:
        listing_url = await post_listing(listing, photos)
    except Exception as exc:
        logger.error("Playwright posting error: %s", exc)
        await query.edit_message_text(
            f"❌ Failed to post listing: {exc}\nSend /sell to try again."
        )
        return ConversationHandler.END

    if listing_url:
        await query.edit_message_text(
            f"✅ Listing posted!\n{listing_url}",
            disable_web_page_preview=False,
        )
    else:
        await query.edit_message_text(
            "⚠️ Playwright finished but couldn't confirm the listing URL.\n"
            "Check your Vinted account to verify."
        )

    context.user_data.clear()
    return ConversationHandler.END


async def callback_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ To edit a field, reply with:\n"
        "`/set title Your new title`\n"
        "`/set price 25`\n"
        "`/set condition good`\n"
        "`/set description Your new description`\n"
        "`/set brand Nike`\n\n"
        "Then I'll show you an updated preview.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return AWAITING_CONFIRMATION


async def callback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelled. Send /sell to start a new listing.")
    context.user_data.clear()
    return ConversationHandler.END


# ── /set <field> <value> ──────────────────────────────────────────────────────

async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    listing: Optional[VintedListing] = context.user_data.get(CTX_LISTING)
    if not listing:
        await update.message.reply_text("No active listing. Send /sell to start.")
        return ConversationHandler.END

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /set <field> <value>")
        return AWAITING_CONFIRMATION

    field = args[0].lower()
    value = " ".join(args[1:])

    valid_conditions = {
        "new_with_tags", "new_without_tags", "very_good", "good", "satisfactory"
    }

    try:
        if field == "title":
            listing = replace(listing, title=value[:60])
        elif field == "price":
            listing = replace(listing, price_eur=float(value))
        elif field == "condition":
            cond = value.lower().replace(" ", "_")
            if cond not in valid_conditions:
                await update.message.reply_text(
                    f"Invalid condition. Choose from: {', '.join(valid_conditions)}"
                )
                return AWAITING_CONFIRMATION
            listing = replace(listing, condition=cond)
        elif field == "description":
            listing = replace(listing, description=value)
        elif field == "brand":
            listing = replace(listing, brand=value if value.lower() != "none" else None)
        elif field == "category":
            listing = replace(listing, category=value)
        else:
            await update.message.reply_text(
                "Unknown field. Valid fields: title, price, condition, description, brand, category"
            )
            return AWAITING_CONFIRMATION
    except ValueError as exc:
        await update.message.reply_text(f"Invalid value: {exc}")
        return AWAITING_CONFIRMATION

    context.user_data[CTX_LISTING] = listing
    await update.message.reply_text(
        _preview_text(listing),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_confirmation_keyboard(),
    )
    return AWAITING_CONFIRMATION


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Cancelled. Send /sell to start again.")
    context.user_data.clear()
    return ConversationHandler.END


# ── Build the Application ─────────────────────────────────────────────────────

def build_listing_bot() -> Application:
    app = Application.builder().token(settings.telegram_token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("sell", cmd_sell),
            # Also allow starting with a photo directly
            MessageHandler(filters.PHOTO, handle_photo),
        ],
        states={
            COLLECTING_PHOTOS: [
                MessageHandler(filters.PHOTO, handle_photo),
                CommandHandler("done", cmd_done),
                CommandHandler("cancel", cmd_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_non_photo),
            ],
            AWAITING_CONFIRMATION: [
                CallbackQueryHandler(callback_confirm, pattern="^confirm$"),
                CallbackQueryHandler(callback_edit, pattern="^edit$"),
                CallbackQueryHandler(callback_cancel, pattern="^cancel$"),
                CommandHandler("set", cmd_set),
                CommandHandler("cancel", cmd_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)

    return app


async def run_listing_bot() -> None:
    """Start the listing bot (runs until cancelled)."""
    app = build_listing_bot()
    logger.info("Listing bot started.")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    # Block until cancelled
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
