# AI Restaurant Sales Intelligence Agent

An Orchestrator/Worker AI agent that analyzes **real** restaurant sales data with Pandas
and writes a business report using Groq (`openai/gpt-oss-20b`).

## Data source

The agent uses [Maven Analytics' Coffee Shop Sales dataset](https://huggingface.co/datasets/jason1966/agungpambudi_trends-product-coffee-shop-sales-revenue-dataset). It downloads the data from the free
**Hugging Face Datasets API**, which needs no API key. The dataset has 149,116 real transactions
from 3 New York branches (Astoria, Hell's Kitchen and Lower Manhattan), covering
January to June 2023. The first run downloads about 2 MB and caches it in `data/`.

## How it works

```
                 main.py
                    |
              Orchestrator  ── loads data (data_fetcher.py)
                    |        ── runs Pandas analysis (analysis.py)
        +-----------+-----------+
        |   asyncio.gather()    |        run at the same time
  Sales Analysis         Peak Hours
     Worker                Worker
        +-----------+-----------+
                    |
        Business Recommendations         needs both results, so it runs after
                 Worker
                    |
           Final report -> report.md
```

| File | Purpose |
|---|---|
| `data_fetcher.py` | Downloads and caches the dataset, then adds `hour`, `weekday` and `revenue` columns |
| `analysis.py` | Pandas: revenue, best/worst items, branch performance, peak hours |
| `llm.py` | Async Groq client and API-key check |
| `workers.py` | The 3 worker agents |
| `orchestrator.py` | Assigns tasks, runs workers concurrently, builds the report |
| `main.py` | Command-line entry point |
| `tests/test_agent.py` | Unit tests (no network or API key needed) |

## Setup

```bash
cd restaurant-sales-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your GROQ_API_KEY in .env
```

## Run

```bash
python main.py              # prints the report and saves report.md
python main.py --refresh    # downloads the data again
python -m pytest -q tests   # runs the tests
```

## Error handling

- **Missing API key:** shows a clear message and exits with code 1.
- **Network or API download failure:** shows a clear message and exits with code 1.
- **Corrupt cached data:** deletes the cache file so the next run downloads it again.
- **One worker fails (for example, a Groq rate limit):** the other sections are still produced, and
  the failed section says why it failed.

## Notes

- In this dataset each row is one transaction line, so "average transaction" means the
  average value of one line.
- The LLM only gets the computed numbers. Its prompt tells it never to invent data.
