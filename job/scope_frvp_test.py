"""Read-only SCOPE FRVP comparison using Angel One 5-minute candles."""
import datetime as dt
import math
import os
import sys
import time

import numpy as np
import pandas as pd

TICK = 0.05
ROWS = 24
VA_PCT = 70.0
TV_REFERENCE = {"VAH": 443.30, "POC": 441.65, "VAL": 438.75}


def pine_tick_string(value, tick=TICK):
    """Approximate Pine str.tostring(value, format.mintick) tie-up behavior."""
    snapped = math.floor(float(value) / tick + 0.5) * tick
    decimals = max(0, len(f"{tick:.10f}".rstrip("0").split(".")[-1]))
    return f"{snapped:.{decimals}f}"


def scope_profile(df, tick=TICK, requested_rows=ROWS, va_pct=VA_PCT):
    """Reproduce SCOPE Pine row sizing, overlap weighting, POC and VA expansion."""
    if df is None or len(df) < 4:
        raise ValueError(f"Need at least 4 candles; received {0 if df is None else len(df)}")

    highs = df["h"].astype(float).to_numpy()
    lows = df["l"].astype(float).to_numpy()
    volumes = df["v"].astype(float).to_numpy()
    range_high, range_low = float(np.max(highs)), float(np.min(lows))
    if not range_high > range_low:
        raise ValueError(f"Invalid profile range: high={range_high}, low={range_low}")

    # Pine: totalTicks = round((range high - range low) / minimum tick)
    total_ticks = max(1, int(math.floor((range_high - range_low) / tick + 0.5)))
    raw_ticks_per_row = total_ticks / float(requested_rows)
    ticks_down = max(1, int(math.floor(raw_ticks_per_row)))
    ticks_up = max(1, int(math.ceil(raw_ticks_per_row)))
    rows_down = int(math.ceil(total_ticks / float(ticks_down)))
    rows_up = int(math.ceil(total_ticks / float(ticks_up)))
    ticks_per_row = ticks_down if abs(rows_down-requested_rows) <= abs(rows_up-requested_rows) else ticks_up

    row_low, row_high = [], []
    price, ticks_left = range_low, total_ticks
    while ticks_left > 0:
        width_ticks = min(ticks_per_row, ticks_left)
        row_low.append(price)
        price += width_ticks * tick
        row_high.append(price)
        ticks_left -= width_ticks
    row_low = np.asarray(row_low, dtype=float)
    row_high = np.asarray(row_high, dtype=float)
    row_volume = np.zeros(len(row_low), dtype=float)

    # Pine allocates candle volume by the fraction of its high-low range overlapping each row.
    for candle_high, candle_low, candle_volume in zip(highs, lows, volumes):
        candle_range = candle_high - candle_low
        for row in range(len(row_low)):
            overlap = min(candle_high, row_high[row]) - max(candle_low, row_low[row])
            if overlap > 0:
                row_volume[row] += candle_volume * (overlap / candle_range if candle_range > 0 else 1.0)

    max_volume = float(np.max(row_volume))
    poc_index = int(np.argmax(row_volume))  # first maximum, like array.indexof
    poc_price = float((row_low[poc_index] + row_high[poc_index]) * 0.5)

    target_volume = float(np.sum(row_volume)) * va_pct / 100.0
    accumulated = max_volume
    low_index = high_index = poc_index
    # Match Pine expansion: pick the larger adjacent row; stop if next row would overshoot target.
    for _ in range(len(row_low)):
        if accumulated >= target_volume:
            break
        up_volume = float(row_volume[high_index + 1]) if high_index < len(row_low)-1 else -1.0
        down_volume = float(row_volume[low_index - 1]) if low_index > 0 else -1.0
        if up_volume < 0 and down_volume < 0:
            break
        if up_volume < 0:
            move_up = False
        elif down_volume < 0:
            move_up = True
        elif up_volume != down_volume:
            move_up = up_volume > down_volume
        else:
            move_up = (high_index + 1 - poc_index) <= (poc_index - (low_index - 1))
        next_volume = up_volume if move_up else down_volume
        if accumulated + next_volume > target_volume:
            break
        if move_up:
            high_index += 1
            accumulated += up_volume
        else:
            low_index -= 1
            accumulated += down_volume

    return {
        "VAH": float(row_high[high_index]),
        "POC": poc_price,
        "VAL": float(row_low[low_index]),
        "SessionHigh": range_high,
        "SessionLow": range_low,
        "Candles": int(len(df)),
        "TotalInputVolume": float(np.sum(volumes)),
        "ProfileVolume": float(np.sum(row_volume)),
        "RequestedRows": int(requested_rows),
        "ActualRows": int(len(row_low)),
        "TicksPerRow": int(ticks_per_row),
        "TotalTicks": int(total_ticks),
    }


def fetch_irctc(profile_date):
    """Use the site's existing Angel One login and NSE script-master helpers."""
    # This import reads credentials from environment variables via the existing login helper.
    from scope_daily import load_universe, login

    stocks, _indices = load_universe()
    instrument = next((x for x in stocks if x.get("symbol") == "IRCTC" or x.get("tsym") == "IRCTC-EQ"), None)
    if instrument is None:
        raise RuntimeError("IRCTC-EQ not found in Angel One's current NSE symbol master")
    api = login()
    request = {
        "exchange": "NSE",
        "symboltoken": str(instrument["token"]),
        "interval": "FIVE_MINUTE",
        "fromdate": f"{profile_date.isoformat()} 09:15",
        "todate": f"{profile_date.isoformat()} 15:30",
    }
    response = None
    for attempt in range(1, 5):
        try:
            response = api.getCandleData(request)
            if (response or {}).get("data"):
                break
            print(f"Angel One returned no data (attempt {attempt}/4)")
        except Exception as exc:
            print(f"Angel One request error (attempt {attempt}/4): {type(exc).__name__}: {str(exc)[:120]}")
        if attempt < 4:
            time.sleep(2 * attempt)
    candles = (response or {}).get("data") or []
    if not candles:
        raise RuntimeError(f"Angel One returned no IRCTC candles for {profile_date}")

    frame = pd.DataFrame(candles, columns=["ts", "o", "h", "l", "c", "v"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=False).dt.tz_localize(None)
    for column in ["o", "h", "l", "c", "v"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["ts", "o", "h", "l", "c", "v"])
    frame = frame[
        (frame["ts"].dt.date == profile_date)
        & (frame["ts"].dt.time >= dt.time(9, 15))
        & (frame["ts"].dt.time <= dt.time(15, 25))
    ].sort_values("ts").drop_duplicates("ts").reset_index(drop=True)

    # Normalize floating-point representation noise to NSE cash tick increments.
    # Volume is kept exactly as returned by Angel One; no hand-entered volume override.
    for column in ["o", "h", "l", "c"]:
        frame[column] = (np.round(frame[column].astype(float) / TICK) * TICK).round(2)
    return frame, instrument


def main():
    profile_date = dt.date.fromisoformat(os.environ.get("PROFILE_DATE", "2026-10-23"))
    print("=== SCOPE FRVP / ANGEL ONE TEST (READ ONLY) ===")
    print(f"Symbol=IRCTC-EQ | Date={profile_date} | Interval=FIVE_MINUTE | Rows={ROWS} | VA={VA_PCT:.0f}% | Tick={TICK}")
    candles, instrument = fetch_irctc(profile_date)
    if candles.empty:
        raise RuntimeError("No valid IRCTC candles remained after session filtering")

    print(f"Instrument: {instrument.get('tsym', instrument.get('symbol', 'IRCTC-EQ'))}")
    print(f"Candles={len(candles)} (expected 75 from 09:15 through 15:25)")
    print(f"First={candles['ts'].iloc[0]} | Last={candles['ts'].iloc[-1]}")
    print(f"SessionHigh={candles['h'].max():.2f} | SessionLow={candles['l'].min():.2f}")
    print(f"SessionOpen={candles['o'].iloc[0]:.2f} | SessionClose={candles['c'].iloc[-1]:.2f}")
    print(f"TotalInputVolume={candles['v'].sum():.0f}")
    opening = candles[candles["ts"].dt.time == dt.time(9, 15)]
    print("09:15 volume=" + (f"{opening['v'].iloc[0]:.0f}" if len(opening) else "MISSING"))

    expected = {
        dt.datetime.combine(profile_date, dt.time(9, 15)) + dt.timedelta(minutes=5 * i)
        for i in range(75)
    }
    missing = sorted(expected - set(candles["ts"].to_list()))
    print(f"Missing 5-minute timestamps={len(missing)}")
    if missing:
        print("Missing: " + ", ".join(t.strftime("%H:%M") for t in missing))

    result = scope_profile(candles)
    print("\n=== SCOPE-PINE ALGORITHM RESULT ===")
    visible = {}
    for key in ("VAH", "POC", "VAL"):
        visible[key] = pine_tick_string(result[key])
        print(f"{key}={visible[key]} (raw={result[key]:.10f})")
    print(f"ActualRows={result['ActualRows']} | TicksPerRow={result['TicksPerRow']} | ProfileVolume={result['ProfileVolume']:.0f}")

    print("\n=== COMPARISON TO TRADINGVIEW VALUES PROVIDED ===")
    matches = True
    for key in ("VAH", "POC", "VAL"):
        reference = f"{TV_REFERENCE[key]:.2f}"
        match = visible[key] == reference
        matches = matches and match
        print(f"{key}: TradingView={reference} | Angel One/Python={visible[key]} | {'MATCH' if match else 'DIFFERENT'}")
    print(f"All 3 displayed levels match: {matches}")
    print("No candle or volume values were manually overridden. No trading orders or site JSON were changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"TEST FAILED: {type(exc).__name__}: {str(exc)[:250]}")
        sys.exit(1)
