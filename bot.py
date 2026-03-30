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

import yfinance as yf
import pandas as pd
import requests

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Config from environment ───────────────────────────────────────────────────
# No API key needed for yfinance

TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]

NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")  # optional but recommended

PROFIT_TARGET     = float(os.environ.get("PROFIT_TARGET", "0.05"))   # 5%
STOP_LOSS         = float(os.environ.get("STOP_LOSS",     "0.02"))   # 2%

# Tickers you always want watched (edit watchlist.json to change)
WATCHLIST_FILE    = "watchlist.json"

# ── Clients ───────────────────────────────────────────────────────────────────

# ── In-memory position tracker  (entry_price per ticker) ─────────────────────
open_alerts: dict[str, float] = {}   # { "AAPL": 182.50 }


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def sms(msg: str):
    """Send a Telegram message."""
    import traceback
    log.info("Sending Telegram message...")
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        resp.raise_for_status()
        log.info(f"Telegram sent OK: {msg[:60]}...")
    except Exception as e:
        log.error(f"Telegram failed [{type(e).__name__}]: {e}")
        log.error(traceback.format_exc())


def load_watchlist() -> list[str]:
    """Load core watchlist from JSON file."""
    try:
        with open(WATCHLIST_FILE) as f:
            return json.load(f).get("tickers", [])
    except FileNotFoundError:
        log.warning("watchlist.json not found — using defaults.")
        return ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]


def get_sp500_tickers() -> list[str]:
    """Return a hardcoded list of S&P 500 tickers."""
    return [
        "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM","ALB",
        "ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE","AAL","AEP",
        "AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","ANSS","AON","APA","AAPL","AMAT",
        "APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK","AZO","AVB","AVY","AXON","BKR",
        "BALL","BAC","BK","BBWI","BAX","BDX","BRK.B","BBY","BIO","TECH","BIIB","BLK","BX","BA",
        "BCX","BSX","BMY","AVGO","BR","BRO","BF.B","BLDR","BG","CDNS","CZR","CPT","CPB","COF",
        "CAH","KMX","CCL","CARR","CTLT","CAT","CBOE","CBRE","CDW","CE","COR","CNC","CNX","CDAY",
        "CF","CRL","SCHW","CHTR","CVX","CMG","CB","CHD","CI","CINF","CTAS","CSCO","C","CFG",
        "CLX","CME","CMS","KO","CTSH","CL","CMCSA","CMA","CAG","COP","ED","STZ","CEG","COO",
        "CPRT","GLW","CTVA","CSGP","COST","CTRA","CCI","CSX","CMI","CVS","DHI","DHR","DRI",
        "DVA","DE","DAL","XRAY","DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV",
        "DOW","DTE","DUK","DD","EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR",
        "ENPH","ETR","EOG","EPAM","EQT","EFX","EQIX","EQR","ESS","EL","ETSY","EG","EVRG","ES",
        "EXC","EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FITB","FSLR",
        "FE","FIS","FI","FLT","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN","IT",
        "GE","GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC","GILD","GPN","GL","GS","HAL",
        "HIG","HAS","HCA","DOC","HSIC","HSY","HES","HPE","HLT","HOLX","HD","HON","HRL","HST",
        "HWM","HPQ","HUBB","HUM","HBAN","HII","IBM","IEX","IDXX","ITW","ILMN","INCY","IR",
        "PODD","INTC","ICE","IFF","IP","IPG","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT",
        "JBL","JKHY","J","JNJ","JCI","JPM","JNPR","K","KVUE","KDP","KEY","KEYS","KMB","KIM",
        "KMI","KLAC","KHC","KR","LHX","LH","LRCX","LW","LVS","LDOS","LEN","LIN","LYV","LKQ",
        "LMT","L","LOW","LYB","MTB","MRO","MPC","MKTX","MAR","MMC","MLM","MAS","MA","MTCH",
        "MKC","MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA","MRNA",
        "MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS","MOS","MSI","MSCI","NDAQ","NTAP",
        "NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC","NTRS","NOC","NCLH","NRG",
        "NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL","OTIS","PCAR",
        "PKG","PANW","PARA","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE","PCG","PM","PSX","PNW",
        "PXD","PNC","POOL","PPG","PPL","PFG","PG","PGR","PRU","PEG","PTC","PSA","PHM","QRVO",
        "PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG","RMD","RVTY","ROK",
        "ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS",
        "SJM","SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SYF","SNPS","SYY",
        "TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY","TFX","TER","TSLA","TXN","TXT",
        "TMO","TJX","TSCO","TT","TDG","TRV","TRMB","TFC","TYL","TSN","USB","UDR","ULTA","UNP",
        "UAL","UPS","URI","UNH","UHS","VLO","VTR","VRSN","VRSK","VZ","VRTX","VFC","VTRS","VICI",
        "V","VMC","WRB","WAB","WMT","WBA","WM","WAT","WEC","WFC","WELL","WST","WDC","WRK","WY",
        "WHR","WMB","WTW","GWW","WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZTS"
    ]


def get_bars(ticker: str, limit: int = 60) -> pd.DataFrame | None:
    """Fetch recent 5-min bars from Yahoo Finance."""
    try:
        bars = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
        if bars is None or bars.empty:
            return None
        bars.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in bars.columns]
        return bars.tail(limit)
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

def calc_rsi(close: pd.Series, period: int = 14) -> float:
    """Calculate RSI manually."""
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    rsi   = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def calc_macd(close: pd.Series):
    """Calculate MACD line and signal line."""
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal


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
    if calc_rsi(close) > 40:
        return False

    # 2 — MACD crossover
    macd_line, signal_line = calc_macd(close)
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
