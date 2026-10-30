"""Download real restaurant (coffee shop) sales data from the free Hugging Face Datasets API.

Dataset: Maven Analytics "Coffee Shop Sales" - 149,116 real transactions from
3 New York store locations (Jan-Jun 2023). No API key is needed.
"""

from pathlib import Path

import pandas as pd
import requests

DATASET = "jason1966/agungpambudi_trends-product-coffee-shop-sales-revenue-dataset"
PARQUET_API = "https://datasets-server.huggingface.co/parquet"
CACHE_FILE = Path(__file__).parent / "data" / "sales.parquet"

REQUIRED_COLUMNS = [
    "transaction_id", "transaction_date", "transaction_time", "transaction_qty",
    "store_location", "unit_price", "product_category", "product_type", "product_detail",
]


class DataFetchError(Exception):
    """Raised when the sales data cannot be downloaded or is invalid."""


def _download_parquet() -> None:
    """Ask the API where the data file lives, then download it to the cache."""
    try:
        resp = requests.get(PARQUET_API, params={"dataset": DATASET}, timeout=30)
        resp.raise_for_status()
        files = resp.json().get("parquet_files", [])
        if not files:
            raise DataFetchError("The API returned no data files for the dataset.")

        data = requests.get(files[0]["url"], timeout=120)
        data.raise_for_status()
    except requests.RequestException as e:
        raise DataFetchError(f"Could not download sales data: {e}") from e

    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_bytes(data.content)


def load_sales_data(refresh: bool = False) -> pd.DataFrame:
    """Return the sales data as a clean DataFrame.

    The file is downloaded once and cached in data/. Pass refresh=True to download again.
    """
    if refresh or not CACHE_FILE.exists():
        print("Downloading sales data from the Hugging Face Datasets API...")
        _download_parquet()

    try:
        df = pd.read_parquet(CACHE_FILE)
    except Exception as e:
        CACHE_FILE.unlink(missing_ok=True)  # Corrupt cache: delete so the next run re-downloads
        raise DataFetchError(f"Could not read the cached data file: {e}") from e

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataFetchError(f"Data is missing expected columns: {missing}")

    # Add the columns the analysis needs
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["hour"] = pd.to_datetime(df["transaction_time"].astype(str), format="%H:%M:%S").dt.hour
    df["weekday"] = df["transaction_date"].dt.day_name()
    df["revenue"] = df["transaction_qty"] * df["unit_price"]
    return df
