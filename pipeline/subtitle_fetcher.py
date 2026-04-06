"""
subtitle_fetcher.py
────────────────────
Fetches .SRT subtitle files for a movie from OpenSubtitles.org API (free tier).
Subtitles are the KEY input for the AI script generator — they tell us what
happens at every timestamp in the movie.

Free tier: 5 downloads/day (enough for our 1 movie/day pipeline).
Sign up at: https://www.opensubtitles.com/en/consumers
"""

import os
import sys
import json
import gzip
import requests
from pathlib import Path
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

OPENSUBS_API   = "https://api.opensubtitles.com/api/v1"
OPENSUBS_AGENT = "MovieShortsBot v1.0"


def _get_headers() -> dict:
    return {
        "Api-Key": config.OPENSUBTITLES_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": OPENSUBS_AGENT,
    }


def search_subtitles(movie_title: str, imdb_id: str = None, tmdb_id: int = None) -> list[dict]:
    """Search for English subtitles. Returns list of subtitle results."""
    params = {
        "query": movie_title,
        "languages": "en",
        "type": "movie",
    }
    if imdb_id:
        params["imdb_id"] = imdb_id.replace("tt", "")
    if tmdb_id:
        params["tmdb_id"] = tmdb_id

    try:
        resp = requests.get(
            f"{OPENSUBS_API}/subtitles",
            headers=_get_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        console.print(f"[yellow]⚠️  OpenSubtitles search failed: {e}[/yellow]")
        return []


def download_subtitle(file_id: int, output_path: str) -> bool:
    """Download a specific subtitle file by its file_id."""
    try:
        resp = requests.post(
            f"{OPENSUBS_API}/download",
            headers=_get_headers(),
            json={"file_id": file_id, "sub_format": "srt"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        download_url = data.get("link")
        if not download_url:
            return False

        # Download the actual .srt file
        srt_resp = requests.get(download_url, timeout=30)
        srt_resp.raise_for_status()

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(srt_resp.content)

        console.print(f"[green]✅ Subtitles saved: {output_path}[/green]")
        return True

    except Exception as e:
        console.print(f"[red]❌ Subtitle download failed: {e}[/red]")
        return False


def fetch_subtitles(movie_title: str, output_path: str,
                    tmdb_id: int = None, imdb_id: str = None) -> bool:
    """
    High-level function: search + download best English subtitle for a movie.
    Returns True if subtitles were fetched successfully.
    """
    console.print(f"[cyan]📝 Fetching subtitles for: {movie_title}[/cyan]")

    # Check cache first
    if os.path.exists(output_path) and os.path.getsize(output_path) > 100:
        console.print(f"[green]✅ Subtitles already cached: {output_path}[/green]")
        return True

    results = search_subtitles(movie_title, tmdb_id=tmdb_id, imdb_id=imdb_id)

    if not results:
        console.print(f"[red]❌ No subtitles found for: {movie_title}[/red]")
        return False

    # Pick the best result: prefer ones with most downloads (most reliable)
    best = sorted(
        results,
        key=lambda r: r.get("attributes", {}).get("download_count", 0),
        reverse=True
    )[0]

    file_id = best["attributes"]["files"][0]["file_id"]
    sub_title = best["attributes"].get("release", movie_title)
    console.print(f"[dim]Selected subtitle: {sub_title}[/dim]")

    return download_subtitle(file_id, output_path)


def parse_srt_to_text(srt_path: str) -> str:
    """
    Convert an SRT file to clean plain text with timestamps.
    Format: [HH:MM:SS] Dialogue text
    This is what we feed to the AI script generator.
    """
    import re
    with open(srt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    # Parse SRT blocks
    blocks = re.split(r'\n\s*\n', content.strip())
    lines_out = []

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        # Line 0: index number
        # Line 1: timestamp (00:01:23,456 --> 00:01:26,789)
        # Line 2+: dialogue
        try:
            timestamp_line = lines[1]
            start_ts = timestamp_line.split(' --> ')[0].strip()
            # Convert to HH:MM:SS
            start_clean = start_ts.replace(',', '.').rsplit('.', 1)[0]
            dialogue = ' '.join(lines[2:]).strip()
            # Remove HTML tags (italic markers etc)
            dialogue = re.sub(r'<[^>]+>', '', dialogue)
            if dialogue:
                lines_out.append(f"[{start_clean}] {dialogue}")
        except (IndexError, ValueError):
            continue

    return '\n'.join(lines_out)


if __name__ == "__main__":
    import sys
    title = sys.argv[1] if len(sys.argv) > 1 else "Inception"
    fetch_subtitles(title, f"temp/{title.replace(' ', '_')}.srt")
