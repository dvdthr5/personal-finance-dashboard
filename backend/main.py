from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from dotenv import load_dotenv
from datetime import datetime
import os
import time
import threading
import requests
import bcrypt
from bs4 import BeautifulSoup

# =========================
# Setup & configuration
# =========================
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017/")
DB_NAME = os.getenv("DB_NAME", "finance")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()
PRICE_TTL_SECONDS = 3600  # 1 hour
RATE_LIMIT_CALLS_PER_MIN = 8

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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory cache: { "AAPL": {"price": 123.45, "time": 1710000000.0} }
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


# =========================
# Price Fetching
# =========================
def fetch_price_from_twelvedata(symbol: str) -> float | None:
    """Fetch latest stock price using Twelve Data API (free-tier friendly)."""
    api_key = TWELVEDATA_API_KEY
    if not api_key:
        print("⚠️ No Twelve Data API key configured.")
        return None

    url = f"https://api.twelvedata.com/price?symbol={symbol.upper()}&apikey={api_key}"
    headers = {"User-Agent": "Mozilla/5.0 (FinanceDashboardBot/1.0)"}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ Twelve Data returned {r.status_code} for {symbol}")
            return None
        data = r.json()
        if "price" in data and data["price"] is not None:
            price = float(data["price"])
            print(f"✅ Twelve Data: {symbol} @ {price}")
            return price
        print(f"⚠️ Twelve Data: unexpected response for {symbol}: {data}")
    except Exception as e:
        print(f"❌ Twelve Data fetch failed for {symbol}: {e}")
    return None


def fetch_price(symbol: str) -> float | None:
    """Unified fetcher using Twelve Data only."""
    symbol = symbol.upper()
    p = fetch_price_from_twelvedata(symbol)
    if p is not None:
        _set_db_price(symbol, p)
        _set_mem_price(symbol, p)
        return p
    print(f"🚫 No valid price found for {symbol}")
    return None


# =========================
# Continuous price refresher
# =========================
def continuous_price_refresher():
    """Continuously fill missing/stale prices within the API rate limit."""
    SLEEP_BETWEEN_CALLS = 60 / RATE_LIMIT_CALLS_PER_MIN  # ≈7.5 seconds

    while True:
        try:
            symbols = [s.upper() for s in holdings_col.distinct("symbol")]
            for sym in symbols:
                current = _get_mem_price(sym) or _get_db_price(sym)
                if current is None:
                    print(f"🔄 Fetching missing price for {sym} ...")
                    price = fetch_price(sym)
                    if price is not None:
                        print(f"✅ Cached {sym} @ {price}")
                    else:
                        print(f"⚠️ Still missing {sym}, will retry later")
                    time.sleep(SLEEP_BETWEEN_CALLS)
            print("🕐 Cycle complete — waiting 60s before next sweep")
            time.sleep(60)
        except Exception as e:
            print(f"❌ Refresher error: {e}")
            time.sleep(60)


# =========================
# Models
# =========================
class Holding(BaseModel):
    symbol: str
    qty: float
    price: float
    user_id: str


class SellRequest(BaseModel):
    qty: float
    price: float | None = None
    user_id: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    identifier: str
    password: str


# =========================
# Root Endpoint
# =========================
@app.get("/")
def read_root():
    """Root endpoint to confirm backend is running."""
    return {"message": "Backend is running successfully 🚀"}

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
    users_col.insert_one(
        {
            "username": req.username,
            "email": req.email,
            "password": hashed_pw,
            "admin": False,
        }
    )
    return {"message": "Account created"}


@app.post("/login")
def login_user(req: LoginRequest):
    user = users_col.find_one(
        {"$or": [{"email": req.identifier}, {"username": req.identifier}]}
    )
    if not user or not bcrypt.checkpw(req.password.encode("utf-8"), user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {
        "user_id": str(user["_id"]),
        "username": user["username"],
        "admin": user["admin"],
    }


# =========================
# Holdings (Add / Update / Delete / Sell)
# =========================


@app.post("/holding")
def add_or_update_holding(req: Holding):
    """
    Creates or updates a holding for the given user.
    - If it exists, overwrite qty & price directly (edit mode).
    - If it doesn’t, create a new record.
    """
    user_id = _objid(req.user_id)
    sym = req.symbol.upper()

    existing = holdings_col.find_one({"symbol": sym, "user_id": user_id})
    if existing:
        holdings_col.update_one(
            {"_id": existing["_id"]},
            {"$set": {"qty": float(req.qty), "price": float(req.price)}},
        )
        print(f"🟢 Updated holding {sym} for user {user_id}")
        action = "updated"
    else:
        holdings_col.insert_one(
            {
                "symbol": sym,
                "qty": float(req.qty),
                "price": float(req.price),
                "user_id": user_id,
            }
        )
        print(f"🟢 Created new holding {sym} for user {user_id}")
        action = "created"

    # Cache price best-effort
    try:
        fetch_price(sym)
    except Exception as e:
        print(f"⚠️ Could not fetch price for {sym}: {e}")

    return {"status": "ok", "message": f"Holding {sym} {action} successfully"}


@app.delete("/holding/{symbol}")
def delete_holding(symbol: str, user_id: str = Query(...)):
    """
    Deletes a holding for a specific user by symbol.
    Case-insensitive and logs detailed status.
    """
    sym = symbol.upper()
    uid = _objid(user_id)

    print(f"🗑️ Attempting to delete holding {sym} for user {uid}")
    result = holdings_col.delete_one({"symbol": sym, "user_id": uid})

    if result.deleted_count == 0:
        print(f"⚠️ No holding found for {sym} with user_id {uid}")
        raise HTTPException(
            status_code=404, detail=f"Holding {sym} not found for this user"
        )

    print(f"✅ Deleted holding {sym} for user {uid}")
    return {"status": "ok", "message": f"Deleted {sym}"}


@app.post("/holding/{symbol}/sell")
def sell_holding(symbol: str, req: SellRequest):
    """
    Sells part or all of a holding, records realized profit.
    """
    sym = symbol.upper()
    uid = _objid(req.user_id)
    h = holdings_col.find_one({"symbol": sym, "user_id": uid})
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")

    qty_to_sell = float(req.qty)
    if qty_to_sell <= 0 or qty_to_sell > float(h["qty"]):
        raise HTTPException(status_code=400, detail="Invalid sell quantity")

    buy_price = float(h["price"])
    sell_price = (
        float(req.price)
        if req.price is not None
        else (_get_mem_price(sym) or _get_db_price(sym) or buy_price)
    )
    profit = (sell_price - buy_price) * qty_to_sell

    realized_col.insert_one(
        {
            "symbol": sym,
            "qty": qty_to_sell,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit": profit,
            "user_id": uid,
            "ts": datetime.utcnow(),
        }
    )

    new_qty = float(h["qty"]) - qty_to_sell
    if new_qty <= 0:
        holdings_col.delete_one({"_id": h["_id"]})
        print(f"💸 Sold out of {sym}, holding removed.")
    else:
        holdings_col.update_one({"_id": h["_id"]}, {"$set": {"qty": new_qty}})
        print(f"💸 Sold {qty_to_sell} of {sym}, remaining {new_qty} shares.")

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

    results = []
    for h in holdings:
        sym = h["symbol"].upper()
        qty = float(h["qty"])
        avg_price = float(h["price"])
        current_price = _get_mem_price(sym) or _get_db_price(sym)

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
        float(x.get("profit", 0)) for x in realized_col.find({"user_id": uid})
    )
    return {"holdings": results, "realized_profit": realized}


@app.get("/sales_history")
def get_sales_history(user_id: str = Query(...)):
    """Return all realized sales for the given user."""
    uid = _objid(user_id)
    sales = list(realized_col.find({"user_id": uid}).sort("ts", -1))
    results = []
    for s in sales:
        results.append(
            {
                "id": str(s["_id"]),
                "symbol": s.get("symbol"),
                "qty": float(s.get("qty", 0)),
                "buy_price": float(s.get("buy_price", 0)),
                "sell_price": float(s.get("sell_price", 0)),
                "profit": float(s.get("profit", 0)),
                "timestamp": s.get("ts"),
            }
        )
    return {"sales": results}


@app.delete("/sales_history/{sale_id}")
def delete_sale_record(sale_id: str, user_id: str = Query(...)):
    """Delete a single realized sale record for the user."""
    try:
        uid = _objid(user_id)
        sid = ObjectId(sale_id)
        result = realized_col.delete_one({"_id": sid, "user_id": uid})
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404, detail="Sale not found or not authorized to delete."
            )
        return {"status": "ok", "message": "Sale deleted."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete sale: {e}")


# =========================
# Collectr Scraper
# =========================
@app.get("/collectr_value")
def get_collectr_value(url: str = Query(..., description="Full Collectr app link")):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Could not access the page")

        soup = BeautifulSoup(resp.text, "html.parser")
        value_el = soup.find(string=lambda t: t and "$" in t and "Collection" in t)
        if not value_el:
            value_el = soup.find(
                lambda tag: tag.name in ["span", "div"] and "$" in tag.text
            )

        if not value_el:
            raise HTTPException(
                status_code=404, detail="Could not find collection value on page"
            )

        import re

        match = re.search(
            r"\$([\d,\.]+)", value_el if isinstance(value_el, str) else value_el.text
        )
        if not match:
            raise HTTPException(status_code=404, detail="Could not parse value")

        value = float(match.group(1).replace(",", ""))
        return {"total_value": value}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch: {e}")


# =========================
# Startup
# =========================
@app.on_event("startup")
def on_startup():
    threading.Thread(target=continuous_price_refresher, daemon=True).start()
