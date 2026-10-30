"""The Orchestrator: loads the data, runs the Pandas analysis, assigns tasks to
the workers, and builds the final report."""

import asyncio
from datetime import datetime

import analysis
from data_fetcher import DATASET, load_sales_data
from llm import MODEL, get_client
from workers import PeakHoursWorker, RecommendationsWorker, SalesAnalysisWorker


class Orchestrator:
    def __init__(self):
        self.client = get_client()
        self.sales_worker = SalesAnalysisWorker(self.client)
        self.peak_worker = PeakHoursWorker(self.client)
        self.recommend_worker = RecommendationsWorker(self.client)

    async def run(self, refresh_data: bool = False) -> str:
        print("Step 1/4: Loading sales data")
        df = load_sales_data(refresh=refresh_data)
        print(f"  Loaded {len(df):,} transactions")

        print("Step 2/4: Running Pandas analysis")
        revenue = analysis.revenue_summary(df)
        items = analysis.item_performance(df)
        branches = analysis.branch_performance(df)
        peaks = analysis.peak_hours(df)

        # Sales and Peak Hours do not depend on each other, so they run at the same time.
        print("Step 3/4: Running Sales + Peak Hours workers concurrently")
        sales_task = {"revenue": revenue, "items": items, "branches": branches}
        peak_task = {k: v for k, v in peaks.items() if k != "hourly"} | {
            "transactions_by_hour": {r["hour"]: r["transactions"] for r in peaks["hourly"]}
        }
        results = await asyncio.gather(
            self.sales_worker.run(sales_task),
            self.peak_worker.run(peak_task),
            return_exceptions=True,  # One failed worker should not hide the other's result
        )
        sales_text, peak_text = [
            r if isinstance(r, str) else f"_This section failed: {r}_" for r in results
        ]

        # Recommendations need both findings, so this worker runs after them.
        print("Step 4/4: Running Business Recommendations worker")
        try:
            rec_text = await self.recommend_worker.run(
                {"sales_findings": sales_text, "peak_hour_findings": peak_text}
            )
        except Exception as e:
            rec_text = f"_This section failed: {e}_"

        return self.build_report(revenue, items, branches, peaks, sales_text, peak_text, rec_text)

    @staticmethod
    def build_report(revenue, items, branches, peaks, sales_text, peak_text, rec_text) -> str:
        best = items["best_sellers"][0]
        worst = items["worst_sellers"][0]
        top_branch = branches[0]

        branch_rows = "\n".join(
            f"| {b['store_location']} | ${b['revenue']:,.2f} | {b['transactions']:,} "
            f"| ${b['avg_transaction_value']:.2f} | {b['revenue_share_pct']:.1f}% |"
            for b in branches
        )
        hours = ", ".join(f"{h}:00" for h in peaks["top_3_hours"])

        return f"""# Restaurant Sales Intelligence Report

_Generated {datetime.now():%Y-%m-%d %H:%M} | Data: Hugging Face `{DATASET}` | Model: `{MODEL}`_

## Key Numbers

- **Period:** {revenue['date_range']}
- **Total revenue:** ${revenue['total_revenue']:,.2f} from {revenue['total_transactions']:,} transactions
- **Average transaction:** ${revenue['avg_transaction_value']:.2f}
- **Best seller:** {best['product_detail']} (${best['revenue']:,.2f})
- **Worst seller:** {worst['product_detail']} (${worst['revenue']:,.2f})
- **Top branch:** {top_branch['store_location']} ({top_branch['revenue_share_pct']:.1f}% of revenue)
- **Peak hours:** {hours}

## Branch Performance

| Branch | Revenue | Transactions | Avg Transaction | Share |
|---|---|---|---|---|
{branch_rows}

## Sales Analysis

{sales_text}

## Peak Hours Analysis

{peak_text}

## Business Recommendations

{rec_text}
"""
