"""Tests for the analysis and error handling.

The small DataFrame below is only a test fixture used to check the math;
the app itself always uses the real dataset from the API.
"""

import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import analysis
import data_fetcher
import llm
import orchestrator


@pytest.fixture
def df():
    rows = [
        # id, date, time, qty, store, price, category, item
        (1, "2023-01-02", "08:10:00", 2, "A", 3.0, "Coffee", "Latte"),
        (2, "2023-01-02", "08:30:00", 1, "B", 3.0, "Coffee", "Latte"),
        (3, "2023-01-03", "09:00:00", 1, "A", 1.0, "Bakery", "Scone"),
        (4, "2023-02-04", "17:00:00", 1, "B", 5.0, "Tea", "Chai"),
    ]
    frame = pd.DataFrame(rows, columns=[
        "transaction_id", "transaction_date", "transaction_time", "transaction_qty",
        "store_location", "unit_price", "product_category", "product_detail",
    ])
    frame["transaction_date"] = pd.to_datetime(frame["transaction_date"])
    frame["hour"] = pd.to_datetime(frame["transaction_time"], format="%H:%M:%S").dt.hour
    frame["weekday"] = frame["transaction_date"].dt.day_name()
    frame["revenue"] = frame["transaction_qty"] * frame["unit_price"]
    return frame


def test_revenue_summary(df):
    result = analysis.revenue_summary(df)
    assert result["total_revenue"] == 15.0
    assert result["total_transactions"] == 4
    assert result["monthly_revenue"] == {"2023-01": 10.0, "2023-02": 5.0}


def test_item_performance(df):
    result = analysis.item_performance(df, top_n=2)
    assert result["best_sellers"][0]["product_detail"] == "Latte"
    assert result["worst_sellers"][0]["product_detail"] == "Scone"


def test_branch_performance(df):
    branches = analysis.branch_performance(df)
    assert [b["store_location"] for b in branches] == ["B", "A"]
    assert branches[0]["revenue"] == 8.0


def test_peak_hours(df):
    result = analysis.peak_hours(df)
    assert result["top_3_hours"][0] == 8
    assert result["peak_hour_per_branch"] == {"A": 8, "B": 17}


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="GROQ_API_KEY"):
        llm.get_client()


def test_network_error_becomes_data_fetch_error(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("no internet")

    monkeypatch.setattr(data_fetcher, "CACHE_FILE", tmp_path / "sales.parquet")
    monkeypatch.setattr(data_fetcher.requests, "get", fail)
    with pytest.raises(data_fetcher.DataFetchError, match="no internet"):
        data_fetcher.load_sales_data()


def test_one_failed_worker_does_not_stop_the_report(monkeypatch, df):
    """If the Sales worker fails, the report is still built with the other sections."""
    async def fake_ask_llm(client, system_prompt, user_prompt):
        if "sales data" in user_prompt:
            raise llm.LLMError("rate limit")
        return "ok"

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("workers.ask_llm", fake_ask_llm)
    monkeypatch.setattr(orchestrator, "load_sales_data", lambda refresh: df)

    report = asyncio.run(orchestrator.Orchestrator().run())
    assert "_This section failed: rate limit_" in report
    assert "## Peak Hours Analysis\n\nok" in report
    assert "## Business Recommendations\n\nok" in report
