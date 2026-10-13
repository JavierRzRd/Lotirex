"""One bounded update for cPanel Cron Jobs; no continuously running process."""
import argparse
import json

from . import sync
from .deployment import ensure_database


def run_once(max_days=7):
    if not 1 <= max_days <= 31:
        raise ValueError("max_days must be between 1 and 31")
    ensure_database()
    return sync.run_sync(max_days=max_days, prioritize_recent=True, retry_due=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a bounded, scheduled lottery update")
    parser.add_argument("--max-days", type=int, default=7)
    args = parser.parse_args(argv)
    if not 1 <= args.max_days <= 31:
        parser.error("--max-days must be between 1 and 31")
    result = run_once(args.max_days)
    print(json.dumps(result, ensure_ascii=False))
    # Pending results and overlap are normal; genuine source failures need attention.
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
