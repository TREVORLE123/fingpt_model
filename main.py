from fastapi import FastAPI, HTTPException
import requests
from dotenv import load_dotenv
import os

load_dotenv()

MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")

# Base Massive screener URL for options snapshots (no ticker hard-coded).
MASSIVE_SCREENER_URL = "https://api.massive.com/v3/snapshot/options"

app = FastAPI()

from typing import List, Dict, Tuple, Optional

# Load symbols list from symbols.txt
SYMBOLS_LIST: List[str] = []
try:
    with open("symbols.txt", "r") as f:
        SYMBOLS_LIST = [line.strip() for line in f if line.strip()]
except Exception:
    SYMBOLS_LIST = []

def get_screener_data(symbol: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Fetch raw screener data from Massive for a single underlying symbol
    using the options snapshot endpoint.
    """
    if not MASSIVE_SCREENER_URL:
        return None, "MASSIVE_SCREENER_URL is not set."
    if not MASSIVE_API_KEY:
        return None, "MASSIVE_API_KEY is not set."

    # Default symbol safety net
    if not symbol:
        symbol = "SPY"

    try:
        # Massive options snapshot endpoint pattern:
        # https://api.massive.com/v3/snapshot/options/{UNDERLYING}?order=asc&amp;limit=10&amp;sort=ticker&amp;apiKey=...
        url = f"{MASSIVE_SCREENER_URL}/{symbol}"

        resp = requests.get(
            url,
            params={
                "apiKey": MASSIVE_API_KEY,
                "order": "asc",
                "limit": 200,   # you can tune this down if needed
                "sort": "ticker",
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        # Ensure we always return a dict or list for downstream code
        if isinstance(data, (dict, list)):
            return data, None
        return None, "Unexpected screener JSON shape from Massive."
    except requests.exceptions.Timeout:
        return None, "Timed out trying to reach Massive screener (over 8 seconds)."
    except Exception as e:
        return None, f"Error calling Massive screener: {e}"


def select_top_signals(
    data,
    max_signals: int = 5,
    option_side: str = "call",
    risk_profile: str = "balanced",
) -> List[Dict]:
    """
    Given raw Massive option snapshot data, pick the most 'important' rows.

    This is tuned to the /v3/snapshot/options/{underlyingAsset} schema, where
    each row has nested fields like day, details, greeks, fmv, etc.

    option_side: "call" or "put"
    risk_profile: "conservative", "balanced", or "aggressive"
    """
    # Normalize to a list of dicts
    if isinstance(data, dict) and "results" in data:
        rows = data["results"]
    elif isinstance(data, list):
        rows = data
    else:
        return []

    rows = [r for r in rows if isinstance(r, dict)]

    option_side_norm = (option_side or "call").lower()
    risk_profile_norm = (risk_profile or "balanced").lower()

    def as_float(val) -> float:
        try:
            return float(val or 0.0)
        except (TypeError, ValueError):
            return 0.0

    scored: list[tuple[float, dict]] = []

    for row in rows:
        details = row.get("details") or {}
        day = row.get("day") or {}
        greeks = row.get("greeks") or {}

        # Determine option type from various possible fields
        opt_type_raw = (
            row.get("option_type")
            or details.get("option_type")
            or details.get("contract_type")
            or row.get("type")
        )
        opt_type = str(opt_type_raw or "").lower()
        if opt_type == "c":
            opt_type = "call"
        if opt_type == "p":
            opt_type = "put"

        # If we know the type and it doesn't match the requested side, skip
        if opt_type:
            if opt_type != option_side_norm:
                continue

        volume_val = as_float(day.get("volume"))
        iv_val = as_float(row.get("implied_volatility"))
        premium_val = as_float(row.get("fmv"))
        oi_val = as_float(row.get("open_interest"))

        bid_val = as_float(row.get("bid") or details.get("bid"))
        ask_val = as_float(row.get("ask") or details.get("ask"))
        mid_val = premium_val
        if bid_val > 0 and ask_val > 0:
            mid_val = (bid_val + ask_val) / 2.0

        spread_to_mid = 0.0
        if mid_val > 0 and bid_val > 0 and ask_val > 0:
            spread_to_mid = abs(ask_val - bid_val) / mid_val

        delta_val = as_float(greeks.get("delta"))
        gamma_val = as_float(greeks.get("gamma"))

        # Base signal strength: liquidity + activity
        notional_score = volume_val * mid_val
        base_score = notional_score + oi_val * 10.0

        # Adjust score by risk profile
        if risk_profile_norm == "conservative":
            # Prefer lower delta (around 0.2), tight spreads, solid OI
            score = (
                base_score
                + oi_val * 10.0
                - abs(abs(delta_val) - 0.2) * 500.0
                - spread_to_mid * 1000.0
            )
        elif risk_profile_norm == "aggressive":
            # Prefer higher delta and higher IV (more explosive trades)
            score = (
                base_score
                + abs(delta_val) * 2000.0
                + iv_val * 10.0
                + gamma_val * 100.0
            )
        else:
            # Balanced: mix of delta, IV, and spread quality
            score = (
                base_score
                + abs(delta_val) * 1000.0
                + iv_val * 5.0
                - spread_to_mid * 300.0
            )

        scored.append((score, row))

    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [row for _, row in scored[:max_signals]]
    return top


def format_signals_for_prompt(signals: List[Dict]) -> str:
    """
    Turn the selected top option signals into a compact text block for the LLM.

    This is tuned to the Massive option chain snapshot schema, using nested
    fields from details, day, greeks, and top-level values like fmv.
    """
    if not signals:
        return ""

    lines = []
    for row in signals:
        details = row.get("details") or {}
        day = row.get("day") or {}
        greeks = row.get("greeks") or {}

        symbol = details.get("ticker") or row.get("symbol") or row.get("ticker") or "N/A"
        expiry = details.get("expiration_date") or row.get("expiration_date") or "N/A"
        strike = details.get("strike_price") or "N/A"

        volume = day.get("volume")
        iv = row.get("implied_volatility")
        delta = greeks.get("delta")
        premium = row.get("fmv")
        oi = row.get("open_interest")

        # Safe string conversions with N/A fallback
        def _fmt(v):
            return "N/A" if v is None else str(v)

        line = (
            f"- {symbol} | expiry={_fmt(expiry)}, strike={_fmt(strike)}, "
            f"volume={_fmt(volume)}, OI={_fmt(oi)}, IV={_fmt(iv)}, "
            f"delta={_fmt(delta)}, premium={_fmt(premium)}"
        )
        lines.append(line)

    header = "Top option signals from Massive (pre-filtered):\n"
    return header + "\n".join(lines)

@app.get("/screener")
def screener(
    symbol: str = "SPY",
    option_side: str = "call",
    risk_profile: str = "balanced",
    max_signals: int = 5,
    top_n: Optional[int] = None,
):
    """
    Core screener endpoint.

    - Calls Massive via get_screener_data(symbol)
    - Selects the top option signals
    - Returns structured data only (no AI insights)

    The `symbol` query parameter should match the user's selected ticker
    (e.g. SPY, QQQ, IWM, SPX).

    option_side: "call" or "put"
    risk_profile: "conservative", "balanced", or "aggressive"
    top_n: optional override for how many signals to return
    """
    data, error = get_screener_data(symbol)
    if error:
        raise HTTPException(status_code=502, detail=error)

    limit = top_n if top_n is not None else max_signals
    top_signals = select_top_signals(
        data,
        max_signals=limit,
        option_side=option_side,
        risk_profile=risk_profile,
    )

    if isinstance(data, dict) and "results" in data:
        raw_count = len(data["results"])
    elif isinstance(data, list):
        raw_count = len(data)
    else:
        raw_count = 0

    return {
        "symbol": symbol,
        "option_side": option_side,
        "risk_profile": risk_profile,
        "raw_count": raw_count,
        "top_signals_count": len(top_signals),
        "top_signals": top_signals,
    }

@app.get("/debug/screener")
def debug_screener(
    symbol: str = "SPY",
    option_side: str = "call",
    risk_profile: str = "balanced",
    top_n: int = 5,
):
    """
    Debug endpoint to inspect Massive screener data and the selected top signals.
    This does NOT call any model or generate insights; it just shows what the backend sees.

    You can pass ?symbol=QQQ, ?symbol=IWM, etc. to see different underlyings.
    You can also tweak:
    - option_side=call|put
    - risk_profile=conservative|balanced|aggressive
    - top_n=number of signals to return
    """
    data, error = get_screener_data(symbol)
    if error:
        return {"error": error}

    top_signals = select_top_signals(
        data,
        max_signals=top_n,
        option_side=option_side,
        risk_profile=risk_profile,
    )
    formatted = format_signals_for_prompt(top_signals)

    if isinstance(data, dict) and "results" in data:
        raw_count = len(data["results"])
    elif isinstance(data, list):
        raw_count = len(data)
    else:
        raw_count = 0

    return {
        "symbol": symbol,
        "option_side": option_side,
        "risk_profile": risk_profile,
        "raw_count": raw_count,
        "top_signals_count": len(top_signals),
        "top_signals": top_signals,
        "formatted_for_prompt": formatted,
    }