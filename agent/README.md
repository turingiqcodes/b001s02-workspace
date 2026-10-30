# Swiggy Food Search Agent

A beginner-friendly food search agent that uses LangGraph and a free Groq LLM.

A web page asks for a food name, a location, and optionally a category and budget. It then shows matching
dishes with prices and ratings, sorted by **highest rating first, then lowest price**.
At the end, the Groq LLM recommends the best options.

## How it works

```
fetch_food  ->  filter_and_sort  ->  recommend (Groq LLM)
```

| File | What it does |
|------|--------------|
| `swiggy_api.py` | Converts the location to coordinates (OpenStreetMap), then searches Swiggy. Falls back to sample data if that fails. |
| `agent.py` | The LangGraph workflow: fetch, then filter and sort, then the LLM recommendation. |
| `app.py` | Streamlit web page: search form, AI recommendation, and a sortable results table. |

> **Note:** Swiggy has no official public API. This project calls the endpoints the Swiggy website uses.
> They can change or block requests at any time. When that happens, the app automatically uses
> built-in sample data so it still works.

## Setup

```bash
cd agent
source /opt/turingiq-venv/bin/activate
pip install -r requirements.txt   # already installed in turingiq-venv
cp .env.example .env      # then put your Groq key in .env
streamlit run app.py
```

Then open http://localhost:8501 in your browser.

Get a free Groq API key at https://console.groq.com/keys.
