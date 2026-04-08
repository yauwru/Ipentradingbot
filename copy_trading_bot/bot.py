"""
Main bot orchestration.

On each run:
  1. Fetch top politicians from Capitol Trades.
  2. Scrape their recent trades.
  3. For each new (unseen) trade, place a matching order on Alpaca.
  4. Record everything in the local SQLite database.
"""

import logging
from datetime import datetime, timedelta

from copy_trading_bot import scraper, tracker, trader
from copy_trading_bot.config import COPY_OPTIONS, TRADE_EXECUTION_DAYS

logger = logging.getLogger(__name__)


def run_once() -> dict:
    """
    Execute one full cycle of the bot.

    Returns a summary dict:
        {
            "timestamp":       str,
            "politicians":     int,
            "trades_scraped":  int,
            "trades_new":      int,
            "orders_placed":   int,
            "orders_skipped":  int,
            "orders_failed":   int,
        }
    """
    timestamp = datetime.utcnow().isoformat()
    logger.info("=== Bot cycle started at %s ===", timestamp)

    # ------------------------------------------------------------------ #
    # 1. Discover top politicians
    # ------------------------------------------------------------------ #
    politicians = scraper.get_top_politicians(limit=15)
    logger.info("Tracking %d politicians", len(politicians))

    # ------------------------------------------------------------------ #
    # 2. Fetch recent trades
    # ------------------------------------------------------------------ #
    all_trades = scraper.get_all_recent_trades(politicians, days_back=180)
    logger.info("Scraped %d total trades", len(all_trades))

    # ------------------------------------------------------------------ #
    # 3. Process each trade
    # ------------------------------------------------------------------ #
    trades_new = 0
    orders_placed = 0
    orders_skipped = 0
    orders_failed = 0

    execution_cutoff = datetime.utcnow() - timedelta(days=TRADE_EXECUTION_DAYS)

    for trade in all_trades:
        # Skip options if not enabled
        if trade.get("asset_type") == "option" and not COPY_OPTIONS:
            tracker.mark_seen(trade)
            orders_skipped += 1
            continue

        # Deduplicate
        if tracker.is_seen(trade["trade_id"]):
            continue

        trades_new += 1
        tracker.mark_seen(trade)

        # Hanya eksekusi order untuk trades yang cukup baru
        try:
            trade_date = datetime.strptime(trade["trade_date"], "%Y-%m-%d")
        except ValueError:
            trade_date = datetime.utcnow()

        if trade_date < execution_cutoff:
            logger.info(
                "Old trade (>%dd): %s %s %s – mark seen, skip order",
                TRADE_EXECUTION_DAYS,
                trade["politician"], trade["trade_type"].upper(), trade["ticker"],
            )
            orders_skipped += 1
            continue

        logger.info(
            "New trade: %s %s %s (reported $%.0f, date %s)",
            trade["politician"],
            trade["trade_type"].upper(),
            trade["ticker"],
            trade.get("amount_usd", 0),
            trade["trade_date"],
        )

        # Place order
        result = trader.place_order(trade)

        if result is None:
            orders_skipped += 1
            continue

        if "alpaca_order_id" in result:
            orders_placed += 1
            tracker.record_order(
                trade_id=trade["trade_id"],
                alpaca_order_id=result["alpaca_order_id"],
                ticker=result["ticker"],
                side=result["side"],
                qty=result["qty"],
                position_usd=result["position_usd"],
                status=result["status"],
                politician=trade["politician"],
            )
        else:
            orders_failed += 1

    summary = {
        "timestamp": timestamp,
        "politicians": len(politicians),
        "trades_scraped": len(all_trades),
        "trades_new": trades_new,
        "orders_placed": orders_placed,
        "orders_skipped": orders_skipped,
        "orders_failed": orders_failed,
    }

    logger.info(
        "=== Cycle done | politicians=%d scraped=%d new=%d "
        "placed=%d skipped=%d failed=%d ===",
        summary["politicians"],
        summary["trades_scraped"],
        summary["trades_new"],
        summary["orders_placed"],
        summary["orders_skipped"],
        summary["orders_failed"],
    )

    return summary


def print_portfolio() -> None:
    """Log current Alpaca account state and positions."""
    try:
        acct = trader.get_account_info()
        positions = trader.get_positions()

        logger.info(
            "Account | cash=$%.2f  portfolio=$%.2f  buying_power=$%.2f",
            acct["cash"],
            acct["portfolio_value"],
            acct["buying_power"],
        )

        if positions:
            logger.info("Current positions:")
            for ticker, pos in positions.items():
                logger.info(
                    "  %-6s  qty=%-6.0f  value=$%.2f  avg_entry=$%.2f",
                    ticker,
                    pos["qty"],
                    pos["market_value"],
                    pos["avg_entry_price"],
                )
        else:
            logger.info("No open positions.")

    except Exception as exc:
        logger.error("Could not fetch portfolio info: %s", exc)
