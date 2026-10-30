"""
The food search agent, built as a small LangGraph workflow:

    fetch_food  ->  filter_and_sort  ->  recommend (Groq LLM)

Each step (node) reads from and writes to a shared "state" dictionary.
"""

import os
from typing import TypedDict

from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph

from swiggy_api import search_food


class FoodState(TypedDict, total=False):
    # Inputs from the user
    food_name: str
    location: str
    category: str
    min_price: float
    max_price: float
    # Filled in by the agent
    results: list
    source: str
    recommendation: str


def fetch_food(state):
    """Step 1: get dishes from Swiggy (or sample data)."""
    results, source = search_food(state["food_name"], state["location"])
    return {"results": results, "source": source}


def filter_and_sort(state):
    """Step 2: apply category/budget filters, then sort.

    Sort order: highest rating first; for equal ratings, lowest price first.
    """
    results = state["results"]

    category = (state.get("category") or "").lower().strip()
    if category == "veg":
        results = [r for r in results if r["is_veg"]]
    elif category in ("non-veg", "nonveg", "non veg"):
        results = [r for r in results if not r["is_veg"]]
    elif category:
        results = [
            r for r in results
            if category in r["category"].lower() or category in r["name"].lower()
        ]

    min_price = state.get("min_price")
    if min_price is not None:
        results = [r for r in results if r["price"] >= min_price]

    max_price = state.get("max_price")
    if max_price is not None:
        results = [r for r in results if r["price"] <= max_price]

    results.sort(key=lambda r: (-r["rating"], r["price"]))
    return {"results": results}


def recommend(state):
    """Step 3: ask the Groq LLM to pick the best options."""
    results = state["results"]
    if not results:
        return {"recommendation": "No dishes matched your search. Try a wider budget or a different category."}

    top_items = "\n".join(
        f"- {r['name']} from {r['restaurant']}: Rs {r['price']:.0f}, "
        f"rating {r['rating']}, {'veg' if r['is_veg'] else 'non-veg'}"
        for r in results[:10]
    )
    prompt = (
        f"A user in {state['location']} is looking for '{state['food_name']}'.\n"
        f"Here are the matching dishes, sorted by rating then price:\n{top_items}\n\n"
        "In 3-4 short sentences, recommend the best pick overall, the best value-for-money "
        "pick, and one alternative. Only use the dishes listed above."
    )

    llm = ChatGroq(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        temperature=0.3,
    )
    try:
        answer = llm.invoke(prompt)
        return {"recommendation": answer.content}
    except Exception as error:
        return {"recommendation": f"(Could not get an AI recommendation: {error})"}


def build_agent():
    """Connect the steps into a graph and compile it."""
    graph = StateGraph(FoodState)
    graph.add_node("fetch_food", fetch_food)
    graph.add_node("filter_and_sort", filter_and_sort)
    graph.add_node("recommend", recommend)

    graph.set_entry_point("fetch_food")
    graph.add_edge("fetch_food", "filter_and_sort")
    graph.add_edge("filter_and_sort", "recommend")
    graph.add_edge("recommend", END)
    return graph.compile()
