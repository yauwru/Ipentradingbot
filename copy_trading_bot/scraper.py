"""
Capitol Trades scraper.

Fetches recent politician stock trades from https://www.capitoltrades.com
and returns structured trade data for the bot to copy.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

from copy_trading_bot.config import CAPITOL_TRADES_BASE_URL, HEADERS, TOP_POLITICIANS

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _get(url: str, params: dict = None, retries: int = 3) -> Optional[requests.Response]:
    """HTTP GET with retry + polite delay."""
    for attempt in range(retries):
        try:
            time.sleep(1.5)  # polite crawl delay
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 200:
                return resp
            logger.warning("HTTP %s for %s", resp.status_code, url)
        except requests.RequestException as exc:
            logger.warning("Request error (%s): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


# --------------------------------------------------------------------------- #
# Top-politician discovery (by number of recent trades / profile prominence)
# --------------------------------------------------------------------------- #

def get_top_politicians(limit: int = 15) -> list[dict]:
    """
    Fetch the politicians page and return the top `limit` politicians
    sorted by trade count / activity.  Falls back to the hardcoded list
    in config.py if scraping fails.
    """
    url = f"{CAPITOL_TRADES_BASE_URL}/politicians"
    resp = _get(url)
    if not resp:
        logger.warning("Could not fetch politicians page, using hardcoded list.")
        return [{"slug": s, "name": s.replace("-", " ").title()} for s in TOP_POLITICIANS]

    soup = BeautifulSoup(resp.text, "lxml")
    politicians = []

    # Capitol Trades renders politician cards / table rows
    for row in soup.select("a[href^='/politicians/']"):
        href = row.get("href", "")
        # Skip non-profile links (e.g. /politicians?page=…)
        parts = href.strip("/").split("/")
        if len(parts) < 2 or parts[1] in ("", "page"):
            continue
        slug = parts[1]
        name = row.get_text(strip=True) or slug.replace("-", " ").title()
        if slug and {"slug": slug, "name": name} not in politicians:
            politicians.append({"slug": slug, "name": name})

    # Merge with hardcoded list so we never miss key traders
    slugs_seen = {p["slug"] for p in politicians}
    for s in TOP_POLITICIANS:
        if s not in slugs_seen:
            politicians.append({"slug": s, "name": s.replace("-", " ").title()})

    return politicians[:limit]


# --------------------------------------------------------------------------- #
# Trade scraping
# --------------------------------------------------------------------------- #

def _parse_amount(amount_str: str) -> float:
    """Convert '$1K – $15K' → midpoint in dollars."""
    if not amount_str:
        return 0
    # strip dollar signs, commas, spaces
    clean = amount_str.replace("$", "").replace(",", "").strip()
    # handle ranges like "1K – 15K" or "1,000 – 15,000"
    if "–" in clean or "-" in clean:
        sep = "–" if "–" in clean else "-"
        parts = [p.strip() for p in clean.split(sep)]
        vals = []
        for p in parts:
            vals.append(_k_to_float(p))
        return sum(vals) / len(vals)
    return _k_to_float(clean)


def _k_to_float(s: str) -> float:
    s = s.strip().upper()
    if s.endswith("K"):
        return float(s[:-1]) * 1_000
    if s.endswith("M"):
        return float(s[:-1]) * 1_000_000
    try:
        return float(s)
    except ValueError:
        return 0


def _parse_trade_type(raw: str) -> Optional[str]:
    """Map Capitol Trades labels → 'buy' | 'sell' | None."""
    raw = raw.lower().strip()
    if "purchase" in raw or "buy" in raw:
        return "buy"
    if "sale" in raw or "sell" in raw:
        return "sell"
    return None


def get_politician_trades(slug: str, days_back: int = 7) -> list[dict]:
    """
    Return a list of trades for one politician from the last `days_back` days.

    Each trade dict:
        {
            "politician":  str,
            "slug":        str,
            "ticker":      str,
            "trade_type":  "buy" | "sell",
            "trade_date":  str (YYYY-MM-DD),
            "report_date": str (YYYY-MM-DD),
            "amount_usd":  float,
            "asset_type":  "stock" | "option" | "other",
            "trade_id":    str,   # dedup key
        }
    """
    url = f"{CAPITOL_TRADES_BASE_URL}/politicians/{slug}/trades"
    resp = _get(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    trades = []

    # Capitol Trades uses a <table> or a list of trade-cards.
    # We target both layouts.
    rows = soup.select("table tbody tr") or soup.select(".trade-row, .q-table__row")

    if not rows:
        # Fallback: try to find any row-like containers
        rows = soup.find_all("tr")

    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue

        text = [c.get_text(strip=True) for c in cells]

        # Heuristic column detection
        ticker = ""
        trade_type_raw = ""
        trade_date_str = ""
        amount_str = ""
        asset_type = "stock"

        for i, t in enumerate(text):
            # Ticker: 1-5 uppercase letters
            if not ticker and t.isupper() and 1 <= len(t) <= 5 and t.isalpha():
                ticker = t
            # Trade type
            if not trade_type_raw and any(kw in t.lower() for kw in ("purchase", "sale", "buy", "sell")):
                trade_type_raw = t
            # Date: YYYY-MM-DD or MM/DD/YYYY
            if not trade_date_str and len(t) in (8, 10) and any(c.isdigit() for c in t):
                trade_date_str = t
            # Amount
            if "$" in t or (t.endswith("K") and t[:-1].replace(",", "").replace(".", "").isdigit()):
                amount_str = t
            # Asset type
            if "option" in t.lower():
                asset_type = "option"

        if not ticker or not trade_type_raw:
            continue

        trade_type = _parse_trade_type(trade_type_raw)
        if trade_type is None:
            continue

        # Parse trade date
        trade_date = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%d %b %Y"):
            try:
                trade_date = datetime.strptime(trade_date_str, fmt)
                break
            except ValueError:
                continue

        if trade_date is None or trade_date < cutoff:
            continue

        amount_usd = _parse_amount(amount_str)

        trade_id = f"{slug}_{ticker}_{trade_type}_{trade_date.strftime('%Y%m%d')}_{int(amount_usd)}"

        trades.append({
            "politician": slug.replace("-", " ").title(),
            "slug": slug,
            "ticker": ticker,
            "trade_type": trade_type,
            "trade_date": trade_date.strftime("%Y-%m-%d"),
            "report_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "amount_usd": amount_usd,
            "asset_type": asset_type,
            "trade_id": trade_id,
        })

    logger.info("Scraped %d trades for %s", len(trades), slug)
    return trades


# --------------------------------------------------------------------------- #
# Aggregate: all recent trades across top politicians
# --------------------------------------------------------------------------- #

def get_all_recent_trades(politicians: list[dict], days_back: int = 7) -> list[dict]:
    """Fetch recent trades for every politician in the list."""
    all_trades = []
    for pol in politicians:
        trades = get_politician_trades(pol["slug"], days_back=days_back)
        all_trades.extend(trades)
        logger.info("Total trades so far: %d", len(all_trades))
    return all_trades
