"""
Capitol Trades HTML Scraper.

Data trades ADA di HTML halaman politisi sebagai <table>.
Format tabel: Traded Issuer | Published | Traded | Filed After | Type | Size

URL: https://www.capitoltrades.com/politicians/{bioguide_id}
Ticker format di HTML: GOOGL:US  → kita strip :US → GOOGL
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

from copy_trading_bot.config import HEADERS, TOP_POLITICIANS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.capitoltrades.com"

# Bioguide ID → nama politisi (dari data resmi Congress)
# Daftar ini mencakup politisi dengan track record trading terkuat
POLITICIAN_IDS = {
    "P000197": "Nancy Pelosi",
    "M001157": "Michael McCaul",
    "G000583": "Josh Gottheimer",
    "C001120": "Dan Crenshaw",
    "T000278": "Tommy Tuberville",
    "M001199": "Brian Mast",
    "K000389": "Ro Khanna",
    "W000823": "Michael Waltz",
    "G000584": "Greg Gianforte",
    "F000450": "Virginia Foxx",
    "G000596": "Marjorie Taylor Greene",
    "G000565": "Paul Gosar",
    "R000603": "David Rouzer",
    "S000522": "Pete Sessions",
    "C001047": "Shelley Moore Capito",
}


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

def _get(url: str, params: dict = None, retries: int = 3) -> Optional[requests.Response]:
    h = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }
    for attempt in range(retries):
        try:
            time.sleep(1.5)
            resp = requests.get(url, headers=h, params=params, timeout=20)
            if resp.status_code == 200:
                return resp
            logger.warning("HTTP %s untuk %s", resp.status_code, url)
        except requests.RequestException as exc:
            logger.warning("Request error (attempt %s): %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return None


# --------------------------------------------------------------------------- #
# Politician discovery (dari sitemap + hardcoded)
# --------------------------------------------------------------------------- #

def _get_politicians_from_sitemap() -> dict[str, str]:
    """Ambil ID politisi dari sitemap Capitol Trades. Return {id: url}."""
    resp = _get(f"{BASE_URL}/politicians/sitemap.xml")
    if not resp:
        return {}

    ids = {}
    urls = re.findall(r'<loc>(https://www\.capitoltrades\.com/politicians/([A-Z0-9]+))</loc>',
                      resp.text)
    for full_url, bio_id in urls:
        ids[bio_id] = full_url

    logger.info("Sitemap: ditemukan %d politisi", len(ids))
    return ids


def get_top_politicians(limit: int = 15) -> list[dict]:
    """
    Kembalikan daftar politisi dengan bioguide ID yang valid.
    Pakai hardcoded list + tambahkan dari sitemap jika ada slot kosong.
    """
    politicians = []
    for bio_id, name in POLITICIAN_IDS.items():
        politicians.append({
            "slug": bio_id,
            "name": name,
            "bio_id": bio_id,
        })

    logger.info("Tracking %d politisi", min(limit, len(politicians)))
    return politicians[:limit]


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def _parse_ticker(raw: str) -> str:
    """'GOOGL:US' → 'GOOGL', 'AB:US' → 'AB'."""
    if not raw:
        return ""
    ticker = raw.split(":")[0].strip().upper()
    # Hanya terima ticker valid (1-5 huruf)
    if ticker and ticker.isalpha() and 1 <= len(ticker) <= 5:
        return ticker
    return ""


def _parse_amount(raw: str) -> float:
    """'1M–5M', '500K–1M', '$15,001 - $50,000' → midpoint float."""
    if not raw:
        return 0
    clean = raw.replace("$", "").replace(",", "").strip()
    # Cari semua angka dengan suffix K/M/B
    parts = re.split(r'[–\-–to]+', clean, maxsplit=1)
    vals = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r'^([\d.]+)([KMBkmb]?)$', p)
        if m:
            num, suffix = float(m.group(1)), m.group(2).upper()
            mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
            vals.append(num * mult)
    return sum(vals) / len(vals) if vals else 0


def _parse_trade_type(raw: str) -> Optional[str]:
    raw = raw.lower().strip()
    if any(k in raw for k in ("buy", "purchase", "bought")):
        return "buy"
    if any(k in raw for k in ("sell", "sale", "sold")):
        return "sell"
    return None


def _parse_date(raw: str) -> Optional[datetime]:
    """Parse tanggal: '26 Jan 2026', 'Jan 26, 2026', '2026-01-26', dll."""
    if not raw or raw.strip() in ("", "--", "N/A"):
        return None
    raw = raw.strip()
    for fmt in ("%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d",
                "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Parse tabel trades dari HTML
# --------------------------------------------------------------------------- #

def _parse_trades_table(soup: BeautifulSoup, politician_name: str,
                         bio_id: str, cutoff: datetime) -> list[dict]:
    """
    Parse tabel trades dari halaman politisi Capitol Trades.

    Kolom tabel: Traded Issuer | Published | Traded | Filed After | Type | Size
    Ticker ada dalam format 'GOOGL:US' di dalam sel pertama atau kedua.
    """
    trades = []
    table = soup.find("table")
    if not table:
        logger.debug("[%s] Tidak ada <table> di halaman", bio_id)
        return trades

    rows = table.find_all("tr")
    if len(rows) < 2:
        return trades

    for row in rows[1:]:  # Skip header
        cells = row.find_all(["td", "th"])
        if len(cells) < 4:
            continue

        # Ambil teks semua sel
        texts = [c.get_text(" ", strip=True) for c in cells]

        # ── Cari ticker (format TICKER:US) ───────────────────────────────
        ticker = ""
        for t in texts:
            m = re.search(r'\b([A-Z]{1,5}):US\b', t)
            if m:
                ticker = m.group(1)
                break

        if not ticker:
            continue

        # ── Cari trade type ───────────────────────────────────────────────
        trade_type = None
        for t in texts:
            trade_type = _parse_trade_type(t)
            if trade_type:
                break
        if not trade_type:
            continue

        # ── Cari tanggal transaksi (Traded date, bukan Published) ─────────
        # Biasanya kolom ke-3 (index 2) = Published, kolom ke-4 (index 3) = Traded
        trade_date = None
        dates_found = []
        for t in texts:
            d = _parse_date(t)
            if d:
                dates_found.append(d)

        # Ambil tanggal yang paling tua (= transaction date, bukan disclosure date)
        if dates_found:
            trade_date = min(dates_found)  # transaction date < disclosure date

        if not trade_date or trade_date < cutoff:
            continue

        # ── Cari amount ───────────────────────────────────────────────────
        amount_usd = 0
        for t in texts:
            if re.search(r'\d+[KMB]', t.upper()) or '$' in t:
                amount_usd = _parse_amount(t)
                if amount_usd > 0:
                    break

        # ── Asset type ────────────────────────────────────────────────────
        full_text = " ".join(texts).lower()
        asset_type = "option" if "option" in full_text else "stock"

        slug = bio_id.lower()
        trade_id = (f"{slug}_{ticker}_{trade_type}_"
                    f"{trade_date.strftime('%Y%m%d')}_{int(amount_usd)}")

        trades.append({
            "politician": politician_name,
            "slug": slug,
            "ticker": ticker,
            "trade_type": trade_type,
            "trade_date": trade_date.strftime("%Y-%m-%d"),
            "report_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "amount_usd": amount_usd,
            "asset_type": asset_type,
            "trade_id": trade_id,
            "source": "capitoltrades",
        })

    return trades


# --------------------------------------------------------------------------- #
# Public interface
# --------------------------------------------------------------------------- #

def get_politician_trades(bio_id: str, days_back: int = 30,
                           name: str = "") -> list[dict]:
    """
    Fetch dan parse trades untuk satu politisi dari Capitol Trades.
    """
    url = f"{BASE_URL}/politicians/{bio_id}"
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    resp = _get(url)
    if not resp:
        logger.warning("Gagal fetch halaman untuk %s (%s)", name or bio_id, bio_id)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    pol_name = name or bio_id

    trades = _parse_trades_table(soup, pol_name, bio_id, cutoff)
    logger.info("[%s] %d trades ditemukan (cutoff: %s)",
                pol_name, len(trades), cutoff.strftime("%Y-%m-%d"))
    return trades


def get_all_recent_trades(politicians: list[dict], days_back: int = 30) -> list[dict]:
    """Fetch trades dari semua politisi yang di-track."""
    all_trades: list[dict] = []
    seen_ids: set[str] = set()

    for pol in politicians:
        bio_id = pol.get("bio_id") or pol.get("slug", "")
        name   = pol.get("name", bio_id)

        trades = get_politician_trades(bio_id, days_back=days_back, name=name)
        for t in trades:
            if t["trade_id"] not in seen_ids:
                seen_ids.add(t["trade_id"])
                all_trades.append(t)

    logger.info("Total trades unik dari semua politisi: %d", len(all_trades))
    return all_trades
