"""
scene_analyzer.py
─────────────────
Analyzes the full movie subtitle file to find the most emotionally
engaging scenes — WITHOUT needing to process the video file directly.

Algorithm:
1. Parse SRT into time-windowed segments (every 90 seconds)
2. Score each window for emotional categories:
   - Romantic/flirty (highest weight for engagement)
   - Dramatic/tense
   - Action/conflict
   - Mystery/revelation
3. Return top N scored windows with timestamps for clip extraction
4. Also fetches TMDB metadata for character names and plot context
"""

import re
import os
import sys
import json
import requests
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

# ── Emotion keyword scoring ────────────────────────────────────────────────────
KEYWORD_SCORES = {
    # Romantic/flirty — highest weight (these get most engagement)
    "romantic": {
        "words": [
            "love", "kiss", "touch", "feel", "together", "hold", "close",
            "beautiful", "heart", "mine", "yours", "stay", "need you",
            "want you", "miss you", "trust", "safe", "gentle", "smile",
            "laugh", "dance", "promise", "forever", "always", "feelings",
        ],
        "weight": 3.0,
    },
    # Dramatic reveals / twists
    "revelation": {
        "words": [
            "truth", "know who", "found out", "lied", "secret", "never told",
            "all along", "the whole time", "real reason", "actually",
            "been hiding", "figured out", "it was you", "don't understand",
            "explain", "why didn't you", "how could",
        ],
        "weight": 2.5,
    },
    # High tension / conflict
    "tension": {
        "words": [
            "kill", "stop", "run", "help", "no!", "please", "don't", "won't",
            "can't", "never", "danger", "die", "dead", "shoot", "fight",
            "lose", "afraid", "scared", "threat", "warning", "gone",
            "destroyed", "end", "over", "finished",
        ],
        "weight": 2.0,
    },
    # Mystery / intrigue
    "mystery": {
        "words": [
            "what is", "who are", "where did", "how is", "impossible",
            "can't be", "something wrong", "strange", "weird", "monster",
            "creature", "unknown", "real", "exist", "down there", "gorge",
            "beneath", "underground", "hidden",
        ],
        "weight": 2.0,
    },
}


def _srt_to_segments(srt_text: str, window_seconds: int = 90) -> list[dict]:
    """
    Parse SRT and group into time windows.
    Returns list of {start_sec, end_sec, text, start_ts, end_ts}
    """
    # Match SRT blocks: index, timestamps, text
    pattern = re.compile(
        r'\d+\s*\n'
        r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n'
        r'([\s\S]*?)(?=\n\n|\Z)',
        re.MULTILINE
    )

    def ts_to_secs(ts):
        ts = ts.replace(',', '.')
        h, m, s = ts.split(':')
        return int(h)*3600 + int(m)*60 + float(s)

    def secs_to_ts(s):
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}"

    lines = []
    for match in pattern.finditer(srt_text):
        start = ts_to_secs(match.group(1))
        end   = ts_to_secs(match.group(2))
        text  = re.sub(r'<[^>]+>', '', match.group(3)).strip()
        if text:
            lines.append({"start": start, "end": end, "text": text})

    if not lines:
        return []

    # Group into windows
    movie_end = lines[-1]["end"]
    segments  = []
    win_start = 0.0
    while win_start < movie_end:
        win_end = win_start + window_seconds
        chunk = [l for l in lines if l["start"] >= win_start and l["start"] < win_end]
        if chunk:
            text = " ".join(l["text"] for l in chunk)
            segments.append({
                "start_sec": win_start,
                "end_sec":   min(win_end, movie_end),
                "start_ts":  secs_to_ts(win_start),
                "end_ts":    secs_to_ts(min(win_end, movie_end)),
                "text":      text,
                "line_count": len(chunk),
            })
        win_start = win_end

    return segments


def _score_segment(segment: dict) -> dict:
    """Score a time window for emotional intensity across all categories."""
    text_lower = segment["text"].lower()
    total  = 0.0
    cats   = {}

    for cat, cfg in KEYWORD_SCORES.items():
        hits = sum(1 for w in cfg["words"] if w in text_lower)
        score = hits * cfg["weight"]
        if score > 0:
            cats[cat] = round(score, 2)
        total += score

    # Density bonus: more dialogue in window = richer scene
    density_bonus = min(segment["line_count"] / 20.0, 1.5)
    total += density_bonus

    return {**segment, "score": round(total, 2), "categories": cats}


def find_best_scenes(srt_text: str, n_scenes: int = 10) -> list[dict]:
    """
    Full scene scoring pipeline.
    Returns top N scored windows sorted by score descending.
    """
    segments = _srt_to_segments(srt_text)
    if not segments:
        return []

    scored = [_score_segment(s) for s in segments]
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Deduplicate: don't pick two adjacent windows
    top = []
    used_starts = set()
    for s in scored:
        # Skip if we already picked a window within 3 minutes of this one
        too_close = any(abs(s["start_sec"] - u) < 180 for u in used_starts)
        if not too_close:
            top.append(s)
            used_starts.add(s["start_sec"])
        if len(top) >= n_scenes:
            break

    # Re-sort chronologically so narrative flows properly
    top.sort(key=lambda x: x["start_sec"])

    console.print(f"[dim]   Scored {len(scored)} windows → top {len(top)} scenes[/dim]")
    return top


def get_tmdb_metadata(movie_title: str, tmdb_id: int = 0) -> dict:
    """
    Fetch movie details from TMDB: plot summary, cast, genres, tagline.
    Returns dict with overview, cast_names, tagline, genres.
    """
    meta = {
        "overview": "",
        "tagline": "",
        "cast": [],
        "genres": [],
        "title": movie_title,
    }

    if not config.TMDB_API_KEY or config.TMDB_API_KEY == "YOUR_TMDB_API_KEY_HERE":
        return meta

    try:
        # Search by title if no ID
        if not tmdb_id:
            r = requests.get(
                "https://api.themoviedb.org/3/search/movie",
                params={"api_key": config.TMDB_API_KEY, "query": movie_title},
                timeout=8,
            )
            results = r.json().get("results", [])
            if results:
                tmdb_id = results[0]["id"]
                meta["overview"] = results[0].get("overview", "")

        if tmdb_id:
            # Full details
            r2 = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                params={"api_key": config.TMDB_API_KEY},
                timeout=8,
            )
            d = r2.json()
            meta["overview"] = d.get("overview", meta["overview"])
            meta["tagline"]  = d.get("tagline", "")
            meta["genres"]   = [g["name"] for g in d.get("genres", [])]

            # Cast
            r3 = requests.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/credits",
                params={"api_key": config.TMDB_API_KEY},
                timeout=8,
            )
            cast = r3.json().get("cast", [])[:6]
            meta["cast"] = [
                {"name": c["name"], "character": c.get("character", "")}
                for c in cast
            ]
    except Exception as e:
        console.print(f"[yellow]⚠️  TMDB fetch failed: {e}[/yellow]")

    if meta["overview"]:
        console.print(f"[dim]   TMDB: {len(meta['cast'])} cast members, genres: {meta['genres']}[/dim]")

    return meta


def build_scene_context(scenes: list[dict], max_chars_per_scene: int = 300) -> str:
    """
    Format top scenes into a compact text block for Gemini.
    Includes timestamp, score category and dialogue snippet.
    """
    lines = []
    for i, s in enumerate(scenes):
        cats = ", ".join(s.get("categories", {}).keys()) or "neutral"
        snippet = s["text"][:max_chars_per_scene].replace("\n", " ")
        lines.append(
            f"[SCENE {i}] ⏱ {s['start_ts']} → {s['end_ts']} "
            f"| Emotion: {cats} | Score: {s['score']}\n"
            f"  Dialogue: \"{snippet}...\""
        )
    return "\n\n".join(lines)
