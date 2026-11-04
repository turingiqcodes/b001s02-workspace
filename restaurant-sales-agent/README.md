# AI Restaurant Sales Intelligence Agent

An Orchestrator/Worker AI agent that analyzes **real** restaurant sales data with Pandas
and writes a business report using Groq (`openai/gpt-oss-20b`).

## 1. Big Picture

```mermaid
flowchart LR
    API[("🌐 Hugging Face<br/>Datasets API<br/>(free, no key)")]
    PD["🐼 Pandas<br/>Analysis"]
    ORC{{"🧠 Orchestrator"}}
    GROQ[["⚡ Groq LLM<br/>openai/gpt-oss-20b"]]
    REP["📄 report.md"]

    API -->|149,116 real sales| PD
    PD -->|numbers| ORC
    ORC <-->|prompts / answers| GROQ
    ORC --> REP
```

## 2. Orchestrator / Worker Architecture

```mermaid
flowchart TD
    MAIN["main.py<br/>(CLI entry point)"] --> ORC

    subgraph ORC["🧠 Orchestrator (orchestrator.py)"]
        direction TB
        S1["Step 1: Load data<br/>data_fetcher.py"] --> S2["Step 2: Pandas analysis<br/>analysis.py"]
        S2 --> S3["Step 3: Assign tasks"]
    end

    S3 --> GATHER

    subgraph GATHER["⚙️ asyncio.gather() - run at the same time"]
        direction LR
        W1["📊 Sales Analysis Worker<br/>revenue, items, branches"]
        W2["⏰ Peak Hours Worker<br/>hours, weekdays"]
    end

    W1 --> W3
    W2 --> W3
    W3["💡 Business Recommendations Worker<br/>(needs both results, runs after)"] --> FINAL
    FINAL["📄 Final Report<br/>printed + saved to report.md"]
```

## 3. Step-by-Step Run (Sequence)

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Main as main.py
    participant Orc as Orchestrator
    participant HF as Hugging Face API
    participant Sales as Sales Worker
    participant Peak as Peak Hours Worker
    participant Rec as Recommendations Worker
    participant Groq as Groq LLM

    User->>Main: python main.py
    Main->>Orc: run()
    Orc->>HF: download data (first run only)
    HF-->>Orc: sales.parquet (cached in data/)
    Orc->>Orc: Pandas analysis

    par Run concurrently (asyncio.gather)
        Orc->>Sales: sales numbers
        Sales->>Groq: analyse sales
        Groq-->>Sales: findings
    and
        Orc->>Peak: peak-hour numbers
        Peak->>Groq: analyse peak hours
        Groq-->>Peak: findings
    end

    Orc->>Rec: sales + peak findings
    Rec->>Groq: give recommendations
    Groq-->>Rec: recommendations
    Orc-->>Main: final report
    Main-->>User: print + save report.md
```

## 4. What the Pandas Analysis Calculates

```mermaid
mindmap
  root((analysis.py))
    revenue_summary
      Total revenue
      Transactions
      Avg transaction value
      Monthly revenue
    item_performance
      Top 5 best sellers
      Bottom 5 worst sellers
      Revenue by category
    branch_performance
      Revenue per branch
      Revenue share %
      Avg transaction per branch
    peak_hours
      Busiest 3 hours
      Quietest 3 hours
      Revenue by weekday
      Peak hour per branch
```

## 5. Data Source

```mermaid
flowchart LR
    A["Dataset: Maven Coffee Shop Sales<br/>149,116 transactions<br/>Jan - Jun 2023"] --> B1["🏪 Astoria"]
    A --> B2["🏪 Hell's Kitchen"]
    A --> B3["🏪 Lower Manhattan"]
```

The dataset is [`jason1966/agungpambudi_trends-product-coffee-shop-sales-revenue-dataset`](https://huggingface.co/datasets/jason1966/agungpambudi_trends-product-coffee-shop-sales-revenue-dataset).
The first run downloads about 2 MB and caches it in `data/`. Run with `--refresh` to download it again.

## 6. Project Files

```mermaid
flowchart TD
    ROOT["📁 restaurant-sales-agent/"]
    ROOT --> M["main.py - CLI entry point"]
    ROOT --> O["orchestrator.py - assigns tasks, builds report"]
    ROOT --> W["workers.py - the 3 worker agents"]
    ROOT --> AN["analysis.py - Pandas calculations"]
    ROOT --> D["data_fetcher.py - downloads + caches data"]
    ROOT --> L["llm.py - async Groq client"]
    ROOT --> T["tests/test_agent.py - 7 unit tests"]
    ROOT --> R["requirements.txt"]
    ROOT --> E[".env.example - copy to .env"]
```

## 7. Error Handling

```mermaid
flowchart TD
    START([Run agent]) --> K{GROQ_API_KEY set?}
    K -- No --> E1["❌ Clear error message, exit 1"]
    K -- Yes --> N{Data download OK?}
    N -- No --> E2["❌ Clear error message, exit 1"]
    N -- Yes --> C{Cached file readable?}
    C -- No --> E3["🗑️ Delete bad cache, error message<br/>(next run downloads again)"]
    C -- Yes --> WK{Did every worker succeed?}
    WK -- No --> E4["⚠️ Report still built;<br/>the failed section shows the reason"]
    WK -- Yes --> OK(["✅ Full report"])
```

## 8. Setup and Run

```mermaid
flowchart LR
    A["1. Create venv"] --> B["2. pip install"] --> C["3. Add GROQ_API_KEY to .env"] --> D["4. python main.py"] --> E["5. Open report.md"]
```

```bash
cd restaurant-sales-agent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then put your GROQ_API_KEY in .env

python main.py              # prints the report and saves report.md
python main.py --refresh    # downloads the data again
python -m pytest -q tests   # runs the tests
```

> If you already have the shared venv (`source /opt/turingiq-venv/bin/activate`), skip the
> venv and `pip install` steps. It already has every dependency.

## Notes

- In this dataset each row is one transaction line, so "average transaction" means the
  average value of one line.
- The LLM only gets the computed numbers. Its prompt tells it never to invent data.
