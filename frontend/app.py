import streamlit as st
import requests
import pandas as pd

BACKEND_URL = "http://localhost:8000"  # change to LAN/cloud URL if needed

st.set_page_config(page_title="📊 Personal Finance Dashboard", layout="wide")

# 🔄 Auto-refresh every 10 seconds
st_autorefresh = st.autorefresh(interval=10000, key="refresh")

st.title("📊 Personal Finance Dashboard")

# ----------------------------
# Portfolio Overview
# ----------------------------
st.subheader("Portfolio Overview")

try:
    resp = requests.get(f"{BACKEND_URL}/portfolio")
    if resp.status_code == 200:
        portfolio = resp.json()
        if portfolio:
            total_value = sum(item["value"] for item in portfolio)
            st.metric("Total Portfolio Value", f"${total_value:,.2f}")
            df = pd.DataFrame(portfolio)
            st.dataframe(df, use_container_width=True)
        else:
            st.warning("No holdings yet.")
    else:
        st.error("⚠️ Could not fetch portfolio")
except Exception as e:
    st.error(f"❌ Backend not reachable: {e}")

# ----------------------------
# Trade & Holding Controls
# ----------------------------
st.subheader("Manage Portfolio")

with st.expander("➕ Add Trade"):
    with st.form("add_trade_form"):
        symbol = st.text_input("Stock Symbol (e.g. AAPL)").upper()
        qty = st.number_input("Quantity", min_value=0.0, step=1.0)
        price = st.number_input("Price per share", min_value=0.0, step=0.01)
        side = st.selectbox("Type", ["buy", "sell"])
        submitted = st.form_submit_button("Add Trade")

        if submitted:
            payload = {"symbol": symbol, "qty": qty, "price": price, "side": side}
            try:
                r = requests.post(f"{BACKEND_URL}/trade", json=payload)
                if r.status_code == 200:
                    st.success(f"✅ Trade added: {symbol} ({qty} @ {price:.2f})")
                    st.rerun()  # 🔥 refresh immediately
                else:
                    st.error(f"❌ Error {r.status_code}: {r.text}")
            except Exception as e:
                st.error(f"❌ Backend error: {e}")

with st.expander("📦 Add Existing Holding"):
    with st.form("add_holding_form"):
        symbol = st.text_input("Stock Symbol (e.g. TSLA)").upper()
        qty = st.number_input("Quantity", min_value=0.0, step=1.0, key="holding_qty")
        price = st.number_input("Average Price per share", min_value=0.0, step=0.01, key="holding_price")
        submitted = st.form_submit_button("Add Holding")

        if submitted:
            payload = {"symbol": symbol, "qty": qty, "price": price}
            try:
                r = requests.post(f"{BACKEND_URL}/holding", json=payload)
                if r.status_code == 200:
                    st.success(f"✅ Holding added: {symbol} ({qty} @ {price:.2f})")
                    st.rerun()  # 🔥 refresh immediately
                else:
                    st.error(f"❌ Error {r.status_code}: {r.text}")
            except Exception as e:
                st.error(f"❌ Backend error: {e}")
