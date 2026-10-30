"""The three worker agents. Each worker gets the numbers it needs from the
Orchestrator, asks the LLM to interpret them, and returns a Markdown section."""

import json

from llm import ask_llm

SYSTEM_PROMPT = (
    "You are a restaurant business analyst. Use ONLY the numbers given to you; "
    "never invent data. Answer in concise Markdown with short bullet points. "
    "Currency is US dollars."
)


class Worker:
    """Base worker: subclasses set a name and a task prompt."""

    name = "Worker"
    task = ""

    def __init__(self, client):
        self.client = client

    async def run(self, data: dict) -> str:
        print(f"  [{self.name}] started")
        prompt = f"{self.task}\n\nData (JSON):\n{json.dumps(data, indent=2, default=str)}"
        answer = await ask_llm(self.client, SYSTEM_PROMPT, prompt)
        print(f"  [{self.name}] finished")
        return answer


class SalesAnalysisWorker(Worker):
    name = "Sales Analysis Agent"
    task = (
        "Analyse this sales data. Cover: total revenue and monthly trend, "
        "best-selling and worst-selling items, category mix, and how the branches "
        "compare (revenue share and average transaction value). Under 250 words."
    )


class PeakHoursWorker(Worker):
    name = "Peak Hours Agent"
    task = (
        "Analyse when customers buy. Cover: the busiest and quietest hours, "
        "the strongest and weakest weekdays, and any differences in peak hour "
        "between branches. Under 200 words."
    )


class RecommendationsWorker(Worker):
    name = "Business Recommendations Agent"
    task = (
        "You are given the findings of a sales analyst and a peak-hours analyst. "
        "Give 5 to 7 specific, actionable recommendations (menu, staffing, "
        "promotions, branch strategy). Each one must cite the number it is based on."
    )
