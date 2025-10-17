# backend/main.py
from __future__ import annotations

import os
import time
import logging
import asyncio
from typing import Optional, Dict, Any, List

import requests
import bcrypt
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


# ----------------------------
# Setup / Config
# ----------------------------
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "finance")
FMP_API_KEY = os.getenv("FMP_API_KEY")  # required for FMP
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")  # optional
FROM_EMAIL = os.getenv("FROM_EMAIL")  # optional

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is not set")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backend")

log.info("✅ Connected to Mongo — databases: %s", client.list_database_names())

# Collections
users_col = db["users"]
holdings_col = db["holdings"]
realized_col = db["realized_gains"]
prices_col = db["prices"]  # { symbol, price: float, timestamp: epoch_seconds }

# Indexes
holdings_col.create_index([("symbol", ASCENDING), ("user_id", ASCENDING)], unique=True)
prices_col.create_index([("symbol", ASCENDING)], unique=True)


# ----------------------------
# Models
# ----------------------------
class Holding(BaseModel):
    symbol: str
    qty: float
    price: float
    user_id: str  # Mongo ObjectId string


class SellRequest(BaseModel):
    qty: float
    price: Optional[float] = None
    user_id: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    identifier: str  # username OR email
    password: str


# ----------------------------
# Utilities
# ----------------------------
def send_welcome_email(to_email: str, username: str) -> None:
    if not SENDGRID_API_KEY or not FROM_EMAIL:
        log.info("ℹ️ SENDGRID_API_KEY or FROM_EMAIL not set; skipping welcome email.")
        return
    try:
        message = Mail(
            from_email=FROM_EMAIL,
            to_emails=to_email,
            subject="🎉 Welcome to Your Personal Finance Dashboard!",
            html_content=f"""
                <h2>Hi {username},</h2>
                <p>Thanks for creating an account with <b>Personal Finance Dashboard</b>!</p>
                <p>You can now log in to track your portfolio and manage your investments.</p>
                <p>Stay smart with your finances,<br>— The Dashboard Team</p>
            """,
        )
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        resp = sg.send(message)
        log.info("✅ Welcome email sent to %s (status %s)", to_email, resp.status_code)
    except Exception as e:
        log.info("❌ Failed to send SendGrid email: %s", e)


def _store_price(symbol: str, price: float) -> None:
    """Upsert price into Mongo cache with current timestamp."""
    prices_col.update_one(
        {"symbol": symbol.upper()},
        {"$set": {"price": float(price), "timestamp": time.time()}},
        upsert=True,
    )


def _read_price(symbol: str) -> Optional[Dict[str, Any]]:
    """Read a cached price doc for symbol."""
    return prices_col.find_one({"symbol": symbol.upper()})


def _fmp_quote(symbol: str) -> Optional[float]:
    """Try Financial Modeling Prep (FMP) quote-short endpoint (free-tier)."""
    if not FMP_API_KEY:
        return None
    url = f"https://financialmodelingprep.com/api/v3/quote-short/{symbol.upper()}"
    params = {"apikey": FMP_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 403:
            log.info("🚫 FMP 403 for %s — endpoint restricted or free-tier limit.", symbol)
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            p = data[0].get("price")
            if p is not None:
                return float(p)
        return None
    except Exception as e:
        log.info("❌ FMP error for %s: %s", symbol, e)
        return None


def _yahoo_quote(symbol: str) -> Optional[float]:
    """Fallback to yfinance with resilient structure handling."""
    try:
        t = yf.Ticker(symbol.upper())
        price = None

        # Try fast_info
        try:
            fast = getattr(t, "fast_info", None)
            if fast:
                if isinstance(fast, dict):
                    price = fast.get("last_price")
                else:
                    price = getattr(fast, "last_price", None)
        except Exception:
            pass

        # Try info field
        if price is None:
            info = getattr(t, "info", {})
            price = info.get("regularMarketPrice")

        # Fallback to last closing price
        if price is None:
            hist = t.history(period="1d")
            if not hist.empty:
                price = hist["Close"].iloc[-1]

        # As a last resort, scrape Yahoo Finance
        if price is None:
            yurl = f"https://finance.yahoo.com/quote/{symbol.upper()}"
            html = requests.get(yurl, timeout=8).text
            import re
            match = re.search(r'"regularMarketPrice":\{"raw":([\d\.]+),', html)
            if match:
                price = float(match.group(1))

        return float(price) if price else None

    except Exception as e:
        log.info("❌ yfinance error for %s: %s", symbol, e)
        return None



def fetch_live_price(symbol: str) -> Optional[float]:
    """Get a live price from FMP, fallback to Yahoo. Return None if both fail."""
    # Try FMP first (respects your free key + rate limits)
    price = _fmp_quote(symbol)
    if price is not None:
        return price
    # Fallback to Yahoo/yfinance
    return _yahoo_quote(symbol)


def get_cached_price(symbol: str, max_age_sec: int = 3600, allow_fetch: bool = True) -> Optional[float]:
    """
    Return a cached price if not older than max_age_sec.
    If stale or missing and allow_fetch=True, fetch & store it.
    """
    symbol = symbol.upper()
    doc = _read_price(symbol)
    now = time.time()
    if doc and (now - float(doc.get("timestamp", 0)) < max_age_sec):
        return float(doc["price"])

    if allow_fetch:
        price = fetch_live_price(symbol)
        if price is not None:
            _store_price(symbol, price)
            return float(price)
    return float(doc["price"]) if doc and "price" in doc else None


def refresh_all_prices(rate_limit_sec: float = 2.0) -> None:
    """
    Rate-limited refresh of *all distinct symbols* from holdings into the prices cache.
    Designed for startup and hourly background refreshes.
    """
    symbols = holdings_col.distinct("symbol")
    if not symbols:
        log.info("ℹ️ No holdings found to refresh prices for.")
        return

    log.info("🌐 Refreshing prices for %d symbols...", len(symbols))
    for i, sym in enumerate(symbols, start=1):
        sym = sym.upper()
        price = fetch_live_price(sym)
        if price is not None:
            _store_price(sym, price)
            log.info("  %2d) ✅ %s @ %.4f", i, sym, price)
        else:
            log.info("  %2d) ⚠️ %s — no price fetched", i, sym)
        # Respect free-tier throttling
        time.sleep(rate_limit_sec)
    log.info("✅ Price refresh complete.")


# ----------------------------
# FastAPI
# ----------------------------
app = FastAPI(title="Personal Finance Dashboard API")

# CORS so Streamlit can call FastAPI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten if you prefer
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    """Preload prices once and start a periodic hourly refresher."""
    # Preload once (blocking) so UI has values on first load
    try:
        refresh_all_prices(rate_limit_sec=2.0)
    except Exception as e:
        log.info("❌ Startup refresh failed: %s", e)

    # Start hourly background refresher
    async def refresher():
        while True:
            try:
                # run the blocking refresh in a thread so we don't block the event loop
                await asyncio.to_thread(refresh_all_prices, 2.0)
            except Exception as e:
                log.info("❌ Hourly refresh failed: %s", e)
            await asyncio.sleep(3600)

    asyncio.create_task(refresher())


# ----------------------------
# Health
# ----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


# ----------------------------
# Auth
# ----------------------------
@app.post("/register")
def register_user(req: RegisterRequest, background_tasks: BackgroundTasks):
    if users_col.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already registered")

    def _create():
        hashed_pw = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt())
        users_col.insert_one({
            "username": req.username,
            "email": req.email,
            "password": hashed_pw,
            "admin": False
        })
        send_welcome_email(req.email, req.username)

    background_tasks.add_task(_create)
    return {"message": "Account creation queued; you’ll get an email shortly."}


@app.post("/login")
def login_user(req: LoginRequest):
    user = users_col.find_one({
        "$or": [{"email": req.identifier}, {"username": req.identifier}]
    })
    if not user or not bcrypt.checkpw(req.password.encode("utf-8"), user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "user_id": str(user["_id"]),
        "username": user["username"],
        "admin": user["admin"]
    }


# ----------------------------
# Holdings
# ----------------------------
@app.post("/holding")
def add_holding(req: Holding, background_tasks: BackgroundTasks):
    def insert_or_update():
        user_id = ObjectId(req.user_id)
        sym = req.symbol.upper()

        # Immediately warm the symbol price so the UI can show it after add
        p = get_cached_price(sym, max_age_sec=3600, allow_fetch=True)
        if p is not None:
            log.info("💰 Cached %s @ %.4f on insert", sym, p)

        existing = holdings_col.find_one({"symbol": sym, "user_id": user_id})
        if existing:
            new_qty = float(existing["qty"]) + float(req.qty)
            new_price = (
                (float(existing["price"]) * float(existing["qty"])) + (float(req.price) * float(req.qty))
            ) / new_qty
            holdings_col.update_one(
                {"_id": existing["_id"]},
                {"$set": {"qty": new_qty, "price": new_price}},
            )
        else:
            holdings_col.insert_one({
                "symbol": sym,
                "qty": float(req.qty),
                "price": float(req.price),
                "user_id": user_id
            })
        log.info("✅ Holding %s saved for %s", sym, user_id)

    background_tasks.add_task(insert_or_update)
    return {"status": "success", "message": f"Holding {req.symbol.upper()} queued for saving"}


@app.delete("/holding/{symbol}")
def delete_holding(symbol: str, background_tasks: BackgroundTasks, user_id: str = Query(...)):
    sym = symbol.upper()

    def perform_delete():
        result = holdings_col.delete_one({"symbol": sym, "user_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            log.info("⚠️ Holding %s not found for %s", sym, user_id)
        else:
            log.info("✅ Holding %s deleted for %s", sym, user_id)

    background_tasks.add_task(perform_delete)
    return {"status": "queued", "message": f"Holding {sym} deletion queued"}


@app.post("/holding/{symbol}/sell")
def sell_holding(symbol: str, request: SellRequest, background_tasks: BackgroundTasks):
    def process_sale():
        user_id = ObjectId(request.user_id)
        sym = symbol.upper()
        h = holdings_col.find_one({"symbol": sym, "user_id": user_id})
        if not h:
            log.info("❌ Holding %s not found for %s", sym, user_id)
            return
        qty_to_sell = float(request.qty)
        current_qty = float(h["qty"])
        if qty_to_sell <= 0 or qty_to_sell > current_qty:
            log.info("❌ Invalid quantity")
            return

        buy_price = float(h["price"])
        if request.price is not None:
            sell_price = float(request.price)
        else:
            # Try cache first; if stale, allow fetch so we don't block on rate limits too often
            sell_price = get_cached_price(sym, max_age_sec=300, allow_fetch=True) or buy_price

        profit = (sell_price - buy_price) * qty_to_sell
        realized_col.insert_one({
            "symbol": sym,
            "qty": qty_to_sell,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit": profit,
            "user_id": user_id
        })

        new_qty = current_qty - qty_to_sell
        if new_qty <= 0:
            holdings_col.delete_one({"_id": h["_id"]})
        else:
            holdings_col.update_one({"_id": h["_id"]}, {"$set": {"qty": new_qty}})

        log.info("✅ Sold %s — profit %.4f", sym, profit)

    background_tasks.add_task(process_sale)
    return {"status": "queued", "message": f"Sale of {symbol.upper()} queued for processing."}


# ----------------------------
# Portfolio
# ----------------------------
@app.get("/portfolio")
def get_portfolio(user_id: str = Query(...)):
    # Validate user
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    user = users_col.find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Read holdings for this user
    holdings = list(holdings_col.find({"user_id": oid, "qty": {"$gt": 0}}))

    # Gather prices from cache; don't hammer providers in the request path
    symbols = [h["symbol"] for h in holdings]
    prices: Dict[str, Optional[float]] = {}
    for sym in symbols:
        # use cached if fresh; do not force fetch here (avoid rate-limit bursts)
        prices[sym] = get_cached_price(sym, max_age_sec=3600, allow_fetch=False)

    results: List[Dict[str, Any]] = []
    for h in holdings:
        symbol = h["symbol"]
        qty = float(h["qty"])
        avg_price = float(h["price"])
        current_price = prices.get(symbol)

        row: Dict[str, Any] = {
            "symbol": symbol,
            "qty": qty,
            "avg_price": avg_price,
        }
        if current_price is not None:
            row.update({
                "current_price": round(float(current_price), 2),
                "value": round(float(current_price) * qty, 2),
                "unrealized_profit": round((float(current_price) - avg_price) * qty, 2),
            })
        else:
            row.update({
                "current_price": None,
                "value": None,
                "unrealized_profit": None,
                "warning": "price_unavailable",
            })
        results.append(row)

    realized = sum(float(x.get("profit", 0) or 0.0) for x in realized_col.find({"user_id": oid}))
    return {"holdings": results, "realized_profit": realized}


# ----------------------------
# Collectr scraping helper (unchanged)
# ----------------------------
@app.get("/collectr_value")
def get_collectr_value(url: str = Query(..., description="Full Collectr app link")):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Could not access the page")

        soup = BeautifulSoup(resp.text, "html.parser")
        value_el = soup.find(string=lambda t: "$" in t and "Collection" in t)
        if not value_el:
            value_el = soup.find(lambda tag: tag.name in ["span", "div"] and "$" in tag.text)
        if not value_el:
            raise HTTPException(status_code=404, detail="Could not find collection value on page")

        import re
        match = re.search(r"\$([\d,\.]+)", value_el if isinstance(value_el, str) else value_el.text)
        if not match:
            raise HTTPException(status_code=404, detail="Could not parse value")

        value = float(match.group(1).replace(",", ""))
        return {"total_value": value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch: {e}")


