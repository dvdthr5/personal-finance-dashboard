# backend/main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from dotenv import load_dotenv

import os
import time
import threading
import requests
import yfinance as yf
import bcrypt
from bs4 import BeautifulSoup

# =========================
# Setup & configuration
# =========================
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/")
DB_NAME = os.getenv("DB_NAME", "finance")
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
PRICE_TTL_SECONDS = 3600  # 1 hour

# Mongo client / DB
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
print(f"✅ Connected to Mongo — databases: {client.list_database_names()}")

users_col = db["users"]
holdings_col = db["holdings"]
realized_col = db["realized_gains"]
prices_cache_col = db["prices_cache"]

# Indexes
holdings_col.create_index([("symbol", ASCENDING), ("user_id", ASCENDING)], unique=True)
prices_cache_col.create_index([("symbol", ASCENDING)], unique=True)

# CORS (Streamlit -> FastAPI)
app = FastAPI(title="Personal Finance Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if desired
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory cache: { "AAPL": {"price": 123.45, "time": 1710000000.0} }
mem_price_cache: dict[str, dict[str, float]] = {}

# =========================
# Utilities
# =========================
def _objid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

def _now() -> float:
    return time.time()

def _get_mem_price(symbol: str):
    e = mem_price_cache.get(symbol)
    if e and _now() - e["time"] < PRICE_TTL_SECONDS:
        return e["price"]
    return None

def _set_mem_price(symbol: str, price: float):
    mem_price_cache[symbol] = {"price": price, "time": _now()}

def _get_db_price(symbol: str):
    doc = prices_cache_col.find_one({"symbol": symbol})
    if not doc:
        return None
    ts = float(doc.get("updated_at", 0))
    if _now() - ts < PRICE_TTL_SECONDS:
        return float(doc["price"])
    return None

def _set_db_price(symbol: str, price: float):
    prices_cache_col.update_one(
        {"symbol": symbol},
        {"$set": {"symbol": symbol, "price": float(price), "updated_at": _now()}},
        upsert=True,
    )

def fetch_price_from_fmp(symbol: str) -> float | None:
    
    if not FMP_API_KEY:
        print("⚠️ No FMP API key configured.")
        return None

    url = f"https://financialmodelingprep.com/api/v3/quote/{symbol.upper()}?apikey={FMP_API_KEY}"
    headers = {"User-Agent": "Mozilla/5.0 (FinanceDashboardBot/1.0)"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 403:
            print(f"🚫 FMP 403 for {symbol} — free-tier limit reached, invalid key, or legacy endpoint.")
            return None
        if r.status_code != 200:
            print(f"⚠️ FMP returned {r.status_code} for {symbol}")
            return None

        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            entry = data[0]
            # The /quote endpoint returns more fields, including 'price'
            if "price" in entry and entry["price"] is not None:
                price = float(entry["price"])
                print(f"✅ FMP: {symbol} @ {price}")
                return price
            else:
                print(f"⚠️ FMP: Missing 'price' field in response: {entry}")
        else:
            print(f"⚠️ FMP: Unexpected data format for {symbol}: {data}")

    except requests.exceptions.RequestException as e:
        print(f"❌ FMP network error for {symbol}: {e}")
    except Exception as e:
        print(f"❌ FMP fetch failed for {symbol}: {e}")

    return None



def fetch_price_from_yf(symbol: str) -> float | None:
    """Fallback using yfinance last close."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        t = yf.Ticker(symbol)
        hist = t.history(period="5d")
        if hist.empty:
            print(f"⚠️ yfinance empty for {symbol}")
            return None
        close_price = float(hist["Close"].dropna().iloc[-1])
        print(f"✅ yfinance: {symbol} @ {close_price}")
        return close_price
    except Exception as e:
        print(f"❌ yfinance error for {symbol}: {e}")
        return None


def get_price(symbol: str, force_refresh: bool = False) -> float | None:
    """Unified price getter: memory → DB → FMP → yfinance → fallback."""
    symbol = symbol.upper()

    if not force_refresh:
        p = _get_mem_price(symbol)
        if p is not None:
            return p
        p = _get_db_price(symbol)
        if p is not None:
            _set_mem_price(symbol, p)
            return p

    # --- 1️⃣ FMP primary ---
    p = fetch_price_from_fmp(symbol)
    if p is not None:
        _set_db_price(symbol, p)
        _set_mem_price(symbol, p)
        return p

    # --- 2️⃣ yfinance fallback ---
    p = fetch_price_from_yf(symbol)
    if p is not None:
        _set_db_price(symbol, p)
        _set_mem_price(symbol, p)
        return p

    # --- 3️⃣ optional free fallback (for testing only) ---
    try:
        r = requests.get(f"https://api.twelvedata.com/price?symbol={symbol}&apikey=demo", timeout=10)
        data = r.json()
        if "price" in data:
            p = float(data["price"])
            _set_db_price(symbol, p)
            _set_mem_price(symbol, p)
            print(f"✅ Fallback price: {symbol} @ {p}")
            return p
    except Exception:
        pass

    print(f"🚫 No valid price found for {symbol}")
    return None

def preload_all_symbols_prices():
    """Preload prices for all distinct symbols (at startup) to warm cache."""
    try:
        symbols = [s.upper() for s in holdings_col.distinct("symbol")]
        if not symbols:
            print("ℹ️ No holdings to preload prices for.")
            return
        print(f"🌐 Refreshing prices for {len(symbols)} symbols...")
        fails = 0
        for idx, sym in enumerate(symbols, start=1):
            price = get_price(sym, force_refresh=True)
            if price is None:
                print(f"   {idx}) ⚠️ {sym} — no price fetched")
                fails += 1
            else:
                print(f"   {idx}) ✅ {sym}: ${price:.2f}")
            # Be gentle to free APIs
            time.sleep(0.6)
        print(f"✅ Price refresh complete. (failures: {fails}/{len(symbols)})")
    except Exception as e:
        print(f"❌ Preload failed: {e}")

def hourly_price_refresher_loop():
    """Background thread that refreshes all symbols every hour."""
    while True:
        try:
            preload_all_symbols_prices()
        except Exception as e:
            print(f"❌ Hourly refresh error: {e}")
        # sleep 1 hour
        time.sleep(3600)

# =========================
# Models
# =========================
class Holding(BaseModel):
    symbol: str
    qty: float
    price: float
    user_id: str  # stringified ObjectId

class SellRequest(BaseModel):
    qty: float
    price: float | None = None
    user_id: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    identifier: str  # username OR email
    password: str

# =========================
# Health
# =========================
@app.get("/health")
def health():
    ok = True
    try:
        client.admin.command("ping")
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded"}

# =========================
# Auth
# =========================
@app.post("/register")
def register_user(req: RegisterRequest):
    if not req.email or not req.username or not req.password:
        raise HTTPException(status_code=400, detail="All fields are required")

    if users_col.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    if users_col.find_one({"username": req.username}):
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed_pw = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt())
    users_col.insert_one({
        "username": req.username,
        "email": req.email,
        "password": hashed_pw,
        "admin": False
    })
    return {"message": "Account created"}

@app.post("/login")
def login_user(req: LoginRequest):
    user = users_col.find_one({
        "$or": [{"email": req.identifier}, {"username": req.identifier}]
    })
    if not user or not bcrypt.checkpw(req.password.encode("utf-8"), user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {"user_id": str(user["_id"]), "username": user["username"], "admin": user["admin"]}

# =========================
# Holdings
# =========================
@app.post("/holding")
def add_or_update_holding(req: Holding):
    user_id = _objid(req.user_id)
    sym = req.symbol.upper()

    # Upsert holding synchronously
    existing = holdings_col.find_one({"symbol": sym, "user_id": user_id})
    if existing:
        new_qty = float(existing["qty"]) + float(req.qty)
        # new weighted average price
        new_price = ((float(existing["price"]) * float(existing["qty"])) + (float(req.price) * float(req.qty))) / new_qty
        holdings_col.update_one(
            {"_id": existing["_id"]},
            {"$set": {"qty": new_qty, "price": new_price}}
        )
    else:
        holdings_col.insert_one({
            "symbol": sym,
            "qty": float(req.qty),
            "price": float(req.price),
            "user_id": user_id
        })

    # Fetch and cache price immediately (best-effort)
    p = get_price(sym, force_refresh=True)
    if p is not None:
        print(f"💰 Cached {sym} @ {p:.2f} on insert/update")

    return {"status": "ok", "message": f"Holding {sym} saved"}

@app.delete("/holding/{symbol}")
def delete_holding(symbol: str, user_id: str = Query(...)):
    sym = symbol.upper()
    uid = _objid(user_id)
    result = holdings_col.delete_one({"symbol": sym, "user_id": uid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Holding not found")
    return {"status": "ok", "message": f"Deleted {sym}"}

@app.post("/holding/{symbol}/sell")
def sell_holding(symbol: str, req: SellRequest):
    sym = symbol.upper()
    uid = _objid(req.user_id)
    h = holdings_col.find_one({"symbol": sym, "user_id": uid})
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")

    qty_to_sell = float(req.qty)
    if qty_to_sell <= 0 or qty_to_sell > float(h["qty"]):
        raise HTTPException(status_code=400, detail="Invalid sell quantity")

    buy_price = float(h["price"])
    # Use provided sell price or market price (last)
    sell_price = float(req.price) if req.price is not None else (get_price(sym) or buy_price)
    profit = (sell_price - buy_price) * qty_to_sell

    realized_col.insert_one({
        "symbol": sym,
        "qty": qty_to_sell,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "profit": profit,
        "user_id": uid,
        "ts": _now()
    })

    new_qty = float(h["qty"]) - qty_to_sell
    if new_qty <= 0:
        holdings_col.delete_one({"_id": h["_id"]})
    else:
        holdings_col.update_one({"_id": h["_id"]}, {"$set": {"qty": new_qty}})

    return {"status": "ok", "realized_profit": profit}

# =========================
# Portfolio
# =========================
@app.get("/portfolio")
def get_portfolio(user_id: str = Query(...)):
    uid = _objid(user_id)
    user = users_col.find_one({"_id": uid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    holdings = list(holdings_col.find({"user_id": uid, "qty": {"$gt": 0}}))
    symbols = [h["symbol"].upper() for h in holdings]

    # Gather prices with caching; do not drop holdings if price missing
    prices: dict[str, float] = {}
    for sym in symbols:
        p = get_price(sym)  # cached if available
        if p is not None:
            prices[sym] = p

    results = []
    for h in holdings:
        sym = h["symbol"].upper()
        qty = float(h["qty"])
        avg_price = float(h["price"])
        current_price = prices.get(sym)

        entry = {"symbol": sym, "qty": qty, "avg_price": avg_price}
        if current_price is not None:
            entry.update(
                {
                    "current_price": round(current_price, 2),
                    "value": round(current_price * qty, 2),
                    "unrealized_profit": round((current_price - avg_price) * qty, 2),
                }
            )
        else:
            entry.update(
                {
                    "current_price": None,
                    "value": None,
                    "unrealized_profit": None,
                    "warning": "price_unavailable",
                }
            )
        results.append(entry)

    realized = sum(
        float(x.get("profit", 0))
        for x in realized_col.find({"user_id": uid})
    )

    return {"holdings": results, "realized_profit": realized}

# =========================
# Collectr scraping helper
# =========================
@app.get("/collectr_value")
def get_collectr_value(url: str = Query(..., description="Full Collectr app link")):
    """Scrapes the total collection value from a public Collectr link."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Could not access the page")

        soup = BeautifulSoup(resp.text, "html.parser")
        # Very generic selectors; adjust if Collectr DOM changes:
        value_el = soup.find(string=lambda t: t and "$" in t and "Collection" in t)
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch: {e}")

# =========================
# Startup hooks
# =========================
@app.on_event("startup")
def on_startup():
    # Preload once at boot so the UI has prices immediately
    threading.Thread(target=preload_all_symbols_prices, daemon=True).start()
    # Then keep prices relatively fresh hourly
    threading.Thread(target=hourly_price_refresher_loop, daemon=True).start()
