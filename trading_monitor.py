"""
Trading Monitor — FlowBot Imbalance su Variational
====================================================
Versione per GitHub Actions — viene eseguito una volta
ogni 15 minuti da GitHub, non gira in loop continuo.

Condizioni per segnale 5/5:
1. RSI tra 35 e 65
2. Volatilità tra 0.15% e 0.8%
3. Choppiness tra 38 e 62
4. Trend EMA20 vs EMA50 entro ±0.5%
5. Volume sopra la media delle ultime 20 candele
"""

import requests
import os
import json
from datetime import datetime

# ─── CONFIGURAZIONE ───────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = "581435460"

ASSETS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
}

CANDLE_INTERVAL = 15
CANDLE_LIMIT = 60

SLEEP_START_HOUR = 1
SLEEP_START_MIN  = 30
SLEEP_END_HOUR   = 7
SLEEP_END_MIN    = 45

STATE_FILE = "state.json"

# ─── TELEGRAM ─────────────────────────────────────────────────

def send_telegram(message: str):
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN non configurato!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"Errore Telegram: {r.text}")
    except Exception as e:
        print(f"Errore Telegram: {e}")

# ─── ORARI ────────────────────────────────────────────────────

def is_sleep_time():
    now = datetime.utcnow()
    rome_hour = (now.hour + 1) % 24
    rome_min  = now.minute
    current_minutes = rome_hour * 60 + rome_min
    sleep_start = SLEEP_START_HOUR * 60 + SLEEP_START_MIN
    sleep_end   = SLEEP_END_HOUR * 60 + SLEEP_END_MIN
    return sleep_start <= current_minutes < sleep_end

def get_rome_time():
    now = datetime.utcnow()
    rome_hour = (now.hour + 1) % 24
    return f"{rome_hour:02d}:{now.minute:02d}"

# ─── DATI DA KRAKEN ───────────────────────────────────────────

def get_candles(pair: str):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": CANDLE_INTERVAL}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("error"):
            print(f"Errore Kraken {pair}: {data['error']}")
            return None, None, None, None
        key = list(data["result"].keys())[0]
        candles = data["result"][key][-CANDLE_LIMIT:]
        closes  = [float(c[4]) for c in candles]
        highs   = [float(c[2]) for c in candles]
        lows    = [float(c[3]) for c in candles]
        volumes = [float(c[6]) for c in candles]
        if len(closes) < 20:
            return None, None, None, None
        return closes, highs, lows, volumes
    except Exception as e:
        print(f"Errore dati {pair}: {e}")
        return None, None, None, None

# ─── INDICATORI ───────────────────────────────────────────────

def calc_rsi(closes, period=14):
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 1)

def calc_ema(closes, period):
    ema = closes[0]
    k = 2 / (period + 1)
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_volatility(closes, highs, lows, period=10):
    ranges = [(highs[-i] - lows[-i]) / closes[-i] * 100 for i in range(1, period+1)]
    return round(sum(ranges) / len(ranges), 3)

def calc_choppiness(highs, lows, period=14):
    import math
    recent_h = highs[-period:]
    recent_l = lows[-period:]
    atr_sum = sum(recent_h[i] - recent_l[i] for i in range(period))
    price_range = max(recent_h) - min(recent_l)
    if price_range == 0:
        return 50.0
    return round(100 * math.log10(atr_sum / price_range) / math.log10(period), 1)

def calc_volume_above_avg(volumes, period=20):
    """
    Controlla se il volume dell'ultima candela è sopra
    la media delle ultime N candele (esclusa l'ultima).
    Restituisce True/False e il rapporto volume attuale / media.
    """
    avg = sum(volumes[-period-1:-1]) / period
    current = volumes[-1]
    ratio = round(current / avg, 2) if avg > 0 else 1.0
    return current > avg, ratio

# ─── LOGICA SEGNALI ───────────────────────────────────────────

def evaluate(name, closes, highs, lows, volumes):
    rsi   = calc_rsi(closes)
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    vol   = calc_volatility(closes, highs, lows)
    chop  = calc_choppiness(highs, lows)
    price = closes[-1]
    trend_pct = (ema20 - ema50) / ema50 * 100
    vol_above, vol_ratio = calc_volume_above_avg(volumes)

    rsi_ok   = 35 <= rsi <= 65
    vol_ok   = 0.15 <= vol <= 0.8
    chop_ok  = 38 <= chop <= 62
    trend_ok = abs(trend_pct) < 0.5
    volume_ok = vol_above

    score = sum([rsi_ok, vol_ok, chop_ok, trend_ok, volume_ok])

    if abs(trend_pct) < 0.2:
        trend_desc = "↔️ laterale"
    elif trend_pct > 0:
        trend_desc = f"📈 rialzista ({trend_pct:+.2f}%)"
    else:
        trend_desc = f"📉 ribassista ({trend_pct:+.2f}%)"

    vol_desc = f"{vol_ratio}x media ({'sopra' if vol_above else 'sotto'})"

    details = [
        f"{'✅' if rsi_ok else '❌'} RSI: {rsi}",
        f"{'✅' if vol_ok else '❌'} Volatilità: {vol}%",
        f"{'✅' if chop_ok else '❌'} Choppiness: {chop}",
        f"{'✅' if trend_ok else '❌'} Trend: {trend_desc}",
        f"{'✅' if volume_ok else '❌'} Volume: {vol_desc}",
    ]

    return {
        "name": name, "price": price,
        "score": score, "details": details,
        "perfect": score == 5,
    }

# ─── FORMATO MESSAGGIO ────────────────────────────────────────

def format_alert(results_5):
    time_str = get_rome_time()
    lines = [f"🟢 <b>SEGNALE — {time_str}</b>\n"]
    for r in results_5:
        lines.append(
            f"<b>{r['name']}</b> ${r['price']:,.1f} — Score 5/5\n"
            f"<b>✅ Avvia FlowBot Imbalance su Variational!</b>\n"
            + "\n".join(f"  {d}" for d in r["details"]) + "\n"
        )
    return "\n".join(lines)

# ─── STATO ────────────────────────────────────────────────────

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Errore salvataggio stato: {e}")

# ─── MAIN ─────────────────────────────────────────────────────

def main():
    if is_sleep_time():
        print(f"[{get_rome_time()}] Pausa notturna (01:30-07:45), skip")
        return

    state = load_state()
    results_5 = []

    for name, pair in ASSETS.items():
        closes, highs, lows, volumes = get_candles(pair)
        if closes is None:
            continue

        r = evaluate(name, closes, highs, lows, volumes)
        print(f"[{get_rome_time()}] {name}: score {r['score']}/5")

        was_perfect = state.get(name, False)

        if r["perfect"] and not was_perfect:
            results_5.append(r)
            state[name] = True
        elif not r["perfect"]:
            state[name] = False

    if results_5:
        send_telegram(format_alert(results_5))
        print(f"Notifica inviata per: {[r['name'] for r in results_5]}")
    else:
        print("Nessun segnale 5/5")

    save_state(state)


if __name__ == "__main__":
    main()
