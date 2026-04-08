"""
Capitol Trades scraper.

Capitol Trades adalah Next.js app — data trades ada di:
  1. __NEXT_DATA__ JSON yang di-embed di HTML (SSR)
  2. API endpoint internal: /api/trades
  3. HTML parsing sebagai fallback terakhir
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

from copy_trading_bot.config import CAPITOL_TRADES_BASE_URL, HEADERS, TOP_POLITICIANS

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #

def _get(url: str, params: dict = None, retries: int = 3,
         extra_headers: dict = None) -> Optional[requests.Response]:
    """HTTP GET dengan retry dan polite delay."""
    h = {**HEADERS, **(extra_headers or {})}
    for attempt in range(retries):
        try:
            time.sleep(1.5)
            resp = requests.get(url, headers=h, params=params, timeout=15)
            if resp.status_code == 200:
                return resp
            logger.warning("HTTP %s untuk %s", resp.status_code, url)
        except requests.RequestException as exc:
            logger.warning("Request error (attempt %s): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


# --------------------------------------------------------------------------- #
# Helpers untuk parsing
# --------------------------------------------------------------------------- #

def _parse_amount(amount_str: str) -> float:
    """'$1K – $15K' atau '$1,000 - $15,000' → midpoint dalam dollar."""
    if not amount_str:
        return 0
    clean = amount_str.replace("$", "").replace(",", "").strip()
    for sep in ("–", "-", "to"):
        if sep in clean:
            parts = [p.strip() for p in clean.split(sep, 1)]
            vals = [_k_to_float(p) for p in parts if p.strip()]
            return sum(vals) / len(vals) if vals else 0
    return _k_to_float(clean)


def _k_to_float(s: str) -> float:
    s = s.strip().upper().replace(",", "")
    if not s:
        return 0
    if s.endswith("K"):
        return float(s[:-1]) * 1_000
    if s.endswith("M"):
        return float(s[:-1]) * 1_000_000
    if s.endswith("B"):
        return float(s[:-1]) * 1_000_000_000
    try:
        return float(s)
    except ValueError:
        return 0


def _parse_trade_type(raw: str) -> Optional[str]:
    raw = raw.lower().strip()
    if any(k in raw for k in ("purchase", "buy", "bought")):
        return "buy"
    if any(k in raw for k in ("sale", "sell", "sold")):
        return "sell"
    return None


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%d %b %Y",
                "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Politician discovery
# --------------------------------------------------------------------------- #

def get_top_politicians(limit: int = 15) -> list[dict]:
    """
    Fetch daftar politisi dari Capitol Trades.
    Coba ambil dari __NEXT_DATA__, fallback ke link scraping.
    """
    url = f"{CAPITOL_TRADES_BASE_URL}/politicians"
    resp = _get(url)
    if not resp:
        logger.warning("Tidak bisa fetch halaman politicians, pakai hardcoded list.")
        return [{"slug": s, "name": s.replace("-", " ").title()} for s in TOP_POLITICIANS]

    politicians = []

    # ── Coba __NEXT_DATA__ JSON dulu ──────────────────────────────────────
    soup = BeautifulSoup(resp.text, "lxml")
    next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_data_tag:
        try:
            data = json.loads(next_data_tag.string)
            # Navigasi ke data politisi di pageProps
            page_props = data.get("props", {}).get("pageProps", {})
            # Capitol Trades menyimpan list di berbagai key
            for key in ("politicians", "data", "items", "results"):
                items = page_props.get(key, [])
                if isinstance(items, list) and items:
                    for item in items:
                        slug = (item.get("bioguideId") or item.get("slug") or
                                item.get("id") or "")
                        name = (item.get("name") or item.get("displayName") or
                                item.get("fullName") or str(slug))
                        if slug:
                            politicians.append({"slug": str(slug), "name": str(name)})
                    if politicians:
                        logger.info("Ditemukan %d politisi dari __NEXT_DATA__", len(politicians))
                        break
        except Exception as exc:
            logger.debug("__NEXT_DATA__ parse error: %s", exc)

    # ── Fallback: scrape link href ─────────────────────────────────────────
    if not politicians:
        seen = set()
        for a in soup.select("a[href^='/politicians/']"):
            href = a.get("href", "")
            parts = href.strip("/").split("/")
            if len(parts) >= 2:
                slug = parts[1]
                # Skip pagination dan filter links
                if slug and slug not in seen and "?" not in slug and len(slug) > 2:
                    seen.add(slug)
                    name = a.get_text(strip=True) or slug.replace("-", " ").title()
                    politicians.append({"slug": slug, "name": name})

    # ── Merge dengan hardcoded list ────────────────────────────────────────
    slugs_seen = {p["slug"] for p in politicians}
    for s in TOP_POLITICIANS:
        if s not in slugs_seen:
            politicians.append({"slug": s, "name": s.replace("-", " ").title()})

    logger.info("Total politisi yang akan di-track: %d", min(limit, len(politicians)))
    return politicians[:limit]


# --------------------------------------------------------------------------- #
# Trade scraping — tiga strategi
# --------------------------------------------------------------------------- #

def _trades_from_next_data(slug: str, html: str, cutoff: datetime) -> list[dict]:
    """Strategi 1: Ekstrak trades dari __NEXT_DATA__ JSON."""
    trades = []
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        return trades

    try:
        data = json.loads(tag.string)
        page_props = data.get("props", {}).get("pageProps", {})

        # Capitol Trades menyimpan trades di berbagai key
        raw_trades = []
        for key in ("trades", "data", "items", "results", "recentTrades"):
            val = page_props.get(key)
            if isinstance(val, list) and val:
                raw_trades = val
                break
            # Kadang nested satu level lagi
            if isinstance(val, dict):
                for sub_key in ("trades", "data", "items"):
                    if isinstance(val.get(sub_key), list):
                        raw_trades = val[sub_key]
                        break

        for item in raw_trades:
            trade = _parse_trade_item(item, slug, cutoff)
            if trade:
                trades.append(trade)

    except Exception as exc:
        logger.debug("__NEXT_DATA__ trades parse error untuk %s: %s", slug, exc)

    return trades


def _trades_from_api(slug: str, cutoff: datetime) -> list[dict]:
    """
    Strategi 2: Capitol Trades internal API endpoints.
    Coba beberapa format URL yang umum dipakai Next.js apps.
    """
    trades = []
    api_headers = {
        **HEADERS,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{CAPITOL_TRADES_BASE_URL}/politicians/{slug}/trades",
    }

    candidate_urls = [
        # Format API yang umum di Capitol Trades
        f"{CAPITOL_TRADES_BASE_URL}/api/trades",
        f"{CAPITOL_TRADES_BASE_URL}/api/v1/trades",
        f"{CAPITOL_TRADES_BASE_URL}/_next/data/trades.json",
    ]

    candidate_params = [
        {"politician": slug, "page": 1, "pageSize": 50},
        {"politicianId": slug, "page": 1, "limit": 50},
        {"slug": slug, "page": 1},
    ]

    for url in candidate_urls:
        for params in candidate_params:
            resp = _get(url, params=params, retries=1, extra_headers=api_headers)
            if not resp:
                continue
            try:
                payload = resp.json()
                # Navigasi ke list trades
                items = None
                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    for key in ("trades", "data", "items", "results"):
                        if isinstance(payload.get(key), list):
                            items = payload[key]
                            break

                if items:
                    for item in items:
                        trade = _parse_trade_item(item, slug, cutoff)
                        if trade:
                            trades.append(trade)
                    if trades:
                        logger.info("API berhasil untuk %s: %d trades", slug, len(trades))
                        return trades
            except Exception:
                continue

    return trades


def _parse_trade_item(item: dict, slug: str, cutoff: datetime) -> Optional[dict]:
    """
    Parse satu item trade dari JSON (bisa dari __NEXT_DATA__ atau API).
    Capitol Trades menggunakan field names yang beragam.
    """
    if not isinstance(item, dict):
        return None

    # ── Ticker ────────────────────────────────────────────────────────────
    ticker = (
        item.get("ticker") or item.get("symbol") or item.get("issuerTicker") or
        item.get("asset", {}).get("ticker") if isinstance(item.get("asset"), dict) else None or
        ""
    )
    if not ticker:
        # Coba dari nested issuer
        issuer = item.get("issuer") or {}
        if isinstance(issuer, dict):
            ticker = issuer.get("ticker") or issuer.get("symbol") or ""
    ticker = str(ticker).strip().upper()
    if not ticker or len(ticker) > 6:
        return None

    # ── Trade type ────────────────────────────────────────────────────────
    type_raw = (
        item.get("type") or item.get("tradeType") or item.get("transactionType") or
        item.get("transaction") or item.get("action") or ""
    )
    trade_type = _parse_trade_type(str(type_raw))
    if not trade_type:
        return None

    # ── Trade date ────────────────────────────────────────────────────────
    date_raw = (
        item.get("tradeDate") or item.get("transactionDate") or
        item.get("reportDate") or item.get("date") or item.get("filedAt") or ""
    )
    trade_date = _parse_date(str(date_raw))
    if not trade_date or trade_date < cutoff:
        return None

    # ── Amount ────────────────────────────────────────────────────────────
    amount_raw = (
        item.get("amount") or item.get("value") or item.get("tradeSize") or
        item.get("size") or ""
    )
    amount_usd = _parse_amount(str(amount_raw)) if amount_raw else 0

    # ── Asset type ────────────────────────────────────────────────────────
    asset_type_raw = str(
        item.get("assetType") or item.get("type") or
        item.get("instrumentType") or ""
    ).lower()
    asset_type = "option" if "option" in asset_type_raw else "stock"

    # ── Politician name ───────────────────────────────────────────────────
    pol = item.get("politician") or {}
    if isinstance(pol, dict):
        pol_name = pol.get("name") or pol.get("displayName") or slug.replace("-", " ").title()
    else:
        pol_name = slug.replace("-", " ").title()

    trade_id = f"{slug}_{ticker}_{trade_type}_{trade_date.strftime('%Y%m%d')}_{int(amount_usd)}"

    return {
        "politician": pol_name,
        "slug": slug,
        "ticker": ticker,
        "trade_type": trade_type,
        "trade_date": trade_date.strftime("%Y-%m-%d"),
        "report_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "amount_usd": amount_usd,
        "asset_type": asset_type,
        "trade_id": trade_id,
    }


def _trades_from_html(slug: str, html: str, cutoff: datetime) -> list[dict]:
    """Strategi 3: Parse HTML secara langsung (last resort)."""
    trades = []
    soup = BeautifulSoup(html, "lxml")

    rows = (
        soup.select("table tbody tr") or
        soup.select("tr[class*='trade']") or
        soup.select("div[class*='trade-row']") or
        soup.find_all("tr")
    )

    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue

        text = [c.get_text(" ", strip=True) for c in cells]
        ticker = trade_type_raw = trade_date_str = amount_str = ""
        asset_type = "stock"

        for t in text:
            t_clean = t.strip()
            if not ticker and t_clean.isupper() and 1 <= len(t_clean) <= 5 and t_clean.isalpha():
                ticker = t_clean
            if not trade_type_raw and _parse_trade_type(t_clean):
                trade_type_raw = t_clean
            if not trade_date_str and _parse_date(t_clean):
                trade_date_str = t_clean
            if "$" in t_clean:
                amount_str = t_clean
            if "option" in t_clean.lower():
                asset_type = "option"

        if not ticker or not trade_type_raw:
            continue

        trade_type = _parse_trade_type(trade_type_raw)
        trade_date = _parse_date(trade_date_str)

        if not trade_type or not trade_date or trade_date < cutoff:
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

    return trades


# --------------------------------------------------------------------------- #
# Public interface
# --------------------------------------------------------------------------- #

def get_politician_trades(slug: str, days_back: int = 7) -> list[dict]:
    """
    Ambil trades terbaru untuk satu politisi.
    Coba tiga strategi secara berurutan:
      1. __NEXT_DATA__ JSON dari halaman trades
      2. Capitol Trades internal API
      3. HTML parsing biasa
    """
    url = f"{CAPITOL_TRADES_BASE_URL}/politicians/{slug}/trades"
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    resp = _get(url)
    if not resp:
        logger.warning("Tidak bisa fetch halaman trades untuk %s", slug)
        return []

    html = resp.text

    # Strategi 1: __NEXT_DATA__
    trades = _trades_from_next_data(slug, html, cutoff)
    if trades:
        logger.info("[%s] %d trades dari __NEXT_DATA__", slug, len(trades))
        return trades

    # Strategi 2: API endpoint
    trades = _trades_from_api(slug, cutoff)
    if trades:
        logger.info("[%s] %d trades dari API", slug, len(trades))
        return trades

    # Strategi 3: HTML parsing
    trades = _trades_from_html(slug, html, cutoff)
    if trades:
        logger.info("[%s] %d trades dari HTML parsing", slug, len(trades))
    else:
        logger.warning("[%s] 0 trades ditemukan dari semua strategi", slug)

    return trades


def get_all_recent_trades(politicians: list[dict], days_back: int = 7) -> list[dict]:
    """Ambil trades terbaru dari semua politisi."""
    all_trades = []
    for pol in politicians:
        trades = get_politician_trades(pol["slug"], days_back=days_back)
        all_trades.extend(trades)
    logger.info("Total trades dari semua politisi: %d", len(all_trades))
    return all_trades
