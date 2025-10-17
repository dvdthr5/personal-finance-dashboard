# backend/main.py
from __future__ import annotations
import os, time, asyncio, logging, requests, bcrypt, yfinance as yf
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from dotenv import load_dotenv

# ----------------------------
# Setup
# ----------------------------
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "finance")
FMP_API_KEY = os.getenv("FMP_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI not set")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backend")

log.info("✅ Connected to Mongo — databases: %s", client.list_database_names())

users_col = db["users"]
holdings_col = db["holdings"]
realized_col = db["realized_gains"]
prices_col = db["prices"]
holdings_col.create_index([("symbol", ASCENDING), ("user_id", ASCENDING)], unique=True)
prices_col.create_index([("symbol", ASCENDING)], unique=True)

# ----------------------------
# Models
# ----------------------------
class Holding(BaseModel):
    symbol: str
    qty: float
    price: float
    user_id: str

class SellRequest(BaseModel):
    qty: float
    price: Optional[float] = None
    user_id: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    identifier: str
    password: str

# ----------------------------
# Utils
# ----------------------------
def send_welcome_email(to_email: str, username: str):
    if not SENDGRID_API_KEY or not FROM_EMAIL:
        log.info("ℹ️ No SendGrid config; skipping email.")
        return
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        msg = Mail(
            from_email=FROM_EMAIL,
            to_emails=to_email,
            subject="🎉 Welcome to Your Personal Finance Dashboard!",
            html_content=f"<p>Hi {username}, welcome aboard!</p>"
        )
        sg.send(msg)
        log.info("✅ Sent welcome email to %s", to_email)
    except Exception as e:
        log.info("❌ Failed to send email: %s", e)

def _store_price(symbol: str, price: float):
    prices_col.update_one(
        {"symbol": symbol.upper()},
        {"$set": {"price": float(price), "timestamp": time.time()}},
        upsert=True
    )

def _read_price(symbol: str) -> Optional[Dict[str, Any]]:
    return prices_col.find_one({"symbol": symbol.upper()})

# ----------------------------
# Price fetchers
# ----------------------------
def _fmp_batch_quote(symbols: List[str]) -> Dict[str, float]:
    """Fetch all symbols in one go using FMP /quote-short (free tier)."""
    prices = {}
    if not FMP_API_KEY or not symbols:
        return prices
    sym_csv = ",".join(symbols)
    url = f"https://financialmodelingprep.com/api/v3/quote-short/{sym_csv}"
    params = {"apikey": FMP_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 403:
            log.info("🚫 FMP 403 — your free key hit rate or plan limit.")
            return prices
        r.raise_for_status()
        data = r.json()
        for item in data:
            sym, p = item.get("symbol"), item.get("price")
            if sym and p:
                prices[sym.upper()] = float(p)
        return prices
    except Exception as e:
        log.info("❌ FMP batch fetch failed: %s", e)
        return prices

def _yahoo_quote(symbol: str) -> Optional[float]:
    try:
        t = yf.Ticker(symbol)
        p = None
        # try simple history
        hist = t.history(period="1d")
        if not hist.empty:
            p = hist["Close"].iloc[-1]
        return float(p) if p else None
    except Exception as e:
        log.info("❌ yfinance error for %s: %s", symbol, e)
        return None

def get_cached_price(symbol: str, max_age: int = 3600, allow_fetch: bool = True) -> Optional[float]:
    symbol = symbol.upper()
    doc = _read_price(symbol)
    now = time.time()
    if doc and now - doc.get("timestamp", 0) < max_age:
        return doc["price"]
    if allow_fetch:
        p = _yahoo_quote(symbol)
        if p:
            _store_price(symbol, p)
            return p
    return doc["price"] if doc else None

def refresh_all_prices():
    symbols = holdings_col.distinct("symbol")
    if not symbols:
        log.info("ℹ️ No holdings yet to refresh.")
        return
    log.info("🌐 Refreshing prices for %d symbols (batched)...", len(symbols))
    batch_prices = _fmp_batch_quote(symbols)
    for sym in symbols:
        p = batch_prices.get(sym)
        if p:
            _store_price(sym, p)
            log.info("✅ %s @ %.2f", sym, p)
        else:
            # fallback to Yahoo (slower)
            p2 = _yahoo_quote(sym)
            if p2:
                _store_price(sym, p2)
                log.info("✅ %s (Yahoo) @ %.2f", sym, p2)
            else:
                log.info("⚠️ %s — no price fetched", sym)
        time.sleep(1.5)
    log.info("✅ Price refresh complete.")

# ----------------------------
# FastAPI setup
# ----------------------------
app = FastAPI(title="Personal Finance Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    try:
        refresh_all_prices()
    except Exception as e:
        log.info("❌ Startup refresh failed: %s", e)
    async def periodic_refresh():
        while True:
            await asyncio.to_thread(refresh_all_prices)
            await asyncio.sleep(3600)
    asyncio.create_task(periodic_refresh())

# ----------------------------
# Auth
# ----------------------------
@app.post("/register")
def register_user(req: RegisterRequest, bg: BackgroundTasks):
    if users_col.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    def create():
        hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt())
        users_col.insert_one({"username": req.username, "email": req.email, "password": hashed})
        send_welcome_email(req.email, req.username)
    bg.add_task(create)
    return {"message": "Registration queued"}

@app.post("/login")
def login(req: LoginRequest):
    user = users_col.find_one({"$or": [{"email": req.identifier}, {"username": req.identifier}]})
    if not user or not bcrypt.checkpw(req.password.encode(), user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"user_id": str(user["_id"]), "username": user["username"]}

# ----------------------------
# Holdings
# ----------------------------
@app.post("/holding")
def add_holding(req: Holding, bg: BackgroundTasks):
    def insert():
        user_id = ObjectId(req.user_id)
        sym = req.symbol.upper()
        price = get_cached_price(sym, allow_fetch=True)
        existing = holdings_col.find_one({"symbol": sym, "user_id": user_id})
        if existing:
            new_qty = existing["qty"] + req.qty
            new_price = ((existing["price"] * existing["qty"]) + (req.price * req.qty)) / new_qty
            holdings_col.update_one({"_id": existing["_id"]}, {"$set": {"qty": new_qty, "price": new_price}})
        else:
            holdings_col.insert_one({"symbol": sym, "qty": req.qty, "price": req.price, "user_id": user_id})
        log.info("✅ Holding %s updated for user %s", sym, user_id)
    bg.add_task(insert)
    return {"status": "ok"}

# ----------------------------
# Portfolio
# ----------------------------
@app.get("/portfolio")
def portfolio(user_id: str = Query(...)):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    holdings = list(holdings_col.find({"user_id": ObjectId(user_id)}))
    results = []
    for h in holdings:
        sym = h["symbol"]
        qty = h["qty"]
        avg = h["price"]
        p = get_cached_price(sym, allow_fetch=False)
        results.append({
            "symbol": sym,
            "qty": qty,
            "avg_price": avg,
            "current_price": round(p, 2) if p else None,
            "value": round((p or 0) * qty, 2) if p else None,
            "unrealized_profit": round(((p or 0) - avg) * qty, 2) if p else None
        })
    realized = sum(float(x.get("profit", 0)) for x in realized_col.find({"user_id": ObjectId(user_id)}))
    return {"holdings": results, "realized_profit": realized}

# ----------------------------
# Health
# ----------------------------
@app.get("/health")
def health(): return {"status": "ok"}
