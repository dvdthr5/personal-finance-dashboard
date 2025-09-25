# backend/main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
import os
import yfinance as yf

load_dotenv()
print("Loaded DB:", os.getenv("DB_NAME"))
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "finance")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
trades_col = db["trades"]
holdings_col = db["holdings"]

# Ensure an index on symbol for upsert/queries
holdings_col.create_index([("symbol", ASCENDING)], unique=True)
trades_col.create_index([("created_at", ASCENDING)])

app = FastAPI(title="Personal Finance Dashboard API")

class Trade(BaseModel):
    symbol: str
    qty: float
    price: float
    side: str  # "buy" or "sell"

class Holding(BaseModel):
    symbol: str
    qty: float
    price: float  # average cost basis

@app.get("/")
def root():
    return {"message": "Backend is running 🚀"}

@app.post("/trade")
def add_trade(trade: Trade):
    doc = trade.dict()
    doc["symbol"] = doc["symbol"].upper()

    # insert trade record
    trades_col.insert_one(doc)

    # also reflect into holdings (net quantity)
    sym = doc["symbol"]
    delta = doc["qty"] if doc["side"].lower() == "buy" else -doc["qty"]

    # update qty; keep existing average price if present
    current = holdings_col.find_one({"symbol": sym})
    if current:
        new_qty = float(current.get("qty", 0)) + float(delta)
        # optional: update avg price on buy using weighted average
        if doc["side"].lower() == "buy" and new_qty > 0:
            old_qty = float(current.get("qty", 0))
            old_price = float(current.get("price", 0))
            new_avg = ((old_qty * old_price) + (doc["qty"] * doc["price"])) / new_qty
        else:
            new_avg = float(current.get("price", 0))
        holdings_col.update_one(
            {"symbol": sym},
            {"$set": {"qty": max(new_qty, 0), "price": new_avg}}
        )
    else:
        # first time we see this symbol
        avg_price = doc["price"]
        holdings_col.insert_one({"symbol": sym, "qty": max(delta, 0), "price": avg_price})

    return {"status": "success", "trade": doc}

@app.post("/holding")
def add_holding(holding: Holding):
    doc = holding.dict()
    doc["symbol"] = doc["symbol"].upper()
    # upsert (add if not exists, otherwise set)
    holdings_col.update_one(
        {"symbol": doc["symbol"]},
        {"$set": {"qty": float(doc["qty"]), "price": float(doc["price"])}},
        upsert=True
    )
    return {"status": "success", "holding": doc}

@app.put("/holding/{symbol}")
def update_holding(symbol: str, holding: Holding):
    sym = symbol.upper()
    exists = holdings_col.find_one({"symbol": sym})
    if not exists:
        raise HTTPException(status_code=404, detail="Holding not found")
    holdings_col.update_one(
        {"symbol": sym},
        {"$set": {"qty": float(holding.qty), "price": float(holding.price)}}
    )
    return {"message": f"Holding {sym} updated"}

@app.get("/portfolio")
def get_portfolio():
    # read holdings from Mongo and fetch prices
    results = []
    for h in holdings_col.find({}):
        symbol = h["symbol"]
        qty = float(h.get("qty", 0))
        if qty <= 0:
            continue
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if hist.empty:
            continue
        current_price = float(hist["Close"].iloc[-1])
        results.append({
            "symbol": symbol,
            "qty": qty,
            "current_price": round(current_price, 2),
            "value": round(current_price * qty, 2)
        })
    return results
