from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from bson import ObjectId
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

import os
import time
import requests
from bs4 import BeautifulSoup
import bcrypt

# ----------------------------
# Setup
# ----------------------------
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "finance")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL")
FMP_API_KEY = os.getenv("FMP_API_KEY")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
print("✅ Connected to:", client.address, "— databases:", client.list_database_names())

users_col = db["users"]
holdings_col = db["holdings"]
realized_col = db["realized_gains"]

# Index for uniqueness per user
holdings_col.create_index([("symbol", ASCENDING), ("user_id", ASCENDING)], unique=True)

# ----------------------------
# Price Fetching (via FMP API)
# ----------------------------
def fetch_live_price(symbol: str) -> float | None:
    """Fetch live stock price using FMP free-tier endpoints with browser headers."""
    symbol = symbol.upper()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }

    # Primary free endpoint
    url_main = f"https://financialmodelingprep.com/api/v3/quote-short/{symbol}?apikey={FMP_API_KEY}"
    # Fallback endpoint (works for many tickers)
    url_fallback = f"https://financialmodelingprep.com/api/v3/profile/{symbol}?apikey={FMP_API_KEY}"

    try:
        res = requests.get(url_main, headers=headers, timeout=10)
        if res.status_code == 403:
            print(f"⚠️ 403 on quote-short — retrying with profile endpoint for {symbol}")
            res = requests.get(url_fallback, headers=headers, timeout=10)

        res.raise_for_status()
        data = res.json()

        if isinstance(data, list) and len(data) > 0:
            if "price" in data[0]:
                price = float(data[0]["price"])
            elif "price" in data[0].get("price", {}):
                price = float(data[0]["price"])
            elif "price" in data[0].get("price", {}):
                price = float(data[0]["price"])
            elif "price" in data[0].get("price", {}):
                price = float(data[0]["price"])
            elif "price" in data[0].get("price", {}):
                price = float(data[0]["price"])
            else:
                # Some /profile/ results use "price" at top level
                price = float(data[0].get("price", 0))

            if price > 0:
                print(f"✅ {symbol} fetched @ ${price:.2f}")
                return price

        print(f"⚠️ No valid price found for {symbol}")
        return None

    except requests.exceptions.HTTPError as e:
        if res.status_code == 403:
            print(f"🚫 Still forbidden for {symbol}. Likely daily quota reached.")
        else:
            print(f"❌ HTTP error for {symbol}: {e}")
        return None
    except Exception as e:
        print(f"❌ Failed to fetch {symbol}: {e}")
        return None




def update_price_cache(symbol: str, price: float):
    """Store cached price + timestamp in holdings collection."""
    holdings_col.update_many(
        {"symbol": symbol},
        {"$set": {"latest_price": price, "last_updated": time.time()}}
    )


def get_cached_or_live_price(symbol: str) -> float | None:
    """Return cached price if under 1 hour old; otherwise refresh."""
    holding = holdings_col.find_one({"symbol": symbol})
    if holding and "latest_price" in holding and "last_updated" in holding:
        age = time.time() - holding["last_updated"]
        if age < 3600:
            return float(holding["latest_price"])

    price = fetch_live_price(symbol)
    if price is not None:
        update_price_cache(symbol, price)
    return price


def refresh_all_prices():
    """Refresh all unique ticker prices hourly."""
    try:
        symbols = holdings_col.distinct("symbol")
        if not symbols:
            print("ℹ️ No holdings to refresh.")
            return

        print(f"🌐 Refreshing {len(symbols)} stock prices...")
        for sym in symbols:
            p = fetch_live_price(sym)
            if p:
                update_price_cache(sym, p)
            time.sleep(1.2)  # avoid API rate limits
        print("✅ Price refresh complete.")
    except Exception as e:
        print(f"❌ Price refresh failed: {e}")

# ----------------------------
# App Initialization
# ----------------------------
app = FastAPI(title="Personal Finance Dashboard API")

@app.on_event("startup")
def on_startup():
    """Fetch prices once at startup."""
    refresh_all_prices()

# CORS for Streamlit
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Utilities
# ----------------------------
def send_welcome_email(to_email: str, username: str):
    if not SENDGRID_API_KEY or not FROM_EMAIL:
        print("ℹ️ SENDGRID_API_KEY or FROM_EMAIL not set; skipping email.")
        return

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject="🎉 Welcome to Your Personal Finance Dashboard!",
        html_content=f"""
        <html>
            <body style="font-family: sans-serif;">
                <h2>Hi {username},</h2>
                <p>Thanks for creating an account with <b>Personal Finance Dashboard</b>!</p>
                <p>You can now log in to track your portfolio and manage your investments.</p>
                <p>Stay smart with your finances,<br>— The Dashboard Team</p>
            </body>
        </html>
        """
    )

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"✅ Email sent to {to_email} (status {response.status_code})")
    except Exception as e:
        print(f"❌ Failed to send SendGrid email: {e}")

# ----------------------------
# Pydantic Models
# ----------------------------
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

# ----------------------------
# Health Endpoint
# ----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ----------------------------
# Authentication
# ----------------------------
@app.post("/register")
def register_user(req: RegisterRequest, background_tasks: BackgroundTasks):
    if users_col.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already registered")

    def create_user():
        hashed_pw = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt())
        users_col.insert_one({
            "username": req.username,
            "email": req.email,
            "password": hashed_pw,
            "admin": False
        })
        send_welcome_email(req.email, req.username)

    background_tasks.add_task(create_user)
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
# Holdings Endpoints
# ----------------------------
@app.post("/holding")
def add_holding(req: Holding, background_tasks: BackgroundTasks):
    def insert_or_update():
        user_id = ObjectId(req.user_id)
        sym = req.symbol.upper()

        # Fetch and store price immediately
        price = fetch_live_price(sym)
        if price:
            update_price_cache(sym, price)
            print(f"💰 Cached {sym} @ ${price:.2f} on insert")

        existing = holdings_col.find_one({"symbol": sym, "user_id": user_id})
        if existing:
            new_qty = existing["qty"] + req.qty
            new_price = ((existing["price"] * existing["qty"]) + (req.price * req.qty)) / new_qty
            holdings_col.update_one(
                {"_id": existing["_id"]},
                {"$set": {"qty": new_qty, "price": new_price}}
            )
        else:
            holdings_col.insert_one({
                "symbol": sym,
                "qty": req.qty,
                "price": req.price,
                "user_id": user_id,
                "latest_price": price,
                "last_updated": time.time()
            })
        print(f"✅ Holding {sym} saved for {user_id}")

    background_tasks.add_task(insert_or_update)
    return {"status": "success", "message": f"Holding {req.symbol.upper()} queued for saving"}


@app.delete("/holding/{symbol}")
def delete_holding(symbol: str, background_tasks: BackgroundTasks, user_id: str = Query(...)):
    sym = symbol.upper()

    def perform_delete():
        result = holdings_col.delete_one({"symbol": sym, "user_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            print(f"⚠️ Holding {sym} not found for {user_id}")
        else:
            print(f"✅ Holding {sym} deleted for {user_id}")

    background_tasks.add_task(perform_delete)
    return {"status": "queued", "message": f"Holding {sym} deletion queued"}


@app.post("/holding/{symbol}/sell")
def sell_holding(symbol: str, request: SellRequest, background_tasks: BackgroundTasks):
    def process_sale():
        user_id = ObjectId(request.user_id)
        sym = symbol.upper()
        h = holdings_col.find_one({"symbol": sym, "user_id": user_id})
        if not h:
            print(f"❌ Holding {sym} not found for {user_id}")
            return
        qty_to_sell = float(request.qty)
        if qty_to_sell <= 0 or qty_to_sell > float(h["qty"]):
            print("❌ Invalid quantity")
            return
        buy_price = float(h["price"])
        sell_price = float(request.price) if request.price else get_cached_or_live_price(sym)
        if not sell_price:
            print(f"⚠️ Could not fetch current price for {sym}")
            return

        profit = (sell_price - buy_price) * qty_to_sell
        realized_col.insert_one({
            "symbol": sym,
            "qty": qty_to_sell,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "profit": profit,
            "user_id": user_id
        })

        new_qty = float(h["qty"]) - qty_to_sell
        if new_qty <= 0:
            holdings_col.delete_one({"_id": h["_id"]})
        else:
            holdings_col.update_one({"_id": h["_id"]}, {"$set": {"qty": new_qty}})
        print(f"✅ Sold {sym} — profit {profit}")

    background_tasks.add_task(process_sale)
    return {"status": "queued", "message": f"Sale of {symbol.upper()} queued for processing."}

# ----------------------------
# Portfolio
# ----------------------------
@app.get("/portfolio")
def get_portfolio(user_id: str = Query(...)):
    user = users_col.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    holdings = list(holdings_col.find({"user_id": ObjectId(user_id), "qty": {"$gt": 0}}))

    results = []
    for h in holdings:
        symbol = h["symbol"]
        qty = float(h["qty"])
        avg_price = float(h["price"])
        current_price = get_cached_or_live_price(symbol)

        entry = {
            "symbol": symbol,
            "qty": qty,
            "avg_price": avg_price,
        }
        if current_price:
            entry.update({
                "current_price": round(current_price, 2),
                "value": round(current_price * qty, 2),
                "unrealized_profit": round((current_price - avg_price) * qty, 2),
            })
        else:
            entry.update({
                "current_price": None,
                "value": None,
                "unrealized_profit": None,
                "warning": "price_unavailable",
            })
        results.append(entry)

    realized = sum(float(x.get("profit", 0)) for x in realized_col.find({"user_id": ObjectId(user_id)}))
    return {"holdings": results, "realized_profit": realized}

# ----------------------------
# Collectr Value Scraper
# ----------------------------
@app.get("/collectr_value")
def get_collectr_value(url: str = Query(..., description="Full Collectr app link")):
    """Scrapes total collection value from a public Collectr link."""
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
