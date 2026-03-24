from typing import List, Dict, Tuple, Optional, Any
from fastapi import FastAPI, HTTPException
import requests
from dotenv import load_dotenv
import os


# Load .env from the same directory as this file (more reliable than relying on CWD)
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)


MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")

# Base Massive options chain snapshot endpoint
MASSIVE_SCREENER_URL = os.getenv("MASSIVE_SCREENER_URL") or "https://api.massive.com/v3/snapshot/options"

 # Contract snapshot endpoint (needed for get_option_contract_snapshot)
MASSIVE_CONTRACT_SNAPSHOT_URL = "https://api.massive.com/v3/snapshot/options"

# Massive indices snapshot endpoint
MASSIVE_INDICES_SNAPSHOT_URL = "https://api.massive.com/v3/snapshot/indices"

# Separate key for unified snapshot endpoint
UNIFIED_SNAP_API_KEY = os.getenv("UNIFIED_SNAP") or MASSIVE_API_KEY

# Separate key for contract snapshot endpoint (optional; falls back to MASSIVE_API_KEY)
OPTION_CONTRACT_API_KEY = os.getenv("OPTION_CONTRACT_API_KEY") or MASSIVE_API_KEY

# Reference / market status API keys (optional; fall back to MASSIVE_API_KEY)
EXCHANGES_API_KEY = os.getenv("EXCHANGES") or MASSIVE_API_KEY
CONDITION_CODES_API_KEY = os.getenv("CONDITION_CODES") or MASSIVE_API_KEY
MARKET_STATUS_API_KEY = os.getenv("MARKET_STATUS") or MASSIVE_API_KEY
MARKET_HOLIDAY_API_KEY = os.getenv("MARKET_HOLIDAY") or MASSIVE_API_KEY



# Massive trades endpoint (per-ticker trades)
# Docs: GET /v3/trades/{ticker}
MASSIVE_TRADES_URL = "https://api.massive.com/v3/trades"

# Massive reference / market status base URLs
MASSIVE_EXCHANGES_URL = "https://api.massive.com/v3/reference/exchanges"
MASSIVE_CONDITIONS_URL = "https://api.massive.com/v3/reference/conditions"
MASSIVE_MARKETSTATUS_NOW_URL = "https://api.massive.com/v1/marketstatus/now"
MASSIVE_MARKETSTATUS_UPCOMING_URL = "https://api.massive.com/v1/marketstatus/upcoming"
def get_trades_snapshot(
    ticker: str,
    order: str = "asc",
    limit: int = 10,
    sort: str = "timestamp",
) -> Tuple[Optional[Dict], Optional[str]]:
    """Fetch recent trades for a specific Massive ticker (e.g. option contract).

    Example ticker:
      O:TSLA210903C00700000
    """
    if not MASSIVE_TRADES_URL:
        return None, "MASSIVE_TRADES_URL is not set."
    if not MASSIVE_API_KEY:
        return None, "MASSIVE_API_KEY is not set."
    if not ticker:
        return None, "ticker is required (e.g. O:TSLA210903C00700000)."

    try:
        url = f"{MASSIVE_TRADES_URL}/{ticker}"
        resp = requests.get(
            url,
            params={
                "order": order,
                "limit": limit,
                "sort": sort,
                "apiKey": MASSIVE_API_KEY,
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, None
        return None, "Unexpected trades JSON shape from Massive."
    except requests.exceptions.Timeout:
        return None, "Timed out trying to reach Massive trades endpoint."
    except Exception as e:
        return None, f"Error calling Massive trades endpoint: {e}"


def clean_trades_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Massive trades response into a clean, stable structure.

    This endpoint is *read-only / data quality focused* — no screening.
    """
    results = payload.get("results") or []

    trades: List[Dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        trades.append(
            {
                "price": r.get("price"),
                "size": r.get("size"),
                "exchange": r.get("exchange"),
                "conditions": r.get("conditions"),
                "timestamp_ns": r.get("sip_timestamp") or r.get("timestamp"),
                "timeframe": r.get("timeframe"),
            }
        )

    return {
        "status": payload.get("status"),
        "request_id": payload.get("request_id"),
        "ticker": payload.get("ticker"),
        "count": len(trades),
        "trades": trades,
    }


app = FastAPI(title="Massive Options API")

@app.get("/")
def root():
    return {"status": "ok", "service": "Massive Options API"}

@app.get("/health")
def health():
    return {"status": "healthy"}

def _massive_get(url: str, params: Dict[str, Any], timeout_s: int = 12) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
    """Wrapper to call Massive and return (json, error)."""
    try:
        resp = requests.get(url, params=params, timeout=timeout_s)
        if resp.status_code >= 400:
            try:
                err_payload = resp.json()
            except Exception:
                err_payload = resp.text
            return None, {
                "message": "Massive request returned an error",
                "status_code": resp.status_code,
                "url": resp.url,
                "error": err_payload,
            }
        return resp.json(), None
    except requests.exceptions.Timeout:
        return None, "Timed out trying to reach Massive."
    except Exception as e:
        return None, f"Error calling Massive: {e}"


# --- Index normalization and snapshot helpers ---

def normalize_underlying_symbol(symbol: str) -> str:
    """Normalize user-friendly symbols to Massive-compatible underlyings.

    - SPX/NDX/RUT are cash indices. Massive uses the `I:` prefix for index tickers.
    - If the caller already passes `I:SPX`, keep it.
    """
    s = (symbol or "").strip().upper()
    if not s:
        return "SPY"

    # Allow explicit index tickers through
    if s.startswith("I:"):
        return s

    # Map common index inputs to Massive index tickers
    if s in {"SPX", "NDX", "RUT", "VIX"}:
        return f"I:{s}"

    return s


# --- Option chain symbol normalization ---
def option_chain_symbol(symbol: str) -> str:
    """Return the plain underlying to use for option-chain endpoints."""
    normalized = normalize_underlying_symbol(symbol)
    if normalized.startswith("I:"):
        return normalized.replace("I:", "", 1)
    return normalized


def get_index_snapshot(ticker: str) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
    """Fetch an index snapshot from Massive with unified fallbacks."""
    if not MASSIVE_API_KEY:
        return None, "MASSIVE_API_KEY is not set."

    t = (ticker or "").strip() or "I:SPX"
    requested = normalize_underlying_symbol(t)

    data, err = _massive_get(
        MASSIVE_INDICES_SNAPSHOT_URL,
        params={"ticker": requested, "apiKey": MASSIVE_API_KEY},
        timeout_s=8,
    )
    if not err and isinstance(data, dict):
        return data, None

    unified_data, unified_err = get_unified_snapshot(requested)
    if not unified_err and isinstance(unified_data, dict):
        return unified_data, None

    plain = requested.replace("I:", "", 1)
    unified_plain_data, unified_plain_err = get_unified_snapshot(plain)
    if not unified_plain_err and isinstance(unified_plain_data, dict):
        return unified_plain_data, None

    return None, {
        "message": "Unable to fetch index snapshot from Massive.",
        "requested_ticker": requested,
        "indices_error": err,
        "unified_error": unified_err,
        "unified_plain_error": unified_plain_err,
    }


# --- Helper: robust index field extraction from Massive indices snapshot ---
def _extract_index_fields(idx_data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[Dict[str, Any]]]:
    if not isinstance(idx_data, dict):
        return None, None, None

    r0 = None
    results = idx_data.get("results")
    if isinstance(results, list) and results:
        if isinstance(results[0], dict):
            r0 = results[0]
    elif isinstance(results, dict):
        r0 = results
    elif isinstance(idx_data.get("ticker"), dict):
        r0 = idx_data.get("ticker")
    else:
        r0 = idx_data

    if not isinstance(r0, dict):
        return None, None, None

    session = r0.get("session") if isinstance(r0.get("session"), dict) else {}
    day = r0.get("day") if isinstance(r0.get("day"), dict) else {}
    underlying_asset = r0.get("underlying_asset") if isinstance(r0.get("underlying_asset"), dict) else {}

    spot = (
        r0.get("value")
        or r0.get("price")
        or r0.get("last")
        or session.get("price")
        or session.get("value")
        or session.get("close")
        or session.get("last")
        or day.get("close")
        or day.get("last")
        or underlying_asset.get("price")
    )

    chg_pct = (
        r0.get("change_percent")
        or r0.get("percent_change")
        or r0.get("chg_percent")
        or session.get("change_percent")
        or day.get("change_percent")
    )

    try:
        spot_f = None if spot is None else float(spot)
    except (TypeError, ValueError):
        spot_f = None

    try:
        chg_pct_f = None if chg_pct is None else float(chg_pct)
    except (TypeError, ValueError):
        chg_pct_f = None

    return spot_f, chg_pct_f, r0

# Load symbols list from symbols.txt
try:
    symbols_path = os.path.join(os.path.dirname(__file__), "symbols.txt")
    with open(symbols_path, "r") as f:
        SYMBOLS_LIST = [line.strip() for line in f if line.strip()]
except Exception:
    SYMBOLS_LIST = []

def get_screener_data(
    symbol: str,
    contract_type: Optional[str] = None,          # call|put
    expiration_date: Optional[str] = None,        # YYYY-MM-DD (exact)
    expiration_gte: Optional[str] = None,         # YYYY-MM-DD
    expiration_lte: Optional[str] = None,         # YYYY-MM-DD
    strike_price: Optional[float] = None,
    strike_gte: Optional[float] = None,
    strike_lte: Optional[float] = None,
    order: str = "asc",
    limit: int = 250,
    sort: str = "expiration_date",
) -> Tuple[Optional[Dict], Optional[str]]:
    """Fetch raw option chain snapshot data from Massive for a single underlying symbol.

    Uses the options snapshot endpoint:
      GET /v3/snapshot/options/{UNDERLYING}

    Supports optional filtering (when supported by Massive):
      - contract_type (call/put)
      - expiration_date (exact) or expiration_date.gte / expiration_date.lte
      - strike_price (exact) or strike_price.gte / strike_price.lte

    NOTE: This function returns raw Massive JSON (dict). Screening/ranking is applied later.
    """
    if not MASSIVE_SCREENER_URL:
        return None, "MASSIVE_SCREENER_URL is not set."
    if not MASSIVE_API_KEY:
        return None, "MASSIVE_API_KEY is not set."

    # Default symbol safety net + normalize indices
    normalized_symbol = normalize_underlying_symbol(symbol)
    symbol = option_chain_symbol(symbol)

    # Normalize contract type
    if contract_type:
        ct = contract_type.lower().strip()
        if ct in ("c", "call"):
            contract_type = "call"
        elif ct in ("p", "put"):
            contract_type = "put"
        else:
            contract_type = None

    try:
        # Snapshot-only: chain snapshot endpoint
        url = f"{MASSIVE_SCREENER_URL}/{symbol}"
        _ = normalized_symbol

        params: Dict[str, Any] = {
            "apiKey": MASSIVE_API_KEY,
            "order": order,
            "limit": min(int(limit or 250), 250),
            "sort": sort or "expiration_date",
        }

        if contract_type:
            params["contract_type"] = contract_type

        if expiration_date:
            params["expiration_date"] = expiration_date
        if expiration_gte:
            params["expiration_date.gte"] = expiration_gte
        if expiration_lte:
            params["expiration_date.lte"] = expiration_lte

        if strike_price is not None:
            params["strike_price"] = strike_price
        if strike_gte is not None:
            params["strike_price.gte"] = strike_gte
        if strike_lte is not None:
            params["strike_price.lte"] = strike_lte

        data, data_err = _massive_get(url, params=params, timeout_s=12)
        if data_err:
            return None, data_err

        if isinstance(data, dict):
            return data, None
        return None, "Unexpected screener JSON shape from Massive."

    except requests.exceptions.Timeout:
        return None, "Timed out trying to reach Massive screener (over 12 seconds)."
    except Exception as e:
        return None, f"Error calling Massive screener: {e}"


def get_option_contract_snapshot(underlying: str, option_contract: str) -> Tuple[Optional[Dict], Optional[str]]:
    """Fetch a single option contract snapshot from Massive.

    Massive docs endpoint:
      GET /v3/snapshot/options/{underlyingAsset}/{optionContract}

    Example option_contract:
      O:AAPL230616C00150000
    """
    if not MASSIVE_CONTRACT_SNAPSHOT_URL:
        return None, "MASSIVE_CONTRACT_SNAPSHOT_URL is not set."
    if not OPTION_CONTRACT_API_KEY:
        return None, "OPTION_CONTRACT_API_KEY (or MASSIVE_API_KEY fallback) is not set."

    if not underlying:
        return None, "underlying is required (e.g. AAPL, SPY, SPX)."
    underlying = option_chain_symbol(underlying)
    if not option_contract:
        return None, "option_contract is required (e.g. O:AAPL230616C00150000)."

    try:
        url = f"{MASSIVE_CONTRACT_SNAPSHOT_URL}/{underlying}/{option_contract}"
        resp = requests.get(
            url,
            params={"apiKey": OPTION_CONTRACT_API_KEY},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, None
        return None, "Unexpected contract snapshot JSON shape from Massive."
    except requests.exceptions.Timeout:
        return None, "Timed out trying to reach Massive contract snapshot (over 8 seconds)."
    except Exception as e:
        return None, f"Error calling Massive contract snapshot: {e}"


def clean_contract_snapshot(payload: Dict) -> Dict:
    """Return a cleaner, stable shape for the option contract snapshot JSON."""
    results = payload.get("results") or {}

    details = results.get("details") or {}
    greeks = results.get("greeks") or {}
    day = results.get("day") or {}
    last_quote = results.get("last_quote") or {}
    last_trade = results.get("last_trade") or {}
    underlying_asset = results.get("underlying_asset") or {}

    # Prefer the options ticker embedded in details; fallback to user-provided fields
    opt_ticker = details.get("ticker") or results.get("ticker")
    underlying = underlying_asset.get("ticker") or details.get("underlying_ticker")

    out = {
        "status": payload.get("status"),
        "request_id": payload.get("request_id"),
        "results": {
            "contract": {
                "ticker": opt_ticker,
                "underlying": underlying,
                "type": details.get("contract_type"),
                "exercise_style": details.get("exercise_style"),
                "expiration_date": details.get("expiration_date"),
                "strike_price": details.get("strike_price"),
                "shares_per_contract": details.get("shares_per_contract"),
            },
            "underlying_asset": {
                "price": underlying_asset.get("price"),
                "change_to_break_even": underlying_asset.get("change_to_break_even"),
                "last_updated_ns": underlying_asset.get("last_updated"),
                "timeframe": underlying_asset.get("timeframe"),
            },
            "pricing": {
                "break_even_price": results.get("break_even_price"),
                "implied_volatility": results.get("implied_volatility"),
                "fmv": results.get("fmv"),
                "fmv_last_updated_ns": results.get("fmv_last_updated"),
            },
            "greeks": {
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
            },
            "liquidity": {
                "open_interest": results.get("open_interest"),
                "last_quote": {
                    "bid": last_quote.get("bid"),
                    "bid_size": last_quote.get("bid_size"),
                    "bid_exchange": last_quote.get("bid_exchange"),
                    "ask": last_quote.get("ask"),
                    "ask_size": last_quote.get("ask_size"),
                    "ask_exchange": last_quote.get("ask_exchange"),
                    "midpoint": last_quote.get("midpoint"),
                    "last_updated_ns": last_quote.get("last_updated"),
                    "timeframe": last_quote.get("timeframe"),
                },
            },
            "last_trade": {
                "price": last_trade.get("price"),
                "size": last_trade.get("size"),
                "exchange": last_trade.get("exchange"),
                "conditions": last_trade.get("conditions"),
                "sip_timestamp_ns": last_trade.get("sip_timestamp"),
                "timeframe": last_trade.get("timeframe"),
            },
            "day": {
                "open": day.get("open"),
                "high": day.get("high"),
                "low": day.get("low"),
                "close": day.get("close"),
                "change": day.get("change"),
                "change_percent": day.get("change_percent"),
                "previous_close": day.get("previous_close"),
                "volume": day.get("volume"),
                "vwap": day.get("vwap"),
                "last_updated_ns": day.get("last_updated"),
            },
        },
    }

    # Clean up None-heavy blocks a bit (optional; keep keys stable)
    return out


# --- Option snapshot row cleaning helpers ---

def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None

def passes_liquidity_filter(cleaned: Dict[str, Any]) -> bool:
    """Reject contracts that are too illiquid or expensive to trade realistically."""
    pricing = cleaned.get("pricing", {})
    liquidity = cleaned.get("liquidity", {})

    bid = pricing.get("bid")
    ask = pricing.get("ask")
    mid = pricing.get("mid")
    spread_to_mid = pricing.get("spread_to_mid")
    oi = liquidity.get("open_interest") or 0
    volume = liquidity.get("volume") or 0

    if bid is None or ask is None or mid is None:
        return False
    if ask < bid:
        return False
    if mid <= 0:
        return False
    if spread_to_mid is None or spread_to_mid > 0.15:
        return False
    if oi < 300:
        return False
    if volume < 50:
        return False

    return True


def build_underlying_context(symbol: str, resolved_symbol: Optional[str] = None) -> Dict[str, Any]:
    """Build a lightweight context payload for the underlying so scoring can use spot/change."""
    requested_symbol = (symbol or "").strip().upper() or "SPY"
    resolved = resolved_symbol or normalize_underlying_symbol(requested_symbol)

    spot = None
    change_percent = None
    source = None

    if resolved.startswith("I:"):
        idx_data, idx_err = get_index_snapshot(resolved)
        if not idx_err and isinstance(idx_data, dict):
            spot, change_percent, _ = _extract_index_fields(idx_data)
            source = "index_snapshot"
    else:
        unified_data, unified_err = get_unified_snapshot(resolved)
        if not unified_err and isinstance(unified_data, dict):
            spot, change_percent, _ = _extract_index_fields(unified_data)
            source = "unified_snapshot"

    return {
        "symbol": requested_symbol,
        "resolved_symbol": resolved,
        "spot": spot,
        "change_percent": change_percent,
        "source": source,
    }


def score_cleaned_contract(
    row: Dict[str, Any],
    option_side: str = "call",
    risk_profile: str = "balanced",
    underlying_context: Optional[Dict[str, Any]] = None,
) -> float:
    """Score a cleaned option contract using tradability first, then directional fit."""
    pricing = row.get("pricing", {})
    liquidity = row.get("liquidity", {})
    greeks = row.get("greeks", {})

    mid = pricing.get("mid") or 0.0
    spread_to_mid = pricing.get("spread_to_mid")
    iv = pricing.get("implied_volatility") or 0.0
    oi = liquidity.get("open_interest") or 0.0
    volume = liquidity.get("volume") or 0.0
    delta = greeks.get("delta")
    gamma = greeks.get("gamma") or 0.0

    if spread_to_mid is None:
        return -1e9
    if not passes_liquidity_filter(row):
        return -1e9

    side = (option_side or "call").lower()
    profile = (risk_profile or "balanced").lower()

    liquidity_score = min(float(volume) / 500.0, 1.0) * 40.0 + min(float(oi) / 2000.0, 1.0) * 30.0
    spread_score = max(0.0, 1.0 - (float(spread_to_mid) / 0.15)) * 20.0

    target_delta = 0.35 if side == "call" else -0.35
    delta_fit = 0.0
    if delta is not None:
        try:
            delta_fit = max(0.0, 1.0 - abs(float(delta) - target_delta) / 0.25)
        except (TypeError, ValueError):
            delta_fit = 0.0
    delta_score = delta_fit * 25.0

    activity_score = min((float(volume) * max(float(mid), 0.01)) / 5000.0, 1.0) * 15.0

    direction_score = 0.0
    if underlying_context:
        change_percent = underlying_context.get("change_percent")
        try:
            if change_percent is not None:
                cp = float(change_percent)
                if side == "call" and cp > 0:
                    direction_score = min(cp, 2.0) * 5.0
                elif side == "put" and cp < 0:
                    direction_score = min(abs(cp), 2.0) * 5.0
                else:
                    direction_score = -5.0
        except (TypeError, ValueError):
            direction_score = 0.0

    iv_penalty = 0.0
    if iv:
        try:
            iv_penalty = max(0.0, float(iv) - 1.0) * 5.0
        except (TypeError, ValueError):
            iv_penalty = 0.0

    gamma_bonus = min(float(gamma), 0.1) * 50.0 if profile == "aggressive" else 0.0

    profile_bonus = 0.0
    if profile == "conservative":
        profile_bonus -= max(0.0, abs((float(delta) if delta is not None else 0.0)) - 0.30) * 10.0
    elif profile == "aggressive":
        profile_bonus += gamma_bonus

    return liquidity_score + spread_score + delta_score + activity_score + direction_score + profile_bonus - iv_penalty


def assign_confidence(score: float) -> str:
    """Map numeric score to a simple confidence label."""
    if score >= 85:
        return "A"
    if score >= 65:
        return "B"
    if score >= 45:
        return "C"
    return "reject"

def clean_option_snapshot_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize a single option snapshot row to a clean, stable shape.

    Returns None if the row is unusable (missing core identifiers).
    This does NOT rank or "screen" – it only cleans and enriches.
    """
    details = row.get("details") or {}
    day = row.get("day") or {}
    greeks = row.get("greeks") or {}
    last_quote = row.get("last_quote") or row.get("quote") or {}

    opt_ticker = details.get("ticker") or row.get("ticker")
    if not opt_ticker:
        return None

    contract_type = details.get("contract_type") or details.get("option_type") or row.get("option_type")
    contract_type = str(contract_type or "").lower()
    if contract_type == "c":
        contract_type = "call"
    if contract_type == "p":
        contract_type = "put"

    strike = _safe_float(details.get("strike_price"))
    expiry = details.get("expiration_date")

    # Pricing / liquidity
    bid = _safe_float(last_quote.get("bid"))
    ask = _safe_float(last_quote.get("ask"))
    midpoint = _safe_float(last_quote.get("midpoint"))
    fmv = _safe_float(row.get("fmv"))

    # Compute a robust mid
    mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
    elif midpoint is not None and midpoint > 0:
        mid = midpoint
    elif fmv is not None and fmv > 0:
        mid = fmv

    spread = None
    spread_to_mid = None
    if bid is not None and ask is not None and ask >= bid:
        spread = ask - bid
        if mid and mid > 0:
            spread_to_mid = spread / mid

    # Metrics
    iv = _safe_float(row.get("implied_volatility"))
    oi = _safe_float(row.get("open_interest"))
    volume = _safe_float(day.get("volume"))

    delta = _safe_float(greeks.get("delta"))
    gamma = _safe_float(greeks.get("gamma"))
    theta = _safe_float(greeks.get("theta"))
    vega = _safe_float(greeks.get("vega"))

    # Quality flags (for debugging / UI)
    flags: List[str] = []
    if contract_type not in {"call", "put"}:
        flags.append("missing_contract_type")
    if strike is None:
        flags.append("missing_strike")
    if not expiry:
        flags.append("missing_expiration")
    if bid is None or ask is None:
        flags.append("missing_bid_ask")
    if bid is not None and ask is not None and ask < bid:
        flags.append("crossed_market")
    if mid is None:
        flags.append("missing_mid")
    if spread_to_mid is not None and spread_to_mid > 0.25:
        flags.append("wide_spread")
    if iv is None:
        flags.append("missing_iv")
    if delta is None:
        flags.append("missing_delta")

    cleaned = {
        "contract": {
            "ticker": opt_ticker,
            "type": contract_type or None,
            "expiration_date": expiry,
            "strike_price": strike,
        },
        "pricing": {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "spread_to_mid": spread_to_mid,
            "fmv": fmv,
            "implied_volatility": iv,
        },
        "liquidity": {
            "open_interest": oi,
            "volume": volume,
        },
        "greeks": {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        },
        "flags": flags,
    }

    return cleaned


def clean_option_snapshot_payload(data: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Clean an entire Massive option snapshot response.

    Returns (clean_rows, summary).
    """
    if isinstance(data, dict) and "results" in data:
        rows = data.get("results") or []
    elif isinstance(data, list):
        rows = data
    else:
        return [], {"error": "Unexpected snapshot JSON shape"}

    total = 0
    kept = 0
    dropped = 0
    flag_counts: Dict[str, int] = {}

    cleaned_rows: List[Dict[str, Any]] = []

    for r in rows:
        if not isinstance(r, dict):
            continue
        total += 1
        cleaned = clean_option_snapshot_row(r)
        if cleaned is None:
            dropped += 1
            continue

        kept += 1
        for f in cleaned.get("flags", []):
            flag_counts[f] = flag_counts.get(f, 0) + 1

        cleaned_rows.append(cleaned)

    summary = {
        "total_rows": total,
        "kept_rows": kept,
        "dropped_rows": dropped,
        "flag_counts": flag_counts,
    }

    return cleaned_rows, summary

def select_top_signals(
    data,
    max_signals: int = 5,
    option_side: str = "call",
    risk_profile: str = "balanced",
    underlying_context: Optional[Dict[str, Any]] = None,
) -> List[Dict]:
    """
    Clean, filter, score, and rank option contracts.

    This version prioritizes tradability and directional fit instead of simply
    rewarding raw IV or premium. It can return fewer than max_signals when the
    chain quality is weak.
    """
    cleaned_rows, _summary = clean_option_snapshot_payload(data)
    if not cleaned_rows:
        return []

    side = (option_side or "call").lower()
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for row in cleaned_rows:
        contract = row.get("contract", {})
        contract_type = (contract.get("type") or "").lower()
        if contract_type and contract_type != side:
            continue

        score = score_cleaned_contract(
            row,
            option_side=side,
            risk_profile=risk_profile,
            underlying_context=underlying_context,
        )
        confidence = assign_confidence(score)
        if confidence == "reject":
            continue

        enriched = {
            **row,
            "score": round(score, 2),
            "confidence": confidence,
            "underlying_context": underlying_context or {},
        }
        scored.append((score, enriched))

    if not scored:
        return []

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [row for _, row in scored[: max(0, int(max_signals or 0))]]
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
        contract = row.get("contract") or {}
        pricing = row.get("pricing") or {}
        liquidity = row.get("liquidity") or {}
        greeks = row.get("greeks") or {}

        symbol = contract.get("ticker") or details.get("ticker") or row.get("symbol") or row.get("ticker") or "N/A"
        expiry = contract.get("expiration_date") or details.get("expiration_date") or row.get("expiration_date") or "N/A"
        strike = contract.get("strike_price") or details.get("strike_price") or "N/A"

        volume = liquidity.get("volume") if liquidity else day.get("volume")
        iv = pricing.get("implied_volatility") if pricing else row.get("implied_volatility")
        delta = greeks.get("delta")
        premium = pricing.get("mid") if pricing else row.get("fmv")
        oi = liquidity.get("open_interest") if liquidity else row.get("open_interest")
        score = row.get("score")
        confidence = row.get("confidence")

        # Safe string conversions with N/A fallback
        def _fmt(v):
            return "N/A" if v is None else str(v)

        line = (
            f"- {symbol} | expiry={_fmt(expiry)}, strike={_fmt(strike)}, "
            f"volume={_fmt(volume)}, OI={_fmt(oi)}, IV={_fmt(iv)}, "
            f"delta={_fmt(delta)}, premium={_fmt(premium)}, "
            f"score={_fmt(score)}, confidence={_fmt(confidence)}"
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
    # NEW: expiration filtering (Weekly/Monthly support)
    expiration_date: Optional[str] = None,
    expiration_gte: Optional[str] = None,
    expiration_lte: Optional[str] = None,
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
    requested_symbol = symbol
    resolved_symbol = normalize_underlying_symbol(symbol)
    underlying_context = build_underlying_context(requested_symbol, resolved_symbol)

    data, error = get_screener_data(
        symbol=resolved_symbol,
        contract_type=option_side,
        expiration_date=expiration_date,
        expiration_gte=expiration_gte,
        expiration_lte=expiration_lte,
    )
    if error:
        raise HTTPException(status_code=502, detail=error)

    limit = top_n if top_n is not None else max_signals
    top_signals = select_top_signals(
        data,
        max_signals=limit,
        option_side=option_side,
        risk_profile=risk_profile,
        underlying_context=underlying_context,
    )

    if isinstance(data, dict) and "results" in data:
        raw_count = len(data["results"])
    elif isinstance(data, list):
        raw_count = len(data)
    else:
        raw_count = 0

    index_spot = None
    index_change_percent = None
    index_payload = None

    if resolved_symbol.startswith("I:"):
        idx_data, idx_err = get_index_snapshot(resolved_symbol)
        if not idx_err and isinstance(idx_data, dict):
            index_payload = idx_data
            index_spot, index_change_percent, _ = _extract_index_fields(idx_data)

        if index_spot is None:
            unified_data, unified_err = get_unified_snapshot(resolved_symbol)
            if not unified_err and isinstance(unified_data, dict):
                index_payload = unified_data
                index_spot, index_change_percent, _ = _extract_index_fields(unified_data)

        if index_spot is None:
            plain_symbol = resolved_symbol.replace("I:", "", 1)
            unified_plain_data, unified_plain_err = get_unified_snapshot(plain_symbol)
            if not unified_plain_err and isinstance(unified_plain_data, dict):
                index_payload = unified_plain_data
                index_spot, index_change_percent, _ = _extract_index_fields(unified_plain_data)

    return {
        "symbol": requested_symbol,
        "resolved_symbol": resolved_symbol,
        "underlying_context": underlying_context,
        "index_spot": index_spot,
        "index_change_percent": index_change_percent,
        "option_side": option_side,
        "risk_profile": risk_profile,
        "expiration_date": expiration_date,
        "expiration_gte": expiration_gte,
        "expiration_lte": expiration_lte,
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
    # NEW: expiration filtering (Weekly/Monthly support)
    expiration_date: Optional[str] = None,
    expiration_gte: Optional[str] = None,
    expiration_lte: Optional[str] = None,
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
    requested_symbol = symbol
    resolved_symbol = normalize_underlying_symbol(symbol)
    underlying_context = build_underlying_context(requested_symbol, resolved_symbol)

    data, error = get_screener_data(
        symbol=resolved_symbol,
        contract_type=option_side,
        expiration_date=expiration_date,
        expiration_gte=expiration_gte,
        expiration_lte=expiration_lte,
    )
    if error:
        return {"error": error}

    top_signals = select_top_signals(
        data,
        max_signals=top_n,
        option_side=option_side,
        risk_profile=risk_profile,
        underlying_context=underlying_context,
    )
    formatted = format_signals_for_prompt(top_signals)

    if isinstance(data, dict) and "results" in data:
        raw_count = len(data["results"])
    elif isinstance(data, list):
        raw_count = len(data)
    else:
        raw_count = 0

    index_spot = None
    index_change_percent = None
    index_payload = None

    if resolved_symbol.startswith("I:"):
        idx_data, idx_err = get_index_snapshot(resolved_symbol)
        if not idx_err and isinstance(idx_data, dict):
            index_payload = idx_data
            index_spot, index_change_percent, _ = _extract_index_fields(idx_data)

        if index_spot is None:
            unified_data, unified_err = get_unified_snapshot(resolved_symbol)
            if not unified_err and isinstance(unified_data, dict):
                index_payload = unified_data
                index_spot, index_change_percent, _ = _extract_index_fields(unified_data)

        if index_spot is None:
            plain_symbol = resolved_symbol.replace("I:", "", 1)
            unified_plain_data, unified_plain_err = get_unified_snapshot(plain_symbol)
            if not unified_plain_err and isinstance(unified_plain_data, dict):
                index_payload = unified_plain_data
                index_spot, index_change_percent, _ = _extract_index_fields(unified_plain_data)

    return {
        "symbol": requested_symbol,
        "resolved_symbol": resolved_symbol,
        "underlying_context": underlying_context,
        "index_spot": index_spot,
        "index_change_percent": index_change_percent,
        "option_side": option_side,
        "risk_profile": risk_profile,
        "expiration_date": expiration_date,
        "expiration_gte": expiration_gte,
        "expiration_lte": expiration_lte,
        "raw_count": raw_count,
        "top_signals_count": len(top_signals),
        "top_signals": top_signals,
        "formatted_for_prompt": formatted,
    }


# --- New endpoint: /indices ---

@app.get("/indices")
def indices(ticker: str = "I:SPX"):
    """Return a normalized index price payload for frontend consumption.

    This endpoint accepts both `SPX` and `I:SPX` (same for NDX/RUT).

    Response shape (stable):
      {
        "status": "OK",
        "symbol": "SPX",
        "resolved_symbol": "I:SPX",
        "price": 6085.45,
        "change_percent": 0.2,
        "raw": { ... optional ... }
      }

    Note: `raw` is included for debugging and can be removed later.
    """
    requested = (ticker or "I:SPX").strip().upper()
    resolved = normalize_underlying_symbol(requested)

    data, err = get_index_snapshot(resolved)
    if err:
        raise HTTPException(status_code=502, detail=err)

    price, change_percent, r0 = _extract_index_fields(data or {})

    return {
        "status": (data or {}).get("status") or "OK",
        "request_id": (data or {}).get("request_id"),
        "symbol": resolved.replace("I:", ""),
        "resolved_symbol": resolved,
        "price": price,
        "change_percent": change_percent,
        "source": "massive_indices",
        "raw": r0,
    }


# --- Indicator helpers ---


# --- Indicator helpers ---

# (other indicator helpers would be here)


# --- Reference/market status helpers ---

def _get_reference(
    url: str,
    api_key: str,
    params: Dict[str, Any],
) -> Tuple[Optional[Dict], Optional[str]]:
    if not url:
        return None, "Reference URL is not set."
    if not api_key:
        return None, "Reference API key is not set."
    try:
        resp = requests.get(
            url,
            params={**params, "apiKey": api_key},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, None
        return None, "Unexpected reference JSON shape from Massive."
    except requests.exceptions.Timeout:
        return None, "Timed out calling Massive reference endpoint."
    except Exception as e:
        return None, f"Error calling Massive reference endpoint: {e}"


def _clean_passthrough(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Light normalization for reference/market status endpoints."""
    return {
        "status": payload.get("status"),
        "request_id": payload.get("request_id"),
        "results": payload.get("results"),
        "count": len(payload.get("results") or []) if isinstance(payload.get("results"), list) else None,
    }

@app.get("/debug/clean_snapshot")
def debug_clean_snapshot(
    symbol: str = "SPY",
    limit: int = 50,
):
    """Return cleaned option snapshot rows for a symbol (no screening/ranking).

    This is intended to verify data quality and schema stability.
    """
    data, error = get_screener_data(symbol)
    if error:
        raise HTTPException(status_code=502, detail=error)

    cleaned_rows, summary = clean_option_snapshot_payload(data)

    # Return only the first N cleaned rows to keep payload small
    if limit and limit > 0:
        cleaned_rows = cleaned_rows[: min(limit, len(cleaned_rows))]

    return {
        "symbol": symbol,
        "summary": summary,
        "rows": cleaned_rows,
    }

@app.get("/debug/contract")
def debug_contract(
    underlying: str,
    option_contract: str,
    clean: bool = True,
):
    """Debug endpoint for a single option contract snapshot.

    Example:
      /debug/contract?underlying=AAPL&option_contract=O:AAPL230616C00150000

    Set clean=false to return raw Massive JSON.
    """
    data, error = get_option_contract_snapshot(underlying, option_contract)
    if error:
        raise HTTPException(status_code=502, detail=error)

    if not clean:
        return data

    return clean_contract_snapshot(data)


# --- New endpoint: /debug/trades ---

@app.get("/debug/trades")
def debug_trades(
    ticker: str,
    order: str = "asc",
    limit: int = 10,
    sort: str = "timestamp",
    clean: bool = True,
):
    """Debug endpoint for Massive trades by ticker.

    Example:
      /debug/trades?ticker=O:TSLA210903C00700000&limit=10

    Set clean=false to return raw Massive JSON.
    """
    data, error = get_trades_snapshot(ticker=ticker, order=order, limit=limit, sort=sort)
    if error:
        raise HTTPException(status_code=502, detail=error)

    if not clean:
        return data

    return clean_trades_snapshot(data)
#
# --- Unified snapshot endpoint function ---
def get_unified_snapshot(symbol: str) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Fetch unified snapshot data for a symbol using a separate API key if set.
    """
    if not UNIFIED_SNAP_API_KEY:
        return None, "UNIFIED_SNAP_API_KEY (or MASSIVE_API_KEY fallback) is not set."
    if not symbol:
        return None, "symbol is required."
    try:
        clean_symbol = (symbol or "").strip().upper()
        url = f"https://api.massive.com/v3/snapshot/unified/{clean_symbol}"
        resp = requests.get(
            url,
            params={
                "apiKey": UNIFIED_SNAP_API_KEY,
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data, None
        return None, "Unexpected unified snapshot JSON shape from Massive."
    except requests.exceptions.Timeout:
        return None, "Timed out trying to reach Massive unified snapshot endpoint."
    except Exception as e:
        return None, f"Error calling Massive unified snapshot endpoint: {e}"


# --- Debug endpoints for Massive reference/market status ---

@app.get("/debug/reference/exchanges")
def debug_exchanges(
    asset_class: str = "options",
    locale: str = "us",
    clean: bool = True,
):
    data, error = _get_reference(
        MASSIVE_EXCHANGES_URL,
        EXCHANGES_API_KEY,
        {
            "asset_class": asset_class,
            "locale": locale,
        },
    )
    if error:
        raise HTTPException(status_code=502, detail=error)
    return _clean_passthrough(data) if clean else data


@app.get("/debug/reference/conditions")
def debug_conditions(
    asset_class: str = "options",
    order: str = "asc",
    limit: int = 10,
    sort: str = "asset_class",
    clean: bool = True,
):
    data, error = _get_reference(
        MASSIVE_CONDITIONS_URL,
        CONDITION_CODES_API_KEY,
        {
            "asset_class": asset_class,
            "order": order,
            "limit": limit,
            "sort": sort,
        },
    )
    if error:
        raise HTTPException(status_code=502, detail=error)
    return _clean_passthrough(data) if clean else data


@app.get("/debug/marketstatus/now")
def debug_marketstatus_now(
    clean: bool = True,
):
    data, error = _get_reference(
        MASSIVE_MARKETSTATUS_NOW_URL,
        MARKET_STATUS_API_KEY,
        {},
    )
    if error:
        raise HTTPException(status_code=502, detail=error)
    return _clean_passthrough(data) if clean else data


@app.get("/debug/marketstatus/upcoming")
def debug_marketstatus_upcoming(
    clean: bool = True,
):
    data, error = _get_reference(
        MASSIVE_MARKETSTATUS_UPCOMING_URL,
        MARKET_HOLIDAY_API_KEY,
        {},
    )
    if error:
        raise HTTPException(status_code=502, detail=error)
    return _clean_passthrough(data) if clean else data