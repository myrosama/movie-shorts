"""
scheduler.py
─────────────
Runs the pipeline automatically every day at a set time.
Uses Python's `schedule` library — no cron required.

Usage:
    # Run as a background daemon (keeps running):
    python scheduler.py

    # Or add to crontab for system-level scheduling:
    # crontab -e
    # 0 9 * * * cd /path/to/movie-shorts && python main.py >> logs/daily.log 2>&1
"""

import os
import sys
import time
import subprocess
import schedule
from datetime import datetime
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

console = Console()
LOG_DIR = "logs"


def run_daily_pipeline():
    """Execute the daily pipeline as a subprocess."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d_%H%M')}.log")

    console.print(f"\n[bold cyan]🚀 {datetime.now().strftime('%Y-%m-%d %H:%M')} — Starting daily pipeline[/bold cyan]")
    console.print(f"[dim]Log: {log_file}[/dim]")

    with open(log_file, "w") as log:
        result = subprocess.run(
            [sys.executable, "main.py"],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

    if result.returncode == 0:
        console.print(f"[bold green]✅ Pipeline completed successfully[/bold green]")
    else:
        console.print(f"[bold red]❌ Pipeline failed (exit {result.returncode}) — check {log_file}[/bold red]")


def main():
    console.print(f"[bold]🕐 Scheduler started — running daily at {config.UPLOAD_TIME}[/bold]")
    console.print(f"[dim]Press Ctrl+C to stop[/dim]\n")

    schedule.every().day.at(config.UPLOAD_TIME).do(run_daily_pipeline)

    # Also run immediately on start if you want (comment out if not desired)
    # run_daily_pipeline()

    while True:
        schedule.run_pending()
        next_run = schedule.next_run()
        if next_run:
            delta = next_run - datetime.now()
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            mins = rem // 60
            console.print(
                f"\r[dim]⏳ Next run in {hours}h {mins}m "
                f"(at {next_run.strftime('%H:%M')})[/dim]",
                end=""
            )
        time.sleep(60)


if __name__ == "__main__":
    main()
