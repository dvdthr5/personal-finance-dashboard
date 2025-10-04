import streamlit as st
import requests
import pandas as pd
import plotly.express as px

BACKEND_URL = "http://localhost:8000"

st.set_page_config(page_title="📊 Personal Finance Dashboard", layout="wide")
st.title("📊 Personal Finance Dashboard")

# ----------------------------
# Portfolio Overview
# ----------------------------
st.subheader("Portfolio Overview")

portfolio_data = {}
try:
    resp = requests.get(f"{BACKEND_URL}/portfolio", timeout=10)
    if resp.status_code == 200:
        portfolio_data = resp.json()
    else:
        st.error(f"⚠️ Could not fetch portfolio ({resp.status_code})")
except Exception as e:
    st.error(f"❌ Backend not reachable: {e}")

holdings = portfolio_data.get("holdings", [])
realized_profit = portfolio_data.get("realized_profit", 0)

if holdings:
    df = pd.DataFrame(holdings)

    # Totals
    total_value = df["value"].sum()
    unrealized = df["unrealized_profit"].sum()
    total_profit = unrealized + realized_profit

    # Display metrics side by side
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Portfolio Value", f"${total_value:,.2f}")
    with col2:
        profit_color = "green" if total_profit >= 0 else "red"
        st.markdown(
            f"<h3>Total Gain/Loss: <span style='color:{profit_color}'>${total_profit:,.2f}</span></h3>",
            unsafe_allow_html=True
        )
    with col3:
        realized_color = "green" if realized_profit >= 0 else "red"
        st.markdown(
            f"<h3>Realized Gains: <span style='color:{realized_color}'>${realized_profit:,.2f}</span></h3>",
            unsafe_allow_html=True
        )

    # ----------------------------
    # Holdings + Pie Chart Layout
    # ----------------------------
    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("Holdings Detail")

        # Profit formatting ($ + %)
        df["Profit"] = df.apply(
            lambda row: (
                f"<span style='color:green'>+${(row['unrealized_profit']):,.2f} "
                f"(+{((row['current_price'] - row['avg_price']) / row['avg_price']) * 100:.2f}%)</span>"
                if row["unrealized_profit"] > 0
                else f"<span style='color:red'>${(row['unrealized_profit']):,.2f} "
                     f"({((row['current_price'] - row['avg_price']) / row['avg_price']) * 100:.2f}%)</span>"
            ),
            axis=1
        )

        # Reorder + rename columns
        df_display = df.rename(
            columns={
                "symbol": "Symbol",
                "qty": "Quantity",
                "avg_price": "Average Cost",
                "current_price": "Current Price",
                "value": "Value",
            }
        )[["Symbol", "Quantity", "Current Price", "Average Cost", "Value", "Profit"]]

        # Render table with scrollbars + smaller font
        st.markdown(
            """
            <style>
            .scrollable-table {
                overflow-x: auto;
                overflow-y: auto;
                max-height: 300px;
            }
            .scrollable-table table {
                font-size: 13px;
            }
            </style>
            """,
            unsafe_allow_html=True
        )
        st.markdown('<div class="scrollable-table">' +
                    df_display.to_html(escape=False, index=False) +
                    '</div>', unsafe_allow_html=True)

        # ----------------------------
        # Manage Holdings (directly under table)
        # ----------------------------
        with st.expander("⚙️ Manage Holdings"):
            symbol = st.selectbox("Select stock to edit", df["symbol"])
            stock = df[df["symbol"] == symbol].iloc[0]
            new_qty = st.number_input("Quantity", value=stock["qty"], step=1.0)
            new_price = st.number_input("Average Cost", value=stock["avg_price"], step=0.01)

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("💾 Save Changes", use_container_width=False):
                    payload = {"symbol": symbol, "qty": new_qty, "price": new_price}
                    r = requests.post(f"{BACKEND_URL}/holding", json=payload)
                    if r.status_code == 200:
                        st.success(f"Updated {symbol}")
                        st.rerun()
            with col_b:
                if st.button("🗑️ Delete Holding", use_container_width=False):
                    r = requests.delete(f"{BACKEND_URL}/holding/{symbol}")
                    if r.status_code == 200:
                        st.warning(f"Deleted {symbol}")
                        st.rerun()

    with col2:
        fig = px.pie(df, names="symbol", values="value", title="Portfolio Allocation")
        st.plotly_chart(fig, use_container_width=True)

else:
    st.warning("No holdings yet.")

# ----------------------------
# Add & Sell Holdings Section
# ----------------------------
st.subheader("Manage Holdings")
col1, col2 = st.columns(2)

# Add Holding
with col1:
    with st.expander("➕ Add a Holding"):
        with st.form("add_holding_form"):
            symbol = st.text_input("Stock Symbol (e.g. AAPL)").strip().upper()
            qty = st.number_input("Quantity", min_value=1.0, step=1.0)
            price = st.number_input("Price Bought At (per share)", min_value=0.01, step=0.01)
            submitted = st.form_submit_button("Save Holding")
            if submitted:
                if not symbol:
                    st.error("❌ Symbol cannot be empty")
                else:
                    payload = {"symbol": symbol, "qty": qty, "price": price}
                    r = requests.post(f"{BACKEND_URL}/holding", json=payload, timeout=10)
                    if r.status_code == 200:
                        st.success(f"✅ Holding saved: {symbol} ({qty} @ {price:.2f})")
                        st.rerun()
                    else:
                        st.error(f"❌ Error {r.status_code}: {r.json().get('detail', r.text)}")

# Sell Holding
with col2:
    with st.expander("💵 Sell a Holding"):
        if holdings:
            with st.form("sell_holding_form"):
                symbol = st.selectbox("Select Holding to Sell", [h["symbol"] for h in holdings])
                qty = st.number_input("Quantity to Sell", min_value=1.0, step=1.0)
                price_input = st.number_input("Sell Price (leave 0 to use market)", min_value=0.0, step=0.01)
                submitted = st.form_submit_button("Sell Holding")
                if submitted:
                    sell_price = None if price_input == 0 else price_input
                    payload = {"qty": qty, "price": sell_price}
                    r = requests.post(f"{BACKEND_URL}/holding/{symbol}/sell", json=payload, timeout=10)
                    if r.status_code == 200:
                        profit = r.json()["realized_profit"]
                        color = "green" if profit >= 0 else "red"
                        st.markdown(
                            f"✅ Sold {symbol} — Realized Profit: <span style='color:{color}'>${profit:,.2f}</span>",
                            unsafe_allow_html=True
                        )
                        st.rerun()
                    else:
                        st.error(f"❌ Error {r.status_code}: {r.json().get('detail', r.text)}")
        else:
            st.info("No holdings available to sell.")
