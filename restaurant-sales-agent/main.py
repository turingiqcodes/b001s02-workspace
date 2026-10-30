"""Run the Restaurant Sales Intelligence Agent from the command line.

    python main.py             # use cached data if present
    python main.py --refresh   # download the data again
"""

import argparse
import asyncio
import sys
from pathlib import Path

from data_fetcher import DataFetchError
from llm import LLMError
from orchestrator import Orchestrator

REPORT_FILE = Path(__file__).parent / "report.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Restaurant Sales Intelligence Agent")
    parser.add_argument("--refresh", action="store_true", help="download the sales data again")
    args = parser.parse_args()

    try:
        report = asyncio.run(Orchestrator().run(refresh_data=args.refresh))
    except (DataFetchError, LLMError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130

    REPORT_FILE.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Report saved to {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
