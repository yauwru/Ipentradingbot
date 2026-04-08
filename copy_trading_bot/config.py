import os
from dotenv import load_dotenv

load_dotenv()

# Alpaca
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Capitol Trades
CAPITOL_TRADES_BASE_URL = os.getenv("CAPITOL_TRADES_BASE_URL", "https://www.capitoltrades.com")
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "60"))

# Trading
POSITION_SIZE_USD = float(os.getenv("POSITION_SIZE_USD", "1000"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "20"))
COPY_OPTIONS = os.getenv("COPY_OPTIONS", "false").lower() == "true"
MIN_TRADE_AMOUNT = float(os.getenv("MIN_TRADE_AMOUNT", "1000"))

# Hanya eksekusi order untuk trades yang dilaporkan dalam N hari terakhir.
# Scraping tetap 180 hari (untuk mark-as-seen), tapi order hanya untuk yang baru.
TRADE_EXECUTION_DAYS = int(os.getenv("TRADE_EXECUTION_DAYS", "14"))

# Top politicians to track (slug from capitoltrades.com)
# These are politicians with historically strong trading records.
# The bot also discovers new top performers dynamically.
TOP_POLITICIANS = [
    "nancy-pelosi",
    "michael-mccaul",
    "josh-gottheimer",
    "dan-crenshaw",
    "tommy-tuberville",
    "brian-mast",
    "ro-khanna",
    "michael-waltz",
    "greg-gianforte",
    "virginia-foxx",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
