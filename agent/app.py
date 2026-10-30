"""
Swiggy Food Search Agent - web page.

    streamlit run app.py
"""

import os

import streamlit as st
from dotenv import load_dotenv

from agent import build_agent

load_dotenv()

st.set_page_config(page_title="Swiggy Food Search", page_icon="🍽️", layout="wide")
st.title("🍽️ Swiggy Food Search Agent")
st.caption("Find dishes near you, sorted by highest rating and lowest price.")

if not os.getenv("GROQ_API_KEY"):
    st.error("Please set GROQ_API_KEY in a .env file (see .env.example).")
    st.stop()


@st.cache_resource
def get_agent():
    """Build the LangGraph agent once and reuse it."""
    return build_agent()


# ---------------------------------------------------------------------------
# Search form
# ---------------------------------------------------------------------------

with st.form("search"):
    col1, col2 = st.columns(2)
    food_name = col1.text_input("Food name *", placeholder="e.g. biryani")
    location = col2.text_input("Location *", placeholder="e.g. Koramangala, Bangalore")

    col3, col4, col5 = st.columns(3)
    category = col3.text_input("Category (optional)", placeholder="e.g. Veg, Non-veg, Pizza")
    min_price = col4.number_input("Min price ₹ (optional)", min_value=0, step=50)
    max_price = col5.number_input("Max price ₹ (optional, 0 = no limit)", min_value=0, step=50)

    submitted = st.form_submit_button("Search", type="primary")

if not submitted:
    st.stop()

if not food_name.strip() or not location.strip():
    st.warning("Please enter both a food name and a location.")
    st.stop()

if max_price and min_price > max_price:
    st.warning("Min price cannot be more than max price.")
    st.stop()

# ---------------------------------------------------------------------------
# Run the agent
# ---------------------------------------------------------------------------

with st.spinner("Searching Swiggy and asking the AI..."):
    state = get_agent().invoke({
        "food_name": food_name.strip(),
        "location": location.strip(),
        "category": category.strip(),
        "min_price": min_price or None,   # 0 means "no limit"
        "max_price": max_price or None,
    })

results = state["results"]

if state["source"] == "sample":
    st.warning("Live Swiggy search failed, so these results come from sample data.")

st.subheader("🤖 AI recommendation")
st.info(state["recommendation"])

st.subheader(f"Results ({len(results)} dishes)")
if not results:
    st.write("No dishes found. Try a wider budget or a different category.")
    st.stop()

table = [
    {
        "Dish": r["name"],
        "Restaurant": r["restaurant"],
        "Price (₹)": r["price"],
        "Rating": r["rating"] or None,
        "Type": "🟢 Veg" if r["is_veg"] else "🔴 Non-veg",
        "Delivery (min)": r["delivery_time"],
        "Category": r["category"],
    }
    for r in results
]

st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Price (₹)": st.column_config.NumberColumn(format="₹%d"),
        "Rating": st.column_config.NumberColumn(format="⭐ %.1f"),
    },
)
