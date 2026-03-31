"""
Stock Signal Bot — Swing Trading Edition
- Scans core watchlist + S&P 500 once daily after market close
- Uses DAILY bars for cleaner signals and delayed data compatibility
- Entry: RSI < 35 + MACD crossover + volume spike + above 200MA + positive sentiment
- Exit: +7% profit target OR -3% stop loss (checked once per day)
- Alerts via Telegram
"""

import os
import time
import json
import logging
from datetime import datetime
import schedule

import yfinance as yf
import pandas as pd
import requests

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NEWS_API_KEY     = os.environ.get("NEWS_API_KEY", "")

PROFIT_TARGET    = float(os.environ.get("PROFIT_TARGET", "0.08"))   # 8%
STOP_LOSS        = float(os.environ.get("STOP_LOSS",     "0.025"))  # 2.5%

WATCHLIST_FILE   = "watchlist.json"

# ── In-memory trade tracker ───────────────────────────────────────────────────
open_alerts: dict[str, float] = {}   # { "AAPL": 182.50 }


# ─────────────────────────────────────────────────────────────────────────────
#  MESSAGING
# ─────────────────────────────────────────────────────────────────────────────

def notify(msg: str):
    """Send a Telegram message."""
    import traceback
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        resp.raise_for_status()
        log.info(f"Telegram sent: {msg[:80]}...")
    except Exception as e:
        log.error(f"Telegram failed [{type(e).__name__}]: {e}")
        log.error(traceback.format_exc())


# ─────────────────────────────────────────────────────────────────────────────
#  DATA
# ─────────────────────────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    try:
        with open(WATCHLIST_FILE) as f:
            return json.load(f).get("tickers", [])
    except FileNotFoundError:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]


def get_sp500_tickers() -> list[str]:
    return [
        "MMM","ABT","ABBV","ACN","ADBE","AMD","AFL","A","APD","ABNB","AKAM","ALB",
        "ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE","AAL","AEP",
        "AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","AON","APA","AAPL","AMAT",
        "APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK","AZO","AVB","AVY","AXON","BKR",
        "BAC","BK","BBWI","BAX","BDX","BBY","BIIB","BLK","BX","BA",
        "BSX","BMY","AVGO","BR","BRO","BLDR","BG","CDNS","CZR","CPT","CPB","COF",
        "CAH","KMX","CCL","CARR","CAT","CBOE","CBRE","CDW","CE","COR","CNC","CF",
        "SCHW","CHTR","CVX","CMG","CB","CHD","CI","CINF","CTAS","CSCO","C","CFG",
        "CLX","CME","CMS","KO","CTSH","CL","CMCSA","CAG","COP","ED","STZ","CEG","COO",
        "CPRT","GLW","CTVA","CSGP","COST","CTRA","CCI","CSX","CMI","CVS","DHI","DHR","DRI",
        "DVA","DE","DAL","DVN","DXCM","FANG","DLR","DG","DLTR","D","DPZ","DOV",
        "DOW","DTE","DUK","DD","EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR",
        "ENPH","ETR","EOG","EQT","EFX","EQIX","EQR","ESS","EL","ETSY","EG","EVRG","ES",
        "EXC","EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FITB","FSLR",
        "FE","FIS","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN","IT",
        "GE","GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC","GILD","GPN","GL","GS","HAL",
        "HIG","HAS","HCA","DOC","HSIC","HSY","HPE","HLT","HOLX","HD","HON","HRL","HST",
        "HWM","HPQ","HUBB","HUM","HBAN","HII","IBM","IEX","IDXX","ITW","ILMN","INCY","IR",
        "PODD","INTC","ICE","IFF","IP","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT",
        "JBL","JKHY","J","JNJ","JCI","JPM","KDP","KEY","KEYS","KMB","KIM",
        "KMI","KLAC","KHC","KR","LHX","LH","LRCX","LW","LVS","LDOS","LEN","LIN","LYV","LKQ",
        "LMT","L","LOW","LYB","MTB","MPC","MKTX","MAR","MLM","MAS","MA","MTCH",
        "MKC","MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP","MU","MSFT","MAA","MRNA",
        "MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS","MOS","MSI","MSCI","NDAQ","NTAP",
        "NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN","NSC","NTRS","NOC","NCLH","NRG",
        "NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC","ON","OKE","ORCL","OTIS","PCAR",
        "PKG","PANW","PH","PAYX","PAYC","PYPL","PNR","PEP","PFE","PCG","PM","PSX","PNW",
        "PNC","POOL","PPG","PPL","PFG","PG","PGR","PRU","PEG","PTC","PSA","PHM","QRVO",
        "PWR","QCOM","DGX","RL","RJF","RTX","O","REG","REGN","RF","RSG","RMD","RVTY","ROK",
        "ROL","ROP","ROST","RCL","SPGI","CRM","SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS",
        "SJM","SNA","SOLV","SO","LUV","SWK","SBUX","STT","STLD","STE","SYK","SYF","SNPS","SYY",
        "TMUS","TROW","TTWO","TPR","TRGP","TGT","TEL","TDY","TFX","TER","TSLA","TXN","TXT",
        "TMO","TJX","TSCO","TT","TDG","TRV","TRMB","TFC","TYL","TSN","USB","UDR","ULTA","UNP",
        "UAL","UPS","URI","UNH","UHS","VLO","VTR","VRSN","VRSK","VZ","VRTX","VICI",
        "V","VMC","WRB","WAB","WMT","WM","WAT","WEC","WFC","WELL","WST","WDC","WY",
        "WHR","WMB","WTW","GWW","WYNN","XEL","XYL","YUM","ZBRA","ZBH","ZTS"
    ]


def get_daily_bars(ticker: str, period: str = "1y") -> pd.DataFrame | None:
    """Fetch daily bars from Yahoo Finance."""
    try:
        bars = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
        if bars is None or bars.empty or len(bars) < 50:
            return None
        bars.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in bars.columns]
        return bars
    except Exception as e:
        log.warning(f"Bar fetch failed for {ticker}: {e}")
        return None


def get_sentiment(ticker: str) -> float:
    """Return sentiment score from NewsAPI. Returns 0 (neutral) if no key set."""
    if not NEWS_API_KEY:
        return 0.0
    try:
        url  = (
            f"https://newsapi.org/v2/everything"
            f"?q={ticker}&sortBy=publishedAt&pageSize=5&apiKey={NEWS_API_KEY}"
        )
        resp     = requests.get(url, timeout=5).json()
        articles = resp.get("articles", [])
        if not articles:
            return 0.0
        positive = {"surge","soar","beat","rally","gain","up","strong","buy","record","upgrade"}
        negative = {"fall","drop","miss","cut","down","weak","sell","loss","crash","downgrade"}
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
#  INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def calc_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def calc_rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series):
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal


def calc_ma(close: pd.Series, period: int) -> float:
    return float(close.rolling(period).mean().iloc[-1])


# ─────────────────────────────────────────────────────────────────────────────
#  SIGNAL LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def check_entry_signals(ticker: str) -> bool:
    """
    Swing trade entry — ALL must be true:
      1. 50MA > 200MA                   (golden cross — medium AND long term uptrend)
      2. Price is above the 200-day MA  (confirmed long-term uptrend)
      3. RSI(14) between 25-45          (meaningful pullback, not in freefall)
      4. RSI is rising vs yesterday     (momentum already turning up — not still falling)
      5. Today closed green (close > open) (selling pressure done, buyers stepping in)
      6. Volume today > 1.5x 20-day avg (real conviction behind the move)
      7. Sentiment >= 0                 (no negative news headwind)
    """
    bars = get_daily_bars(ticker)
    if bars is None or len(bars) < 210:
        return False

    close  = bars["close"]
    volume = bars["volume"]
    open_  = bars["open"]

    # 1 — 50MA above 200MA (golden cross — both trends pointing up)
    ma50  = calc_ma(close, 50)
    ma200 = calc_ma(close, 200)
    if ma50 <= ma200:
        return False

    # 2 — Price above 200MA
    if close.iloc[-1] <= ma200:
        return False

    # 3 — RSI in the sweet spot
    rsi_series = close.diff()
    gain = rsi_series.clip(lower=0).rolling(14).mean()
    loss = (-rsi_series.clip(upper=0)).rolling(14).mean()
    rs   = gain / loss.replace(0, 1e-10)
    rsi_full = 100 - (100 / (1 + rs))
    rsi_today      = float(rsi_full.iloc[-1])
    rsi_yesterday  = float(rsi_full.iloc[-2])
    if rsi_today > 45 or rsi_today < 25:
        return False

    # 4 — RSI rising (momentum turning up)
    if rsi_today <= rsi_yesterday:
        return False

    # 5 — Today closed green (close above open)
    if close.iloc[-1] <= open_.iloc[-1]:
        return False

    # 6 — Volume confirmation
    avg_vol = volume.iloc[-21:-1].mean()
    if volume.iloc[-1] < 1.5 * avg_vol:
        return False

    # 7 — Sentiment
    if get_sentiment(ticker) < 0:
        return False

    return True


def check_exit_signals(ticker: str, entry_price: float) -> str | None:
    """Check if open position has hit profit target or stop loss."""
    bars = get_daily_bars(ticker, period="5d")
    if bars is None or bars.empty:
        return None

    current_price = float(bars["close"].iloc[-1])
    pct_change    = (current_price - entry_price) / entry_price

    if pct_change >= PROFIT_TARGET:
        return f"🎯 PROFIT +{pct_change*100:.1f}% — current ${current_price:.2f}"
    if pct_change <= -STOP_LOSS:
        return f"🛑 STOP {pct_change*100:.1f}% — current ${current_price:.2f}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  SCAN
# ─────────────────────────────────────────────────────────────────────────────

def scan():
    """Scan — runs every 5 minutes during market hours."""
    now = datetime.now()
    log.info(f"Scan started at {now.strftime('%H:%M:%S')}")

    # Only run during market hours (9:35–15:55 ET)
    import pytz
    et  = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    mins = now_et.hour * 60 + now_et.minute
    if not (9 * 60 + 35 <= mins <= 15 * 60 + 55):
        log.info(f"Outside market hours ET ({now_et.strftime('%H:%M')}) — skipping.")
        return

    watchlist   = load_watchlist()
    sp500       = get_sp500_tickers()
    all_tickers = list(set(watchlist + sp500))
    log.info(f"Scanning {len(all_tickers)} tickers...")

    signals_found = 0

    # ── Exit checks first ─────────────────────────────────────────────────
    for ticker, entry_price in list(open_alerts.items()):
        result = check_exit_signals(ticker, entry_price)
        if result:
            msg = (
                f"EXIT ALERT: {ticker}\n"
                f"{result}\n"
                f"Entry was ${entry_price:.2f}"
            )
            notify(msg)
            log.info(f"Exit signal: {ticker}")
            del open_alerts[ticker]

    # ── Entry scan ────────────────────────────────────────────────────────
    for ticker in all_tickers:
        if ticker in open_alerts:
            continue

        try:
            if check_entry_signals(ticker):
                bars = get_daily_bars(ticker, period="5d")
                if bars is None:
                    continue
                price        = float(bars["close"].iloc[-1])
                target_price = round(price * (1 + PROFIT_TARGET), 2)
                stop_price   = round(price * (1 - STOP_LOSS), 2)

                msg = (
                    f"📈 SWING ENTRY: {ticker}\n"
                    f"Price:  ${price:.2f}\n"
                    f"Target: ${target_price} (+7%)\n"
                    f"Stop:   ${stop_price} (-3%)\n"
                    f"Hold:   2-5 days\n"
                    f"Signals: 50/200MA + RSI rising + Green candle + VOL"
                )
                notify(msg)
                log.info(f"Entry signal: {ticker} @ ${price:.2f}")
                open_alerts[ticker] = price
                signals_found += 1

        except Exception as e:
            log.warning(f"Error scanning {ticker}: {e}")

    log.info(f"Scan complete. {signals_found} new signals. Tracking {len(open_alerts)} open positions.")


# ─────────────────────────────────────────────────────────────────────────────
#  BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(ticker: str, period: str = "1y") -> str:
    """Run backtest on a ticker and return a summary string."""
    valid_periods = ["6mo", "1y", "2y"]
    if period not in valid_periods:
        period = "1y"

    bars = get_daily_bars(ticker.upper(), period=period)
    if bars is None or len(bars) < 50:
        return f"Could not fetch data for {ticker.upper()}. Check the ticker and try again."

    close  = bars["close"]
    volume = bars["volume"]
    n      = len(close)

    rsi             = calc_rsi_series(close)
    ma50            = close.rolling(50).mean()
    ma200           = close.rolling(min(200, n)).mean()
    vol20           = volume.rolling(20).mean().shift(1)

    PROFIT_PCT = float(os.environ.get("PROFIT_TARGET", "0.08"))
    STOP_PCT   = float(os.environ.get("STOP_LOSS", "0.025"))
    MAX_HOLD   = 10

    trades   = []
    in_trade = False
    entry_i  = None
    entry_px = None
    start_i  = min(205, n - 2)

    dates = bars.index
    open_ = bars["open"]

    for i in range(start_i, n):
        if not in_trade:
            ok_cross = ma50.iloc[i] > ma200.iloc[i]
            ok_200   = close.iloc[i] > ma200.iloc[i]
            ok_rsi   = 25 < float(rsi.iloc[i]) < 45
            ok_rsi_rising = float(rsi.iloc[i]) > float(rsi.iloc[i-1])
            ok_green = close.iloc[i] > open_.iloc[i]
            ok_vol   = vol20.iloc[i] and volume.iloc[i] > 1.5 * vol20.iloc[i]

            if ok_cross and ok_200 and ok_rsi and ok_rsi_rising and ok_green and ok_vol:
                in_trade = True
                entry_i  = i
                entry_px = float(close.iloc[i])
        else:
            pct  = (float(close.iloc[i]) - entry_px) / entry_px
            held = i - entry_i
            reason = None
            if pct >= PROFIT_PCT:  reason = "profit"
            elif pct <= -STOP_PCT: reason = "stop"
            elif held >= MAX_HOLD: reason = "timeout"
            if reason:
                trades.append({"pct": pct * 100, "held": held, "reason": reason,
                               "entry": dates[entry_i].strftime("%m/%d"), "exit": dates[i].strftime("%m/%d")})
                in_trade = False

    if in_trade:
        pct = (float(close.iloc[-1]) - entry_px) / entry_px
        trades.append({"pct": pct * 100, "held": n - 1 - entry_i, "reason": "open",
                       "entry": dates[entry_i].strftime("%m/%d"), "exit": "open"})

    if not trades:
        return f"No signals found for {ticker.upper()} over {period}. The setup may be too strict for this stock."

    closed   = [t for t in trades if t["reason"] != "open"]
    wins     = [t for t in closed if t["pct"] > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    avg_ret  = sum(t["pct"] for t in closed) / len(closed) if closed else 0
    equity   = 100.0
    for t in closed:
        equity *= (1 + t["pct"] / 100)

    lines = [f"📊 BACKTEST: {ticker.upper()} ({period})"]
    lines.append(f"Trades:    {len(trades)} ({len(closed)} closed)")
    lines.append(f"Win rate:  {win_rate:.1f}%")
    lines.append(f"Avg return: {'+' if avg_ret >= 0 else ''}{avg_ret:.2f}%")
    lines.append(f"Equity:    $100 → ${equity:.2f}")
    lines.append("")
    lines.append("Trade log:")
    for t in trades[-15:]:  # last 15 trades max to keep message short
        sign = "+" if t["pct"] >= 0 else ""
        lines.append(f"  {t['entry']}→{t['exit']}  {sign}{t['pct']:.1f}%  [{t['reason']}]")
    if len(trades) > 15:
        lines.append(f"  ... and {len(trades)-15} earlier trades")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  TELEGRAM COMMAND LISTENER
# ─────────────────────────────────────────────────────────────────────────────

last_update_id = 0

BATCH_50 = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","AVGO","JPM",
    "LLY","UNH","XOM","V","MA","HD","PG","COST","JNJ","ABBV",
    "MRK","WMT","BAC","NFLX","CRM","CVX","KO","PEP","TMO","ACN",
    "MCD","CSCO","ABT","LIN","DHR","TXN","NKE","PM","NEE","ADBE",
    "ORCL","IBM","RTX","HON","QCOM","GE","LOW","AMGN","INTU","CAT"
]

def run_batch_backtest(tickers: list, period: str = "1y") -> str:
    """Run backtest on multiple tickers and return ranked summary."""
    results = []
    for ticker in tickers:
        try:
            bars = get_daily_bars(ticker, period=period)
            if bars is None or len(bars) < 50:
                continue

            close  = bars["close"]
            volume = bars["volume"]
            open_  = bars["open"]
            n      = len(close)

            rsi_diff = close.diff()
            gain = rsi_diff.clip(lower=0).rolling(14).mean()
            loss = (-rsi_diff.clip(upper=0)).rolling(14).mean()
            rs   = gain / loss.replace(0, 1e-10)
            rsi_full = 100 - (100 / (1 + rs))

            ma50  = close.rolling(50).mean()
            ma200 = close.rolling(min(200, n)).mean()
            vol20 = volume.rolling(20).mean().shift(1)

            PROFIT_PCT = float(os.environ.get("PROFIT_TARGET", "0.08"))
            STOP_PCT   = float(os.environ.get("STOP_LOSS", "0.025"))
            MAX_HOLD   = 10

            trades = []
            in_trade = False
            entry_i = None
            entry_px = None
            start_i = min(205, n - 2)

            for i in range(start_i, n):
                if not in_trade:
                    ok_cross     = ma50.iloc[i] > ma200.iloc[i]
                    ok_200       = close.iloc[i] > ma200.iloc[i]
                    ok_rsi        = 25 < float(rsi_full.iloc[i]) < 50
                    ok_rsi_rising = float(rsi_full.iloc[i]) > float(rsi_full.iloc[i-1])
                    ok_green      = close.iloc[i] > open_.iloc[i]
                    ok_vol        = vol20.iloc[i] and volume.iloc[i] > 1.3 * vol20.iloc[i]

                    if ok_cross and ok_200 and ok_rsi and ok_rsi_rising and ok_green and ok_vol:
                        in_trade = True
                        entry_i  = i
                        entry_px = float(close.iloc[i])
                else:
                    pct  = (float(close.iloc[i]) - entry_px) / entry_px
                    held = i - entry_i
                    reason = None
                    if pct >= PROFIT_PCT:  reason = "profit"
                    elif pct <= -STOP_PCT: reason = "stop"
                    elif held >= MAX_HOLD: reason = "timeout"
                    if reason:
                        trades.append({"pct": pct * 100, "reason": reason})
                        in_trade = False

            closed = [t for t in trades if t["reason"] != "open"]
            if len(closed) < 1:
                continue

            wins     = [t for t in closed if t["pct"] > 0]
            win_rate = len(wins) / len(closed) * 100
            avg_ret  = sum(t["pct"] for t in closed) / len(closed)
            results.append((ticker, win_rate, avg_ret, len(closed)))

        except Exception as e:
            log.warning(f"Batch backtest failed for {ticker}: {e}")
            continue

    if not results:
        return "No results found. Try a longer period."

    results.sort(key=lambda x: x[1], reverse=True)

    lines = [f"BATCH BACKTEST ({period}) — top {len(results)} stocks"]
    lines.append("─" * 36)
    for ticker, wr, avg, n in results:
        sign = "+" if avg >= 0 else ""
        flag = " ✅" if wr >= 60 and avg > 0 else ""
        lines.append(f"{ticker:<6} {wr:.0f}%  {sign}{avg:.1f}% avg  {n}t{flag}")

    above60 = [r for r in results if r[1] >= 60 and r[2] > 0]
    lines.append("─" * 36)
    lines.append(f"{len(above60)}/{len(results)} stocks 60%+ win rate & positive avg")

    return "\n".join(lines)


def check_telegram_commands():
    """Poll Telegram for incoming messages and handle commands."""
    global last_update_id
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=5"
        resp = requests.get(url, timeout=10).json()
        updates = resp.get("result", [])
        for update in updates:
            last_update_id = update["update_id"]
            msg = update.get("message", {})
            text = msg.get("text", "").strip()

            if text.lower().startswith("/backtest"):
                parts = text.split()
                ticker = parts[1].upper() if len(parts) > 1 else None
                period = parts[2] if len(parts) > 2 else "1y"
                if not ticker:
                    notify("Usage: /backtest TICKER PERIOD\nExample: /backtest AAPL 1y\nPeriods: 6mo, 1y, 2y")
                else:
                    notify(f"Running backtest for {ticker} ({period})...")
                    result = run_backtest(ticker, period)
                    notify(result)

            elif text.lower().startswith("/batchtest"):
                parts = text.split()
                period = parts[1] if len(parts) > 1 and parts[1] in ["6mo","1y","2y"] else "1y"
                notify(f"Running batch backtest on 50 tickers ({period})... this takes 2-3 minutes.")
                result = run_batch_backtest(BATCH_50, period)
                notify(result)

            elif text.lower() == "/help":
                notify(
                    "Commands:\n"
                    "/backtest TICKER PERIOD — backtest one stock\n"
                    "/batchtest PERIOD — backtest top 50 stocks\n"
                    "Periods: 6mo, 1y, 2y\n"
                    "Example: /backtest NVDA 2y\n"
                    "Example: /batchtest 2y"
                )

    except Exception as e:
        log.warning(f"Telegram poll failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Stock Signal Bot (Swing Edition) started.")
    notify("✅ Stock Signal Bot is online — swing trading mode, scanning every 5 minutes during market hours.")

    # Scan every 5 minutes during market hours
    schedule.every(5).minutes.do(scan)

    # Run immediately on startup
    scan()

    while True:
        schedule.run_pending()
        check_telegram_commands()
        time.sleep(30)
