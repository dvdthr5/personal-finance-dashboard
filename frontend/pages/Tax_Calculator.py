import streamlit as st
import requests
import pandas as pd

# ----------------------------
# CONFIG
# ----------------------------
st.set_page_config(page_title="💰 Tax Calculator", layout="wide")
BACKEND_URL = "http://backend:8000"

# ----------------------------
# AUTH WALL
# ----------------------------
if "user_id" not in st.session_state or not st.session_state.user_id:
    st.warning("🔒 Please log in to access the Tax Calculator.")
    st.stop()

st.title("💰 Capital Gains Tax Calculator")
st.caption("Estimate after-tax returns by combining your realized gains with your income and state tax rates.")

# ----------------------------
# FETCH USER GAINS
# ----------------------------
realized_gains = 0.0
try:
    r = requests.get(f"{BACKEND_URL}/portfolio", params={"user_id": st.session_state.user_id}, timeout=10)
    if r.status_code == 200:
        data = r.json()
        realized_gains = float(data.get("realized_profit", 0) or 0)
    else:
        st.error(f"Backend error ({r.status_code}): {r.text}")
except Exception as e:
    st.error(f"⚠️ Could not connect to backend: {e}")

st.markdown(f"### 💵 Realized Gains: **${realized_gains:,.2f}**")

# ----------------------------
# TAX DATA (STATIC CACHE)
# ----------------------------
@st.cache_data
def get_state_tax_rates():
    # Approximate top marginal long-term capital gains tax rates per state (2025 est.)
    return {
        "Alabama": 5.0, "Alaska": 0.0, "Arizona": 4.5, "Arkansas": 5.5,
        "California": 13.3, "Colorado": 4.4, "Connecticut": 6.99, "Delaware": 6.6,
        "Florida": 0.0, "Georgia": 5.75, "Hawaii": 7.25, "Idaho": 5.8,
        "Illinois": 4.95, "Indiana": 3.15, "Iowa": 6.0, "Kansas": 5.7,
        "Kentucky": 4.5, "Louisiana": 4.25, "Maine": 7.15, "Maryland": 5.75,
        "Massachusetts": 5.0, "Michigan": 4.25, "Minnesota": 9.85, "Mississippi": 5.0,
        "Missouri": 4.95, "Montana": 6.75, "Nebraska": 6.84, "Nevada": 0.0,
        "New Hampshire": 0.0, "New Jersey": 10.75, "New Mexico": 5.9, "New York": 10.9,
        "North Carolina": 4.5, "North Dakota": 2.5, "Ohio": 3.99, "Oklahoma": 4.75,
        "Oregon": 9.9, "Pennsylvania": 3.07, "Rhode Island": 5.99, "South Carolina": 6.5,
        "South Dakota": 0.0, "Tennessee": 0.0, "Texas": 0.0, "Utah": 4.85,
        "Vermont": 8.75, "Virginia": 5.75, "Washington": 7.0, "West Virginia": 6.5,
        "Wisconsin": 7.65, "Wyoming": 0.0, "District of Columbia": 10.75
    }

state_tax_rates = get_state_tax_rates()

# ----------------------------
# INPUTS
# ----------------------------
col1, col2 = st.columns(2)

with col1:
    state = st.selectbox("Select your state of residence", sorted(state_tax_rates.keys()))
with col2:
    holding_period = st.radio("Holding Period", ["Short-Term (< 1 year)", "Long-Term (≥ 1 year)"])

st.markdown("---")

# ----------------------------
# FEDERAL TAX RATES
# ----------------------------
def get_federal_rate(holding_period: str, income: float):
    """Approximate 2025 US federal capital gains brackets."""
    if holding_period.startswith("Short"):
        # short-term = ordinary income rates
        if income <= 11600:
            return 10
        elif income <= 47150:
            return 12
        elif income <= 100525:
            return 22
        elif income <= 191950:
            return 24
        elif income <= 243725:
            return 32
        elif income <= 609350:
            return 35
        else:
            return 37
    else:
        # long-term = preferential capital gains
        if income <= 47025:
            return 0
        elif income <= 518900:
            return 15
        else:
            return 20

# ----------------------------
# SALARY INPUT + COMBINED INCOME
# ----------------------------
st.subheader("🧾 Income Information")

salary = st.number_input(
    "Enter your annual salary (without investment income)",
    min_value=0.0,
    step=1000.0,
    value=60000.0,
)

# Automatically include realized gains
total_income = salary + realized_gains

st.markdown(
    f"**Your total taxable income** (salary + realized gains): **${total_income:,.2f}**"
)

# ----------------------------
# TAX CALCULATION
# ----------------------------
federal_rate = get_federal_rate(holding_period, total_income)
state_rate = state_tax_rates[state]
total_tax_rate = federal_rate + state_rate

tax_owed = realized_gains * (total_tax_rate / 100)
after_tax = realized_gains - tax_owed

colA, colB, colC = st.columns(3)
with colA:
    st.metric("Federal Rate", f"{federal_rate:.1f}%")
with colB:
    st.metric("State Rate", f"{state_rate:.1f}%")
with colC:
    st.metric("Total Rate", f"{total_tax_rate:.1f}%")

st.markdown("---")

# ----------------------------
# RESULTS
# ----------------------------
st.subheader("📈 Tax Summary")
st.write(f"**Base Salary:** ${salary:,.2f}")
st.write(f"**Realized Gains:** ${realized_gains:,.2f}")
st.write(f"**Total Taxable Income:** ${total_income:,.2f}")
st.write(f"**Estimated Tax Owed on Gains:** ${tax_owed:,.2f}")
st.write(f"**After-Tax Gains:** ${after_tax:,.2f}")

if realized_gains <= 0:
    st.info("No realized gains to calculate taxes on yet.")
else:
    st.success(
        f"✅ After-tax return: ${after_tax:,.2f} — you keep {(after_tax / realized_gains) * 100:.2f}% of your gains."
    )

# ----------------------------
# NOTES
# ----------------------------
st.caption("""
*This calculator provides approximate estimates using 2025 U.S. federal and state tax brackets.
It assumes the realized gains are added to your taxable income and taxed at your marginal rate.
Always consult a qualified tax advisor for official calculations.*
""")