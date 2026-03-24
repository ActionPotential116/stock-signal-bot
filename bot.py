"""
Stock Signal Bot
- Scans core watchlist + S&P 500
- Entry: ALL signals must align (RSI, MACD, volume spike, breakout, sentiment)
- Exit: +5% profit target OR -2% stop loss
- Alerts via SMS (Twilio)
"""

import os
import time
import json
import logging
from datetime import datetime, timedelta
import schedule

import alpaca_trade_api as tradeapi
import pandas as pd
import pandas_ta as ta
import requests
from twilio.rest import Client

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Config from environment ───────────────────────────────────────────────────
ALPACA_API_KEY    = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]
ALPACA_BASE_URL   = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

TWILIO_SID        = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_TOKEN      = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM       = os.environ["TWILIO_FROM_NUMBER"]   # e.g. +12015551234
YOUR_PHONE        = os.environ["YOUR_PHONE_NUMBER"]    # e.g. +12055559876

NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")  # optional but recommended

PROFIT_TARGET     = float(os.environ.get("PROFIT_TARGET", "0.05"))   # 5%
STOP_LOSS         = float(os.environ.get("STOP_LOSS",     "0.02"))   # 2%

# Tickers you always want watched (edit watchlist.json to change)
WATCHLIST_FILE    = "watchlist.json"

# ── Clients ───────────────────────────────────────────────────────────────────
api    = tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)
twilio = Client(TWILIO_SID, TWILIO_TOKEN)

# ── In-memory position tracker  (entry_price per ticker) ─────────────────────
open_alerts: dict[str, float] = {}   # { "AAPL": 182.50 }


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def sms(msg: str):
    """Send an SMS alert."""
    try:
        twilio.messages.create(body=msg, from_=TWILIO_FROM, to=YOUR_PHONE)
        log.info(f"SMS sent: {msg[:60]}...")
    except Exception as e:
        log.error(f"SMS failed: {e}")


def load_watchlist() -> list[str]:
    """Load core watchlist from JSON file."""
    try:
        with open(WATCHLIST_FILE) as f:
            return json.load(f).get("tickers", [])
    except FileNotFoundError:
        log.warning("watchlist.json not found — using defaults.")
        return ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 tickers from Wikipedia."""
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        return tables[0]["Symbol"].tolist()
    except Exception as e:
        log.error(f"Could not fetch S&P 500 list: {e}")
        return []


def get_bars(ticker: str, limit: int = 60) -> pd.DataFrame | None:
    """Fetch recent 5-min bars from Alpaca."""
    try:
        bars = api.get_bars(
            ticker,
            tradeapi.rest.TimeFrame.Minute,
            limit=limit,
            adjustment="raw"
        ).df
        if bars.empty:
            return None
        bars = bars.tz_convert("US/Eastern")
        return bars
    except Exception as e:
        log.warning(f"Bar fetch failed for {ticker}: {e}")
        return None


def get_sentiment(ticker: str) -> float:
    """
    Return a simple sentiment score: positive > 0, negative < 0.
    Uses NewsAPI if key is set, otherwise returns neutral (0).
    """
    if not NEWS_API_KEY:
        return 0.0
    try:
        url = (
            f"https://newsapi.org/v2/everything"
            f"?q={ticker}&sortBy=publishedAt&pageSize=5"
            f"&apiKey={NEWS_API_KEY}"
        )
        resp = requests.get(url, timeout=5).json()
        articles = resp.get("articles", [])
        if not articles:
            return 0.0
        # Very lightweight: count positive vs negative headline words
        positive = {"surge", "soar", "beat", "rally", "gain", "up", "strong", "buy", "record"}
        negative = {"fall", "drop", "miss", "cut", "down", "weak", "sell", "loss", "crash"}
        score = 0
        for a in articles:
            headline = (a.get("title") or "").lower()
            score += sum(1 for w in positive if w in headline)
            score -= sum(1 for w in negative if w in headline)
        return score
    except Exception as e:
        log.warning(f"Sentiment fetch failed for {ticker}: {e}")
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def check_entry_signals(ticker: str) -> bool:
    """
    ALL of the following must be true for an entry alert:
      1. RSI(14) < 40  (oversold / building momentum)
      2. MACD line crossed above signal line in last 2 bars
      3. Volume on latest bar > 2x the 20-bar average
      4. Price broke above the 20-bar high (breakout)
      5. News sentiment >= 0 (neutral or positive)
    """
    bars = get_bars(ticker, limit=60)
    if bars is None or len(bars) < 30:
        return False

    close  = bars["close"]
    volume = bars["volume"]

    # 1 — RSI
    rsi = ta.rsi(close, length=14)
    if rsi is None or rsi.iloc[-1] > 40:
        return False

    # 2 — MACD crossover
    macd_df = ta.macd(close)
    if macd_df is None:
        return False
    macd_line   = macd_df["MACD_12_26_9"]
    signal_line = macd_df["MACDs_12_26_9"]
    # crossed above = macd > signal NOW and macd <= signal one bar ago
    if not (macd_line.iloc[-1] > signal_line.iloc[-1] and
            macd_line.iloc[-2] <= signal_line.iloc[-2]):
        return False

    # 3 — Volume spike (>2x 20-bar avg)
    avg_vol = volume.iloc[-21:-1].mean()
    if volume.iloc[-1] < 2 * avg_vol:
        return False

    # 4 — Price breakout above 20-bar high
    recent_high = close.iloc[-21:-1].max()
    if close.iloc[-1] <= recent_high:
        return False

    # 5 — Sentiment
    if get_sentiment(ticker) < 0:
        return False

    return True


def check_exit_signals(ticker: str, entry_price: float) -> str | None:
    """
    Returns 'PROFIT' or 'STOP' if exit condition is met, else None.
    """
    bars = get_bars(ticker, limit=5)
    if bars is None or bars.empty:
        return None

    current_price = bars["close"].iloc[-1]
    pct_change    = (current_price - entry_price) / entry_price

    if pct_change >= PROFIT_TARGET:
        return f"PROFIT  +{pct_change*100:.1f}% — current ${current_price:.2f}"
    if pct_change <= -STOP_LOSS:
        return f"STOP   {pct_change*100:.1f}% — current ${current_price:.2f}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  SCAN
# ─────────────────────────────────────────────────────────────────────────────

def scan():
    """Main scan loop: runs once per schedule tick during market hours."""
    now = datetime.now()
    log.info(f"Scan started at {now.strftime('%H:%M:%S')}")

    # Only trade during regular market hours (9:35–15:55 ET buffer)
    if not (9 * 60 + 35 <= now.hour * 60 + now.minute <= 15 * 60 + 55):
        log.info("Outside market hours — skipping.")
        return

    watchlist = load_watchlist()
    sp500     = get_sp500_tickers()
    all_tickers = list(set(watchlist + sp500))
    log.info(f"Scanning {len(all_tickers)} tickers...")

    # ── Exit checks first ──────────────────────────────────────────────────
    for ticker, entry_price in list(open_alerts.items()):
        result = check_exit_signals(ticker, entry_price)
        if result:
            msg = f"🚨 EXIT ALERT: {ticker}\n{result}\nEntry was ${entry_price:.2f}"
            sms(msg)
            log.info(msg)
            del open_alerts[ticker]

    # ── Entry scan ─────────────────────────────────────────────────────────
    for ticker in all_tickers:
        if ticker in open_alerts:
            continue   # already tracking this one

        try:
            if check_entry_signals(ticker):
                bars = get_bars(ticker, limit=5)
                if bars is None:
                    continue
                price = bars["close"].iloc[-1]
                target_price = round(price * (1 + PROFIT_TARGET), 2)
                stop_price   = round(price * (1 - STOP_LOSS),    2)

                msg = (
                    f"📈 ENTRY SIGNAL: {ticker}\n"
                    f"Current: ${price:.2f}\n"
                    f"Target:  ${target_price} (+5%)\n"
                    f"Stop:    ${stop_price} (-2%)\n"
                    f"Signals: RSI+MACD+VOL+BREAKOUT+NEWS ✅"
                )
                sms(msg)
                log.info(f"Entry signal: {ticker} @ ${price:.2f}")
                open_alerts[ticker] = price
        except Exception as e:
            log.warning(f"Error scanning {ticker}: {e}")

    log.info(f"Scan complete. Tracking {len(open_alerts)} open alerts.")


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Stock Signal Bot started.")
    sms("✅ Stock Signal Bot is online and scanning.")

    # Scan every 5 minutes
    schedule.every(5).minutes.do(scan)

    # Run once immediately on startup
    scan()

    while True:
        schedule.run_pending()
        time.sleep(30)
