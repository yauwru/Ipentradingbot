#!/usr/bin/env python3
"""
Capitol Trades Copy-Trading Bot
================================
Runs on a schedule, scrapes top politician trades from capitoltrades.com,
and mirrors them on your Alpaca paper-trading account.

Usage:
    python main.py              # run scheduler (default: every 60 min)
    python main.py --run-once   # single cycle then exit
    python main.py --portfolio  # show current portfolio and exit
    python main.py --history    # print trade history and exit
"""

import argparse
import logging
import sys
import time
from datetime import datetime

import schedule

from copy_trading_bot import bot, tracker, trader
from copy_trading_bot.config import SCRAPE_INTERVAL_MINUTES

# --------------------------------------------------------------------------- #
# Logging setup
# --------------------------------------------------------------------------- #

LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("copy_trading_bot/logs/bot.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("main")


# --------------------------------------------------------------------------- #
# Scheduled job
# --------------------------------------------------------------------------- #

def scheduled_job() -> None:
    """Wrapper so schedule can call it and we log any unexpected errors."""
    try:
        bot.print_portfolio()
        bot.run_once()
    except Exception as exc:
        logger.exception("Unhandled error in scheduled job: %s", exc)


# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #

def show_history() -> None:
    orders = tracker.get_all_orders()
    if not orders:
        print("No orders placed yet.")
        return

    print(f"\n{'='*80}")
    print(f"{'TRADE HISTORY':^80}")
    print(f"{'='*80}")
    header = f"{'Date':<22} {'Politician':<25} {'Ticker':<8} {'Side':<6} {'Qty':>6} {'USD':>10} {'Status':<12}"
    print(header)
    print("-" * 80)
    for o in orders:
        print(
            f"{o['placed_at'][:19]:<22} "
            f"{o['politician']:<25} "
            f"{o['ticker']:<8} "
            f"{o['side'].upper():<6} "
            f"{int(o['qty']):>6} "
            f"${o['position_usd']:>9,.0f} "
            f"{o['status']:<12}"
        )
    print(f"{'='*80}\n")


def show_portfolio() -> None:
    try:
        acct = trader.get_account_info()
        positions = trader.get_positions()
        print(f"\n{'='*60}")
        print(f"{'ALPACA PAPER ACCOUNT':^60}")
        print(f"{'='*60}")
        print(f"  Cash:           ${acct['cash']:>12,.2f}")
        print(f"  Portfolio value:${acct['portfolio_value']:>12,.2f}")
        print(f"  Buying power:   ${acct['buying_power']:>12,.2f}")
        print(f"  Status:         {acct['status']}")
        print(f"\n  Open Positions ({len(positions)}):")
        if positions:
            print(f"  {'Ticker':<8} {'Qty':>8} {'Mkt Value':>12} {'Avg Entry':>12}")
            print("  " + "-" * 44)
            for ticker, pos in positions.items():
                print(
                    f"  {ticker:<8} {pos['qty']:>8.0f} "
                    f"${pos['market_value']:>11,.2f} "
                    f"${pos['avg_entry_price']:>11,.2f}"
                )
        else:
            print("  (none)")
        print(f"{'='*60}\n")
    except Exception as exc:
        print(f"Error fetching portfolio: {exc}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Capitol Trades Copy-Trading Bot")
    parser.add_argument("--run-once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--portfolio", action="store_true", help="Show portfolio and exit")
    parser.add_argument("--history", action="store_true", help="Show order history and exit")
    args = parser.parse_args()

    # Initialise DB
    tracker.init_db()

    if args.portfolio:
        show_portfolio()
        sys.exit(0)

    if args.history:
        show_history()
        sys.exit(0)

    if args.run_once:
        logger.info("Running single cycle…")
        bot.print_portfolio()
        summary = bot.run_once()
        print(f"\nSummary: {summary}")
        sys.exit(0)

    # ------------------------------------------------------------------ #
    # Scheduler mode
    # ------------------------------------------------------------------ #
    logger.info(
        "Capitol Trades Copy-Trading Bot started. "
        "Scraping every %d minutes.",
        SCRAPE_INTERVAL_MINUTES,
    )

    # Run immediately on startup
    scheduled_job()

    # Then on the configured interval
    schedule.every(SCRAPE_INTERVAL_MINUTES).minutes.do(scheduled_job)

    # Also print portfolio every 30 minutes
    schedule.every(30).minutes.do(bot.print_portfolio)

    logger.info(
        "Scheduler active. Next run in %d minutes. Press Ctrl+C to stop.",
        SCRAPE_INTERVAL_MINUTES,
    )

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
