"""
main.py – Unified entry point.

Runs both features concurrently in the same asyncio event loop:

    Feature 1: monitor.run_monitor()      – polling loop
    Feature 2: listing_bot.run_listing_bot() – Telegram ConversationHandler

Usage
-----
    python main.py               # both features
    python main.py --monitor     # Feature 1 only
    python main.py --bot         # Feature 2 only
    python monitor.py            # Feature 1 standalone (same as --monitor)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from monitor import run_monitor
from listing_bot import run_listing_bot


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Reduce noise from httpx / telegram internals
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


async def _run_both() -> None:
    """Run monitor and listing bot concurrently; cancel all on first failure."""
    monitor_task = asyncio.create_task(run_monitor(), name="monitor")
    bot_task = asyncio.create_task(run_listing_bot(), name="listing_bot")

    done, pending = await asyncio.wait(
        {monitor_task, bot_task},
        return_when=asyncio.FIRST_EXCEPTION,
    )

    # Surface any exception
    for task in done:
        if task.exception():
            logging.error(
                "Task '%s' raised an exception: %s",
                task.get_name(),
                task.exception(),
            )

    # Cancel remaining tasks gracefully
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Vinted Bot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--monitor", action="store_true", help="Feature 1 only")
    group.add_argument("--bot", action="store_true", help="Feature 2 only")
    args = parser.parse_args()

    _setup_logging()

    try:
        if args.monitor:
            asyncio.run(run_monitor())
        elif args.bot:
            asyncio.run(run_listing_bot())
        else:
            asyncio.run(_run_both())
    except KeyboardInterrupt:
        logging.info("Shutting down.")


if __name__ == "__main__":
    main()
