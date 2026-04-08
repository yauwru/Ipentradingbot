"""
STOCK Act Trades Scraper.

Menggunakan data publik dari House & Senate STOCK Act disclosures:
  - House: https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json
  - Senate: https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json

Data ini sama persis dengan yang ditampilkan Capitol Trades,
langsung dari sumber resmi pemerintah AS.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from copy_trading_bot.config import HEADERS, TOP_POLITICIANS

logger = logging.getLogger(__name__)

HOUSE_URL  = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"

# Nama politisi yang ingin di-track (lowercase untuk matching)
TRACKED_NAMES = [
    "nancy pelosi",
    "michael mccaul",
    "josh gottheimer",
    "dan crenshaw",
    "tommy tuberville",
    "brian mast",
    "ro khanna",
    "michael waltz",
    "greg gianforte",
    "virginia foxx",
    "marjorie taylor greene",
    "paul gosar",
    "david rouzer",
    "pete sessions",
    "shelley moore capito",
]


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #

def _get_json(url: str, retries: int = 3) -> Optional[list]:
    for attempt in range(retries):
        try:
            time.sleep(1)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("HTTP %s untuk %s", resp.status_code, url)
        except requests.RequestException as exc:
            logger.warning("Request error (attempt %s): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_amount(amount_str: str) -> float:
    """'$15,001 - $50,000' → midpoint."""
    if not amount_str:
        return 0
    clean = amount_str.replace("$", "").replace(",", "").strip()
    for sep in (" - ", "–", "-"):
        if sep in clean:
            parts = clean.split(sep, 1)
            vals = []
            for p in parts:
                try:
                    vals.append(float(p.strip()))
                except ValueError:
                    pass
            return sum(vals) / len(vals) if vals else 0
    try:
        return float(clean)
    except ValueError:
        return 0


def _parse_trade_type(raw: str) -> Optional[str]:
    raw = raw.lower().strip()
    if any(k in raw for k in ("purchase", "buy", "bought", "exchange")):
        return "buy"
    if any(k in raw for k in ("sale", "sell", "sold")):
        return "sell"
    return None


def _parse_date(s: str) -> Optional[datetime]:
    if not s or s.strip() in ("", "N/A", "--"):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d",
                "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def _name_to_slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace(".", "").replace(",", "")


def _is_tracked(name: str, tracked: list[str]) -> bool:
    name_lower = name.lower().strip()
    return any(t in name_lower or name_lower in t for t in tracked)


# --------------------------------------------------------------------------- #
# Top politician discovery (dari nama yang aktif di data terbaru)
# --------------------------------------------------------------------------- #

def get_top_politicians(limit: int = 15) -> list[dict]:
    """
    Kembalikan daftar politisi yang di-track.
    Selalu kembalikan hardcoded list + siapapun yang aktif di data terbaru.
    """
    politicians = []
    seen = set()

    # Hardcoded top traders
    for name in TRACKED_NAMES:
        slug = _name_to_slug(name)
        if slug not in seen:
            seen.add(slug)
            politicians.append({"slug": slug, "name": name.title()})

    logger.info("Tracking %d politisi", len(politicians))
    return politicians[:limit]


# --------------------------------------------------------------------------- #
# Fetch & filter trades
# --------------------------------------------------------------------------- #

def _process_house_trades(raw: list, cutoff: datetime, tracked: list[str]) -> list[dict]:
    trades = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        name = str(item.get("representative") or "").strip()
        if not name or not _is_tracked(name, tracked):
            continue

        ticker = str(item.get("ticker") or "").strip().upper()
        # Skip non-ticker entries
        if not ticker or ticker in ("", "--", "N/A") or len(ticker) > 6:
            continue
        # Skip options (contain spaces or slashes)
        if " " in ticker or "/" in ticker:
            continue

        trade_date = _parse_date(str(item.get("transaction_date") or ""))
        if not trade_date or trade_date < cutoff:
            continue

        trade_type = _parse_trade_type(str(item.get("type") or ""))
        if not trade_type:
            continue

        amount_usd = _parse_amount(str(item.get("amount") or ""))
        asset_desc = str(item.get("asset_description") or "").lower()
        asset_type = "option" if "option" in asset_desc else "stock"

        slug = _name_to_slug(name)
        trade_id = f"{slug}_{ticker}_{trade_type}_{trade_date.strftime('%Y%m%d')}_{int(amount_usd)}"

        trades.append({
            "politician": name,
            "slug": slug,
            "ticker": ticker,
            "trade_type": trade_type,
            "trade_date": trade_date.strftime("%Y-%m-%d"),
            "report_date": str(item.get("disclosure_date") or datetime.utcnow().strftime("%Y-%m-%d")),
            "amount_usd": amount_usd,
            "asset_type": asset_type,
            "trade_id": trade_id,
            "source": "house",
        })
    return trades


def _process_senate_trades(raw: list, cutoff: datetime, tracked: list[str]) -> list[dict]:
    trades = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        # Senate data structure berbeda sedikit
        first = str(item.get("first_name") or "").strip()
        last  = str(item.get("last_name") or "").strip()
        name  = f"{first} {last}".strip() if (first or last) else str(item.get("senator") or "").strip()

        if not name or not _is_tracked(name, tracked):
            continue

        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("", "--", "N/A") or len(ticker) > 6:
            continue
        if " " in ticker or "/" in ticker:
            continue

        trade_date = _parse_date(str(item.get("transaction_date") or
                                     item.get("date") or ""))
        if not trade_date or trade_date < cutoff:
            continue

        trade_type = _parse_trade_type(str(item.get("type") or
                                          item.get("transaction_type") or ""))
        if not trade_type:
            continue

        amount_usd = _parse_amount(str(item.get("amount") or ""))
        asset_desc = str(item.get("asset_description") or "").lower()
        asset_type = "option" if "option" in asset_desc else "stock"

        slug = _name_to_slug(name)
        trade_id = f"{slug}_{ticker}_{trade_type}_{trade_date.strftime('%Y%m%d')}_{int(amount_usd)}"

        trades.append({
            "politician": name,
            "slug": slug,
            "ticker": ticker,
            "trade_type": trade_type,
            "trade_date": trade_date.strftime("%Y-%m-%d"),
            "report_date": str(item.get("disclosure_date") or datetime.utcnow().strftime("%Y-%m-%d")),
            "amount_usd": amount_usd,
            "asset_type": asset_type,
            "trade_id": trade_id,
            "source": "senate",
        })
    return trades


# --------------------------------------------------------------------------- #
# Public interface
# --------------------------------------------------------------------------- #

def get_politician_trades(slug: str, days_back: int = 7) -> list[dict]:
    """Tidak dipakai langsung — pakai get_all_recent_trades()."""
    return []


def get_all_recent_trades(politicians: list[dict], days_back: int = 30) -> list[dict]:
    """
    Fetch semua trades terbaru dari House & Senate STOCK Act data.
    days_back=30 karena disclosure biasanya delay 30-45 hari dari tanggal transaksi.
    """
    cutoff  = datetime.utcnow() - timedelta(days=days_back)
    tracked = [p["name"].lower() for p in politicians] + TRACKED_NAMES

    all_trades: list[dict] = []

    # ── House trades ──────────────────────────────────────────────────────
    logger.info("Fetching House STOCK Act data...")
    house_raw = _get_json(HOUSE_URL)
    if house_raw:
        house_trades = _process_house_trades(house_raw, cutoff, tracked)
        logger.info("House: %d trades ditemukan untuk politisi yang di-track", len(house_trades))
        all_trades.extend(house_trades)
    else:
        logger.warning("Gagal fetch House data")

    # ── Senate trades ─────────────────────────────────────────────────────
    logger.info("Fetching Senate STOCK Act data...")
    senate_raw = _get_json(SENATE_URL)
    if senate_raw:
        senate_trades = _process_senate_trades(senate_raw, cutoff, tracked)
        logger.info("Senate: %d trades ditemukan untuk politisi yang di-track", len(senate_trades))
        all_trades.extend(senate_trades)
    else:
        logger.warning("Gagal fetch Senate data")

    # Dedup by trade_id (kalau ada duplikat antar source)
    seen_ids: set[str] = set()
    unique_trades = []
    for t in all_trades:
        if t["trade_id"] not in seen_ids:
            seen_ids.add(t["trade_id"])
            unique_trades.append(t)

    logger.info("Total trades unik: %d (dari %d raw)", len(unique_trades), len(all_trades))
    return unique_trades
