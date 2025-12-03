from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import requests
from dotenv import load_dotenv
import os

load_dotenv()

MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")
API_GATE_KEY = os.getenv("API_GATE_KEY")

# Massive screener URL: hard-coded to a known-good reference endpoint for now.
# This avoids any accidental overrides from environment variables or symbols.txt.
MASSIVE_SCREENER_URL = "https://api.massive.com/v3/snapshot/options/SPY"

app = FastAPI()

def generate_answer(prompt: str, max_tokens: int = 200, temperature: float = 0.7) -> str:
    """
    Lightweight answer generator that does NOT call a heavy model.

    It:
    - Detects if Massive screener data was included in the prompt.
    - If present, surfaces the top option signals explicitly in the answer.
    - Adds an educational explanation around those signals.
    """
    # Try to extract the original user question
    lines = prompt.splitlines()
    user_question = ""
    for line in lines:
        if line.lower().startswith("user question:"):
            user_question = line.split(":", 1)[-1].strip()
            break
    if not user_question:
        user_question = "the user's trading question"

    # Detect if screener data was included and, if so, grab that section
    has_screener = "Top option signals from Massive (pre-filtered):" in prompt

    screener_block = ""
    if has_screener:
        start = prompt.find("Top option signals from Massive (pre-filtered):")
        if start != -1:
            # Try to cut off at the instructions that follow the screener section
            end_marker = "\n\nUsing this data"
            end = prompt.find(end_marker, start)
            if end == -1:
                end = len(prompt)
            screener_block = prompt[start:end].strip()

    parts: list[str] = []

    if has_screener and screener_block:
        # Show the actual contracts first
        parts.append("Here are the current top option signals from your Massive screener:\n")
        parts.append(screener_block)
        parts.append(
            "\nNow let’s interpret these in a beginner‑friendly way:\n"
            "- Contracts with **higher volume and open interest (OI)** are where more traders are active.\n"
            "- Higher **premium (fmv)** usually means the market is pricing in more movement or risk.\n"
            "- If you see very high volume or OI at a specific strike or expiry, that level may be important for sentiment.\n"
        )
        parts.append(
            "When you look at each of the contracts above, ask yourself:\n"
            "1. Is this near‑term (0DTE or close) or further out in time?\n"
            "2. Is the strike price near the current underlying price, far out‑of‑the‑money, or deep in‑the‑money?\n"
            "3. Does the volume + OI suggest **new positions** building up, or just existing interest?\n"
        )
    else:
        parts.append(
            "I don’t have live screener data right now, so I’ll explain how to think "
            "about today’s option signals in general."
        )
        parts.append(
            "- Look for tickers with **high options volume and open interest**.\n"
            "- Scan for **spikes in implied volatility (IV)** – those often mark event risk or strong sentiment.\n"
            "- Pay attention to **large trades or blocks** on major indexes like SPY, QQQ, or single names.\n"
            "- Use 0DTE or short-dated options with extreme caution; small moves can wipe out the premium.\n"
        )

    parts.append(
        f"In simple terms for a beginner, the core of your question was:\n"
        f"**“{user_question}”**\n\n"
        "A good rule of thumb:\n"
        "- Use the screener to **find ideas**, not to blindly copy trades.\n"
        "- Write down *why* you like a setup (thesis), where you’d get out (stop), and what your max loss is.\n"
        "- Treat every trade as a small, repeatable decision—not an all-in bet.\n"
    )

    return "\n\n".join(parts)

class ChatRequest(BaseModel):
    prompt: str
    max_tokens: int = 200
    temperature: float = 0.7

class ChatResponse(BaseModel):
    answer: str

from typing import List, Dict, Tuple, Optional

# Load symbols list from symbols.txt
SYMBOLS_LIST: List[str] = []
try:
    with open("symbols.txt", "r") as f:
        SYMBOLS_LIST = [line.strip() for line in f if line.strip()]
except Exception:
    SYMBOLS_LIST = []

# Build Massive screener URL dynamically using symbols.txt
# NOTE: Disabled for now because the /v3/options/aggregates endpoint returns 404.
# Once you have a confirmed-working Massive options URL, you can re-enable this
# and point it to that path.
# if SYMBOLS_LIST:
#     symbols_str = ",".join(SYMBOLS_LIST)
#     MASSIVE_SCREENER_URL = f"https://api.massive.com/v3/options/aggregates?symbols={symbols_str}"

def get_screener_data() -> Tuple[Optional[Dict], Optional[str]]:
    """
    Fetch raw screener data from Massive using the SPY option chain snapshot.

    Returns:
        (data, error_message)
        - data: dict or list with the raw JSON, or None on error
        - error_message: description if something went wrong, else None
    """
    if not MASSIVE_SCREENER_URL:
        return None, "MASSIVE_SCREENER_URL is not set."
    if not MASSIVE_API_KEY:
        return None, "MASSIVE_API_KEY is not set."

    try:
        # For now, keep it simple: pull SPY calls with a reasonable limit.
        # You can later add filters like expiration_date=today for 0DTE.
        resp = requests.get(
            MASSIVE_SCREENER_URL,
            params={
                "apiKey": MASSIVE_API_KEY,
                "contract_type": "call",
                "limit": 100,
            },
            timeout=5,  # short but not too aggressive timeout
        )
        resp.raise_for_status()
        data = resp.json()
        # Ensure we always return a dict (with 'results') or None for downstream code
        if isinstance(data, (dict, list)):
            return data, None
        return None, "Unexpected screener JSON shape from Massive."
    except requests.exceptions.Timeout:
        # Treat timeouts as a soft failure: the chat will fall back to an educational answer
        return None, "Timed out trying to reach Massive screener (over 5 seconds)."
    except Exception as e:
        return None, f"Error calling Massive screener: {e}"


def select_top_signals(data, max_signals: int = 5) -> List[Dict]:
    """
    Given raw Massive option snapshot data, pick the most 'important' rows.

    This is tuned to the /v3/snapshot/options/{underlyingAsset} schema, where
    each row has nested fields like day, details, greeks, fmv, etc.
    """
    # Normalize to a list of dicts
    if isinstance(data, dict) and "results" in data:
        rows = data["results"]
    elif isinstance(data, list):
        rows = data
    else:
        return []

    rows = [r for r in rows if isinstance(r, dict)]

    scored: list[tuple[float, dict]] = []

    for row in rows:
        details = row.get("details") or {}
        day = row.get("day") or {}

        # Volume comes from the day sub-object
        volume_raw = day.get("volume", 0)
        # Implied volatility may or may not be present on the row
        iv_raw = row.get("implied_volatility", 0)
        # Use fair market value (fmv) as a proxy for option premium
        premium_raw = row.get("fmv", 0)
        # Open interest is another useful signal
        oi_raw = row.get("open_interest", 0)

        try:
            volume_val = float(volume_raw or 0.0)
        except (TypeError, ValueError):
            volume_val = 0.0

        try:
            iv_val = float(iv_raw or 0.0)
        except (TypeError, ValueError):
            iv_val = 0.0

        try:
            premium_val = float(premium_raw or 0.0)
        except (TypeError, ValueError):
            premium_val = 0.0

        try:
            oi_val = float(oi_raw or 0.0)
        except (TypeError, ValueError):
            oi_val = 0.0

        # Simple importance score: notional traded (volume * premium) plus a bump for OI
        notional_score = volume_val * premium_val
        score = notional_score + oi_val * 10.0 + iv_val  # IV adds a small extra weight
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


def build_prompt_with_screener(user_prompt: str) -> str:
    """
    Decide whether to call the Massive screener based on the user's question,
    and build the final prompt fragment for the model.

    Note: this returns the 'prompt' argument that will be passed into
    generate_answer(), which will still prepend its own system prefix.
    """
    lower_q = user_prompt.lower()

    wants_screener = any(
        kw in lower_q
        for kw in ["0dte", "0 dte", "screener", "covered call", "options screener"]
    )

    # If the user is not asking for screener-style behavior, just pass the prompt through.
    if not wants_screener:
        return user_prompt

    # Fetch raw screener data
    data, error = get_screener_data()
    screener_text = ""
    if data is not None:
        top_signals = select_top_signals(data)
        screener_text = format_signals_for_prompt(top_signals)
        if not screener_text:
            error = "No usable signals were found in the screener data."

    base = f"User question: {user_prompt}\n\n"

    if screener_text:
        # We have pre-filtered signals from Massive: ask the model to teach around them.
        return (
            base
            + "Please wait — analyzing today's options signals with extended processing time.\n\n"
            + "Here are today's notable option signals from Massive (already pre-filtered):\n"
            + screener_text
            + "\n\nUsing this data, do the following:\n"
              "1) Identify the 3–5 most important signals for TODAY (for example, unusual volume, extreme IV, big moves, or notable changes in options activity).\n"
              "2) Explain, in simple terms, what each of those signals might mean for a cautious trader.\n"
              "3) Focus on education: describe how a trader might THINK about these setups, including risk, reward, and key Greeks, but DO NOT tell them exactly what to buy or sell.\n"
              "4) Start with a short overview of the day, then give bullet points for each key signal.\n"
        )
    else:
        # Massive call failed or returned nothing useful: fall back to a purely educational answer.
        return (
            base
            + f"(Note: Screener data from Massive was unavailable or empty: {error})\n\n"
              "Without that data, teach the user how traders normally identify the most "
              "important options signals for the current day (for example, unusual volume, "
              "high implied volatility, big changes in open interest, or major moves in "
              "indexes like SPY and QQQ). Explain it in simple terms for a beginner and "
              "focus on risk and reward without giving specific trade recommendations.\n"
        )

@app.post("/chat", response_model=ChatResponse)
def chat_open(req: ChatRequest):
    """
    OPEN testing endpoint (no API key).
    Uses build_prompt_with_screener so 0DTE/screener-style questions
    automatically pull Massive data before calling the model.
    """
    try:
        full_prompt = build_prompt_with_screener(req.prompt)
        answer = generate_answer(full_prompt, req.max_tokens, req.temperature)
        return ChatResponse(answer=answer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation error: {e}")

@app.post("/api/chat", response_model=ChatResponse)
def chat_secure(req: ChatRequest, request: Request):
    """
    SECURE endpoint for Copilot / external callers.
    Requires x-api-key header matching API_GATE_KEY (if set).

    Uses build_prompt_with_screener so 0DTE/screener-style questions
    automatically pull Massive data before calling the model.
    """
    if API_GATE_KEY:
        provided = request.headers.get("x-api-key")
        if provided != API_GATE_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        full_prompt = build_prompt_with_screener(req.prompt)
        answer = generate_answer(full_prompt, req.max_tokens, req.temperature)
        return ChatResponse(answer=answer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation error: {e}")

@app.get("/debug/screener")
def debug_screener():
    """
    Debug endpoint to inspect Massive screener data and the selected top signals.
    This does NOT call the model, it just shows what the backend sees.
    """
    data, error = get_screener_data()
    if error:
        return {"error": error}

    top_signals = select_top_signals(data)
    formatted = format_signals_for_prompt(top_signals)

    # Try to compute how many raw rows we had
    if isinstance(data, dict) and "results" in data:
        raw_count = len(data["results"])
    elif isinstance(data, list):
        raw_count = len(data)
    else:
        raw_count = 0

    return {
        "raw_count": raw_count,
        "top_signals_count": len(top_signals),
        "top_signals": top_signals,
        "formatted_for_prompt": formatted,
    }