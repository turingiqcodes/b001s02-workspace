"""Pandas analysis functions. Each returns plain Python dicts/lists so the
results are easy to print and to send to the LLM."""

import pandas as pd

WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def revenue_summary(df: pd.DataFrame) -> dict:
    """Overall revenue numbers and revenue per month."""
    monthly = df.groupby(df["transaction_date"].dt.to_period("M"))["revenue"].sum()
    return {
        "total_revenue": round(df["revenue"].sum(), 2),
        "total_transactions": int(df["transaction_id"].nunique()),
        "total_items_sold": int(df["transaction_qty"].sum()),
        "avg_transaction_value": round(df["revenue"].sum() / df["transaction_id"].nunique(), 2),
        "date_range": f"{df['transaction_date'].min():%Y-%m-%d} to {df['transaction_date'].max():%Y-%m-%d}",
        "monthly_revenue": {str(m): round(v, 2) for m, v in monthly.items()},
    }


def item_performance(df: pd.DataFrame, top_n: int = 5) -> dict:
    """Best and worst selling items (by revenue) and revenue by category."""
    items = (
        df.groupby("product_detail")
        .agg(revenue=("revenue", "sum"), quantity=("transaction_qty", "sum"))
        .sort_values("revenue", ascending=False)
        .round(2)
    )
    categories = df.groupby("product_category")["revenue"].sum().sort_values(ascending=False).round(2)
    return {
        "best_sellers": items.head(top_n).reset_index().to_dict("records"),
        "worst_sellers": items.tail(top_n).iloc[::-1].reset_index().to_dict("records"),
        "revenue_by_category": categories.to_dict(),
    }


def branch_performance(df: pd.DataFrame) -> list:
    """Revenue, transactions and average ticket for each store location."""
    branches = (
        df.groupby("store_location")
        .agg(
            revenue=("revenue", "sum"),
            transactions=("transaction_id", "nunique"),
            items_sold=("transaction_qty", "sum"),
        )
        .sort_values("revenue", ascending=False)
    )
    branches["avg_transaction_value"] = branches["revenue"] / branches["transactions"]
    branches["revenue_share_pct"] = branches["revenue"] / branches["revenue"].sum() * 100
    return branches.round(2).reset_index().to_dict("records")


def peak_hours(df: pd.DataFrame) -> dict:
    """Busiest hours and days, overall and per branch."""
    by_hour = df.groupby("hour").agg(
        revenue=("revenue", "sum"), transactions=("transaction_id", "nunique")
    )
    by_day = df.groupby("weekday")["revenue"].sum().reindex(WEEKDAY_ORDER).round(2)
    branch_peak = df.groupby(["store_location", "hour"])["revenue"].sum().groupby(level=0).idxmax()

    busiest = by_hour.sort_values("transactions", ascending=False)
    return {
        "hourly": by_hour.round(2).reset_index().to_dict("records"),
        "top_3_hours": [int(h) for h in busiest.index[:3]],
        "quietest_3_hours": [int(h) for h in busiest.index[-3:][::-1]],
        "revenue_by_weekday": by_day.to_dict(),
        "peak_hour_per_branch": {store: int(hour) for store, hour in branch_peak},
    }
