from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
import os
import yfinance as yf

load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "finance")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
holdings_col = db["holdings"]
realized_col = db["realized_gains"]

holdings_col.create_index([("symbol", ASCENDING)], unique=True)

app = FastAPI(title="Personal Finance Dashboard API")

class Holding(BaseModel):
    symbol: str
    qty: float
    price: float  # average cost basis

class SellRequest(BaseModel):
    qty: float
    price: float | None = None  # optional sell price

@app.delete("/holding/{symbol}")
def delete_holding(symbol: str):
    sym = symbol.upper()
    result = holdings_col.delete_one({"symbol": sym})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Holding not found")
    return {"status": "success", "message": f"Holding {sym} deleted"}

@app.post("/holding/{symbol}/sell")
def sell_holding(symbol: str, request: SellRequest):
    sym = symbol.upper()
    h = holdings_col.find_one({"symbol": sym})
    if not h:
        raise HTTPException(status_code=404, detail="Holding not found")

    qty_to_sell = float(request.qty)
    if qty_to_sell <= 0 or qty_to_sell > h["qty"]:
        raise HTTPException(status_code=400, detail="Invalid sell quantity")

    buy_price = float(h["price"])

    # get sell price (user input or fetch current)
    if request.price:
        sell_price = float(request.price)
    else:
        ticker = yf.Ticker(sym)
        hist = ticker.history(period="1d")
        if hist.empty:
            raise HTTPException(status_code=400, detail="Could not fetch price")
        sell_price = float(hist["Close"].iloc[-1])

    profit = (sell_price - buy_price) * qty_to_sell

    # record realized gain
    realized_col.insert_one({
        "symbol": sym,
        "qty": qty_to_sell,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "profit": profit
    })

    # update holdings
    new_qty = h["qty"] - qty_to_sell
    if new_qty <= 0:
        holdings_col.delete_one({"symbol": sym})
    else:
        holdings_col.update_one({"symbol": sym}, {"$set": {"qty": new_qty}})

    return {"status": "success", "realized_profit": profit}

@app.get("/portfolio")
def get_portfolio():
    results = []
    holdings = list(holdings_col.find({"qty": {"$gt": 0}}))

    symbols = [h["symbol"] for h in holdings]
    prices = {}
    if symbols:
        try:
            prices = yf.download(symbols, period="1d")["Close"].iloc[-1].to_dict()
        except Exception:
            prices = {}

    for h in holdings:
        symbol = h["symbol"]
        qty = float(h["qty"])
        avg_price = float(h["price"])
        current_price = prices.get(symbol)
        if current_price:
            value = round(current_price * qty, 2)
            profit = round((current_price - avg_price) * qty, 2)
            results.append({
                "symbol": symbol,
                "qty": qty,
                "avg_price": avg_price,
                "current_price": round(current_price, 2),
                "value": value,
                "unrealized_profit": profit
            })

    # compute realized profit
    realized = sum(x["profit"] for x in realized_col.find({}))

    return {"holdings": results, "realized_profit": realized}
