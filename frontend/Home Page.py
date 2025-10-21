import os
import json
import time
import requests
import pandas as pd
import plotly.express as px
import streamlit as st


# ----------------------------
# CONFIG
# ----------------------------
st.set_page_config(page_title="🏠 Home Page", layout="wide")

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")  # Docker default
SESSION_FILE = ".session.json"

# ----------------------------
# HELPERS
# ----------------------------
def api_get(path: str, params: dict | None = None, timeout: int = 10):
    url = f"{BACKEND_URL}{path}"
    r = requests.get(url, params=params, timeout=timeout)
    return r

def api_post(path: str, json_body: dict | None = None, timeout: int = 10):
    url = f"{BACKEND_URL}{path}"
    r = requests.post(url, json=json_body, timeout=timeout)
    return r

def money(x):
    if pd.isna(x) or x is None:
        return "—"
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "—"

# ----------------------------
# SESSION MANAGEMENT
# ----------------------------
if "user_id" not in st.session_state:
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                saved = json.load(f)
                st.session_state.user_id = saved.get("user_id")
                st.session_state.username = saved.get("username")
        except Exception:
            st.session_state.user_id = None
            st.session_state.username = None
    else:
        st.session_state.user_id = None
        st.session_state.username = None

# ----------------------------
# SIDEBAR NAVIGATION
# ----------------------------
st.sidebar.title("Navigation")

if not st.session_state.user_id:
    st.sidebar.info("🔒 Log in to access your portfolio and tax calculator.")
else:
    st.sidebar.page_link("Home Page.py", label="🏠 Home Page")
    st.sidebar.page_link("pages/Tax_Calculator.py", label="💰 Tax Calculator")

# ----------------------------
# AUTH SCREENS
# ----------------------------
if not st.session_state.user_id:
    st.title("🔐 Login or Register")

    # health check
    try:
        r = api_get("/health", timeout=5)
        health_ok = r.status_code == 200
    except Exception:
        health_ok = False
    st.caption(f"Backend: {'🟢 online' if health_ok else '🔴 unreachable'}   ·   {BACKEND_URL}")

    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        with st.form("login_form", clear_on_submit=False):
            identifier = st.text_input("Username or Email", key="login_identifier")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Login")

        if submitted:
            if not identifier or not password:
                st.warning("Please fill in both fields.")
            else:
                try:
                    resp = api_post("/login", {"identifier": identifier, "password": password}, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        st.session_state.user_id = data["user_id"]
                        st.session_state.username = data["username"]
                        with open(SESSION_FILE, "w") as f:
                            json.dump({"user_id": data["user_id"], "username": data["username"]}, f)
                        st.success(f"✅ Welcome back, {data['username']}!")
                        time.sleep(0.6)
                        st.rerun()
                    else:
                        st.error(f"❌ {resp.json().get('detail', 'Invalid credentials')}")
                except Exception as e:
                    st.error(f"⚠️ Could not reach backend: {e}")

    with tab2:
        with st.form("register_form", clear_on_submit=False):
            username = st.text_input("Username")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted_reg = st.form_submit_button("Register")
        if submitted_reg:
            try:
                resp = api_post("/register", {"username": username, "email": email, "password": password}, timeout=10)
                if resp.status_code == 200:
                    st.success("✅ Registration successful! Please log in.")
                else:
                    st.error(f"❌ {resp.json().get('detail', 'Registration failed')}")
            except Exception as e:
                st.error(f"⚠️ Could not reach backend: {e}")

    st.stop()

# ----------------------------
# LOGOUT BUTTON
# ----------------------------
st.sidebar.markdown(f"👋 Logged in as **{st.session_state.username}**")
if st.sidebar.button("Logout"):
    st.session_state.user_id = None
    st.session_state.username = None
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    st.success("👋 Logged out successfully.")
    time.sleep(0.6)
    st.rerun()

# ----------------------------
# DASHBOARD
# ----------------------------
st.title(f"📊 Welcome back, {st.session_state.username}!")
st.subheader("Your Portfolio Overview")

portfolio_data = {}
try:
    resp = api_get("/portfolio", params={"user_id": st.session_state.user_id}, timeout=20)
    if resp.status_code == 200:
        portfolio_data = resp.json()
    else:
        st.error(f"⚠️ Could not fetch portfolio ({resp.status_code}) — {resp.text}")
except Exception as e:
    st.error(f"❌ Backend not reachable: {e}")

holdings = portfolio_data.get("holdings", []) or []
realized_profit = portfolio_data.get("realized_profit", 0) or 0

if not holdings:
    st.warning("No holdings yet.")
    st.stop()

df = pd.DataFrame(holdings)
expected_cols = ["symbol","qty","avg_price","current_price","value","unrealized_profit","warning"]
for c in expected_cols:
    if c not in df.columns:
        df[c] = None

for col in ["qty","avg_price","current_price","value","unrealized_profit"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# ----------------------------
# Summary Metrics
# ----------------------------
total_value = float(df["value"].fillna(0).sum())
unrealized = float(df["unrealized_profit"].fillna(0).sum())
total_profit = unrealized + float(realized_profit or 0)

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Portfolio Value", money(total_value))
with col2:
    color = "green" if total_profit >= 0 else "red"
    st.markdown(f"<h3>Total Gain/Loss: <span style='color:{color}'>{money(total_profit)}</span></h3>", unsafe_allow_html=True)
with col3:
    r_color = "green" if realized_profit >= 0 else "red"
    st.markdown(f"<h3>Realized Gains: <span style='color:{r_color}'>{money(realized_profit)}</span></h3>", unsafe_allow_html=True)

# ----------------------------
# Holdings Table + Pie Chart
# ----------------------------
colA, colB = st.columns([1,1])
with colA:
    st.subheader("Holdings Detail")

    def format_profit_row(row):
        profit = row.get("unrealized_profit")
        cp = row.get("current_price")
        avg = row.get("avg_price")
        if pd.isna(profit) or pd.isna(cp) or pd.isna(avg):
            return "<span style='color:gray'>—</span>"
        color = "green" if profit > 0 else "red"
        sign = "+" if profit > 0 else ""
        pct = ((cp - avg) / avg) * 100 if avg else 0
        return f"<span style='color:{color}'>{sign}{money(profit)} ({sign}{pct:.2f}%)</span>"

    df_display = df.copy()
    df_display.rename(columns={
        "symbol":"Symbol","qty":"Quantity","avg_price":"Average Cost",
        "current_price":"Current Price","value":"Value"
    }, inplace=True)

    for c in ["Average Cost","Current Price","Value"]:
        df_display[c] = df_display[c].apply(money)

    df_display["Profit"] = df.apply(format_profit_row, axis=1)
    df_display = df_display[["Symbol","Quantity","Current Price","Average Cost","Value","Profit"]]

    st.markdown(
        """
        <style>
        .scrollable-table { overflow-x: auto; overflow-y: auto; max-height: 360px; }
        .scrollable-table table { font-size: 13px; }
        </style>
        """,
        unsafe_allow_html=True
    )
    st.markdown('<div class="scrollable-table">' + df_display.to_html(escape=False, index=False) + '</div>', unsafe_allow_html=True)

    with st.expander("⚙️ Manage Holdings"):
        symbol = st.selectbox("Select stock to edit", df["symbol"])
        stock = df[df["symbol"] == symbol].iloc[0]
        new_qty = st.number_input("Quantity", value=float(stock["qty"] or 0), step=1.0)
        new_price = st.number_input("Average Cost", value=float(stock["avg_price"] or 0), step=0.01)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save Changes"):
                payload = {
                    "symbol": symbol,
                    "qty": float(new_qty),
                    "price": float(new_price),
                    "user_id": st.session_state.user_id
                }
                r = api_post("/holding", payload)
                if r.status_code == 200:
                    st.success(f"Updated {symbol}. It may take a moment to reflect.")
                    time.sleep(0.6)
                    st.rerun()
                else:
                    st.error(f"❌ {r.status_code}: {r.text}")
        with c2:
            if st.button("🗑️ Delete Holding"):
                try:
                    r = requests.delete(
                        f"{BACKEND_URL}/holding/{symbol}",
                        params={"user_id": st.session_state.user_id},
                        timeout=10
                    )
                    if r.status_code == 200:
                        st.warning(f"Deleted {symbol}")
                        time.sleep(0.6)
                        st.rerun()
                    else:
                        st.error(f"❌ {r.status_code}: {r.text}")
                except Exception as e:
                    st.error(f"⚠️ Could not reach backend: {e}")

with colB:
    alloc = df.dropna(subset=["value"])
    if not alloc.empty and alloc["value"].sum() > 0:
        fig = px.pie(alloc, names="symbol", values="value", title="Portfolio Allocation")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Waiting on live prices to render allocation pie.")

# ----------------------------
# ADD & SELL HOLDINGS
# ----------------------------
st.subheader("Manage Holdings")

left, right = st.columns(2)
with left:
    with st.expander("➕ Add a Holding"):
        with st.form("add_holding_form", clear_on_submit=False):
            symbol_in = st.text_input("Stock Symbol (e.g. AAPL)").strip().upper()
            qty_in = st.number_input("Quantity", min_value=0.0, step=1.0, value=0.0)
            price_in = st.number_input("Price Bought At (per share)", min_value=0.0, step=0.01, value=0.0)
            submitted = st.form_submit_button("Save Holding")

        if submitted:
            if not symbol_in:
                st.error("❌ Symbol cannot be empty")
            elif qty_in <= 0 or price_in <= 0:
                st.error("❌ Quantity and Price must be greater than zero")
            else:
                payload = {
                    "symbol": symbol_in,
                    "qty": float(qty_in),
                    "price": float(price_in),
                    "user_id": st.session_state.user_id
                }
                try:
                    r = api_post("/holding", payload, timeout=10)
                    if r.status_code == 200:
                        st.success(f"✅ Holding saved: {symbol_in} ({qty_in} @ {price_in:.2f})")
                        time.sleep(0.6)
                        st.rerun()
                    else:
                        st.error(f"❌ Error {r.status_code}: {r.text}")
                except Exception as e:
                    st.error(f"⚠️ Could not reach backend: {e}")

with right:
    with st.expander("💵 Sell a Holding"):
        if isinstance(holdings, list) and len(holdings) > 0:
            with st.form("sell_holding_form"):
                symbols_opts = [h["symbol"] for h in holdings if "symbol" in h]
                symbol_sel = st.selectbox("Select Holding to Sell", symbols_opts)
                qty_sell = st.number_input("Quantity to Sell", min_value=0.0, step=1.0, value=0.0)
                price_input = st.number_input("Sell Price (leave 0 to use market)", min_value=0.0, step=0.01, value=0.0)
                submitted_sell = st.form_submit_button("Sell Holding")
                if submitted_sell:
                    if qty_sell <= 0:
                        st.error("❌ Quantity must be greater than zero")
                    else:
                        sell_price = None if price_input == 0 else float(price_input)
                        payload = {"qty": float(qty_sell), "price": sell_price, "user_id": st.session_state.user_id}
                        try:
                            r = api_post(f"/holding/{symbol_sel}/sell", payload)
                            if r.status_code == 200:
                                st.success(f"✅ Sale of {symbol_sel} queued for processing.")
                                time.sleep(0.6)
                                st.rerun()
                            else:
                                st.error(f"❌ Error {r.status_code}: {r.text}")
                        except Exception as e:
                            st.error(f"⚠️ Could not reach backend: {e}")
        else:
            st.info("No holdings available to sell.")

# ----------------------------
# SALE HISTORY SECTION
# ----------------------------
st.markdown("---")
st.subheader("📜 Sale History")

try:
    r = api_get("/sales_history", params={"user_id": st.session_state.user_id}, timeout=10)
    if r.status_code == 200:
        sales_data = r.json().get("sales", [])
        if sales_data:
            sales_df = pd.DataFrame(sales_data)
            sales_df["timestamp"] = pd.to_datetime(sales_df["timestamp"])
            sales_df["profit_display"] = sales_df["profit"].apply(
                lambda x: f"${x:,.2f}" if x >= 0 else f"-${abs(x):,.2f}"
            )

            # Display table
            st.markdown(
                """
                <style>
                .scrollable-sales { overflow-x: auto; overflow-y: auto; max-height: 300px; }
                .scrollable-sales table { font-size: 13px; }
                </style>
                """,
                unsafe_allow_html=True
            )

            st.markdown('<div class="scrollable-sales">' + 
                        sales_df[["symbol", "qty", "buy_price", "sell_price", "profit_display", "timestamp"]]
                        .rename(columns={
                            "symbol": "Symbol",
                            "qty": "Qty",
                            "buy_price": "Buy Price",
                            "sell_price": "Sell Price",
                            "profit_display": "Profit",
                            "timestamp": "Date"
                        }).to_html(escape=False, index=False) +
                        '</div>', unsafe_allow_html=True)

            # Delete control
            sale_to_delete = st.selectbox(
                "Select a sale to delete (if entered by mistake):",
                options=[f"{row['symbol']} - {row['timestamp']}" for _, row in sales_df.iterrows()]
            )

            if st.button("🗑️ Delete Selected Sale"):
                try:
                    selected_index = [
                        f"{row['symbol']} - {row['timestamp']}"
                        for _, row in sales_df.iterrows()
                    ].index(sale_to_delete)
                    selected_id = sales_df.iloc[selected_index]["id"]
                    del_req = requests.delete(
                        f"{BACKEND_URL}/sales_history/{selected_id}",
                        params={"user_id": st.session_state.user_id},
                        timeout=10
                    )
                    if del_req.status_code == 200:
                        st.warning("✅ Sale deleted successfully.")
                        st.rerun()
                    else:
                        st.error(f"❌ {del_req.status_code}: {del_req.text}")
                except Exception as e:
                    st.error(f"⚠️ Could not delete sale: {e}")

        else:
            st.info("No sales recorded yet.")
    else:
        st.error(f"❌ Backend error {r.status_code}: {r.text}")
except Exception as e:
    st.error(f"⚠️ Could not reach backend: {e}")