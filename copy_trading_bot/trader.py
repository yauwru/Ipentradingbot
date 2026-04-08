"""
Alpaca trading module.

Places market orders that mirror politician trades discovered by the scraper.
Uses the alpaca-py SDK (v2 REST API).
"""

import logging
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce, AssetStatus, AssetClass
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest

from copy_trading_bot.config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    POSITION_SIZE_USD,
    COPY_OPTIONS,
    MIN_TRADE_AMOUNT,
)

logger = logging.getLogger(__name__)

# Singleton client
_client: Optional[TradingClient] = None


def get_client() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=True,
        )
    return _client


# --------------------------------------------------------------------------- #
# Account helpers
# --------------------------------------------------------------------------- #

def get_account_info() -> dict:
    client = get_client()
    acct = client.get_account()
    return {
        "id": str(acct.id),
        "cash": float(acct.cash),
        "portfolio_value": float(acct.portfolio_value),
        "buying_power": float(acct.buying_power),
        "status": str(acct.status),
    }


def get_positions() -> dict[str, dict]:
    """Return current positions keyed by ticker symbol."""
    client = get_client()
    positions = client.get_all_positions()
    return {
        p.symbol: {
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "avg_entry_price": float(p.avg_entry_price),
        }
        for p in positions
    }


# --------------------------------------------------------------------------- #
# Asset validation
# --------------------------------------------------------------------------- #

def is_tradeable(ticker: str) -> bool:
    """Check that the ticker is a tradeable US equity on Alpaca."""
    client = get_client()
    try:
        asset = client.get_asset(ticker)
        return (
            asset.tradable
            and asset.status == AssetStatus.ACTIVE
            and asset.asset_class == AssetClass.US_EQUITY
        )
    except Exception as exc:
        logger.warning("Asset check failed for %s: %s", ticker, exc)
        return False


def get_latest_price(ticker: str) -> Optional[float]:
    """Return the latest trade price for a ticker."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        data_client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        req = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trade = data_client.get_stock_latest_trade(req)
        return float(trade[ticker].price)
    except Exception as exc:
        logger.warning("Price fetch failed for %s: %s", ticker, exc)
        return None


# --------------------------------------------------------------------------- #
# Order placement
# --------------------------------------------------------------------------- #

def place_order(trade: dict) -> Optional[dict]:
    """
    Place a market order on Alpaca mirroring `trade`.

    Returns a dict with order details, or None if the order was skipped/failed.
    """
    ticker = trade["ticker"]
    trade_type = trade["trade_type"]  # "buy" or "sell"
    asset_type = trade.get("asset_type", "stock")
    amount_usd = trade.get("amount_usd", 0)

    # Skip options if not configured
    if asset_type == "option" and not COPY_OPTIONS:
        logger.info("Skipping option trade for %s (COPY_OPTIONS=false)", ticker)
        return None

    # Skip tiny trades
    if amount_usd > 0 and amount_usd < MIN_TRADE_AMOUNT:
        logger.info(
            "Skipping %s %s – reported amount $%.0f below minimum $%.0f",
            trade_type, ticker, amount_usd, MIN_TRADE_AMOUNT,
        )
        return None

    if not is_tradeable(ticker):
        logger.warning("%s is not tradeable on Alpaca – skipping", ticker)
        return None

    side = OrderSide.BUY if trade_type == "buy" else OrderSide.SELL

    # For sells: check we actually hold the stock
    if side == OrderSide.SELL:
        positions = get_positions()
        if ticker not in positions:
            logger.info(
                "SELL signal for %s but no position held – skipping", ticker
            )
            return None

    client = get_client()

    # ── BUY: gunakan notional (dollar amount) → fractional shares ─────────
    if side == OrderSide.BUY:
        # Pastikan cukup buying power
        acct = get_account_info()
        if acct["buying_power"] < POSITION_SIZE_USD * 0.95:
            logger.warning(
                "Buying power $%.2f kurang dari position size $%.2f – skipping %s",
                acct["buying_power"], POSITION_SIZE_USD, ticker,
            )
            return None

        try:
            req = MarketOrderRequest(
                symbol=ticker,
                notional=round(POSITION_SIZE_USD, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )
            order = client.submit_order(req)
            result = {
                "alpaca_order_id": str(order.id),
                "ticker": ticker,
                "side": trade_type,
                "qty": 0,               # fractional, dikonfirmasi saat fill
                "position_usd": POSITION_SIZE_USD,
                "status": str(order.status),
            }
            logger.info(
                "Order placed: BUY $%.2f of %s (fractional) – order %s",
                POSITION_SIZE_USD, ticker, order.id,
            )
            return result
        except Exception as exc:
            logger.error("Failed to place BUY order for %s: %s", ticker, exc)
            return None

    # ── SELL: jual seluruh posisi yang dipegang ────────────────────────────
    positions = get_positions()
    pos = positions.get(ticker)
    if not pos:
        logger.info("SELL signal for %s but no position held – skipping", ticker)
        return None

    held_qty = float(pos["qty"])
    if held_qty <= 0:
        return None

    try:
        req = MarketOrderRequest(
            symbol=ticker,
            qty=held_qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        result = {
            "alpaca_order_id": str(order.id),
            "ticker": ticker,
            "side": trade_type,
            "qty": held_qty,
            "position_usd": float(pos["market_value"]),
            "status": str(order.status),
        }
        logger.info(
            "Order placed: SELL %.4f shares of %s @ market – order %s",
            held_qty, ticker, order.id,
        )
        return result
    except Exception as exc:
        logger.error("Failed to place SELL order for %s: %s", ticker, exc)
        return None
