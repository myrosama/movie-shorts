"""
movie_selector.py
─────────────────
Picks today's movie to process using TMDB's trending API.
Strategy: trending this week → filter for ones not yet processed → pick #1
"""

import json
import os
import requests
from datetime import date
from rich.console import Console
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

TMDB_BASE = "https://api.themoviedb.org/3"

def get_trending_movies(limit: int = 20) -> list[dict]:
    """Fetch this week's trending movies from TMDB."""
    url = f"{TMDB_BASE}/trending/movie/week"
    params = {"api_key": config.TMDB_API_KEY, "language": "en-US"}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    results = resp.json().get("results", [])[:limit]
    return results

def load_processed() -> set:
    """Load the set of already-processed movie IDs."""
    if os.path.exists(config.PROCESSED_FILE):
        with open(config.PROCESSED_FILE) as f:
            data = json.load(f)
            return set(str(item["id"]) for item in data)
    return set()

def load_queue() -> list:
    """Load manually-added movies from queue."""
    if os.path.exists(config.MOVIES_QUEUE_FILE):
        with open(config.MOVIES_QUEUE_FILE) as f:
            return json.load(f)
    return []

def mark_processed(movie: dict):
    """Mark a movie as processed so we don't re-generate it."""
    processed = []
    if os.path.exists(config.PROCESSED_FILE):
        with open(config.PROCESSED_FILE) as f:
            processed = json.load(f)
    processed.append({
        "id": movie["id"],
        "title": movie["title"],
        "processed_date": str(date.today()),
    })
    os.makedirs(os.path.dirname(config.PROCESSED_FILE), exist_ok=True)
    with open(config.PROCESSED_FILE, "w") as f:
        json.dump(processed, f, indent=2)

def select_movie() -> dict | None:
    """
    Returns the best movie to process today.
    Priority: queue file → TMDB trending (skipping already-processed).
    Returns dict with at minimum: id, title, overview, genre_ids, release_date
    """
    processed = load_processed()

    # 1. Check manual queue first
    queue = load_queue()
    for movie in queue:
        if str(movie.get("id", "")) not in processed:
            console.print(f"[cyan]📋 Using queued movie:[/cyan] {movie['title']}")
            return movie

    # 2. Fall back to TMDB trending
    console.print("[cyan]🔍 Fetching trending movies from TMDB...[/cyan]")
    try:
        trending = get_trending_movies()
    except Exception as e:
        console.print(f"[red]❌ TMDB fetch failed: {e}[/red]")
        return None

    for movie in trending:
        movie_id = str(movie["id"])
        if movie_id not in processed:
            console.print(
                f"[green]✅ Selected:[/green] [bold]{movie['title']}[/bold] "
                f"(popularity: {movie.get('popularity', '?'):.0f})"
            )
            return movie

    console.print("[yellow]⚠️  All trending movies already processed![/yellow]")
    return None

def get_movie_genre(movie: dict) -> str:
    """Map TMDB genre IDs to our voice style categories."""
    genre_map = {
        28: "action", 12: "action", 878: "action",   # Action, Adventure, Sci-Fi
        10749: "romance", 35: "comedy",               # Romance, Comedy
        27: "horror", 53: "horror", 9648: "horror",  # Horror, Thriller, Mystery
    }
    genre_ids = movie.get("genre_ids", [])
    for gid in genre_ids:
        if gid in genre_map:
            return genre_map[gid]
    return "default"

if __name__ == "__main__":
    movie = select_movie()
    if movie:
        print(f"\nTitle: {movie['title']}")
        print(f"Overview: {movie.get('overview', '')[:200]}")
        print(f"Genre: {get_movie_genre(movie)}")
