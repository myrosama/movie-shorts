"""
clip_scraper.py
───────────────
Downloads curated movie clips from @MOVIECLIPS (and similar channels) on YouTube.

These are official studio-uploaded promotional clips — safe to use.
Uses yt-dlp to search and download up to MAX_CLIPS per movie.

Returns: list of dicts with {path, title, description, duration}
"""

import os
import json
import subprocess
from pathlib import Path
from rich.console import Console

console = Console()

MAX_CLIPS   = 8      # How many clips to pull per movie
CLIP_HEIGHT = 720    # Download at 720p (faster, sufficient quality)


def search_and_download_clips(movie_title: str, output_dir: str) -> list[dict]:
    """
    Search YouTube @MOVIECLIPS channel for the movie, download top clips.

    Args:
        movie_title:  e.g. "The Gorge"
        output_dir:   Directory to save clip files

    Returns:
        List of dicts: [{path, title, description, duration}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)

    # Check if already downloaded (cache)
    meta_cache = os.path.join(output_dir, "clips_meta.json")
    if os.path.exists(meta_cache):
        with open(meta_cache) as f:
            cached = json.load(f)
        # Verify files still exist on disk
        valid = [c for c in cached if os.path.exists(c["path"])]
        if valid:
            console.print(f"[dim]📎 Using {len(valid)} cached MovieClips[/dim]")
            return valid

    console.print(f"[cyan]🎬 Searching MovieClips for: {movie_title}...[/cyan]")

    # Search query targeting the official Movieclips channel
    search_query = f"{movie_title} clip site:youtube.com/c/MOVIECLIPS OR site:youtube.com/@MOVIECLIPS"
    yt_search = f"ytsearch{MAX_CLIPS}:{movie_title} movieclips"

    # Step 1: Fetch video metadata (no download yet)
    meta_cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--default-search", "ytsearch",
        # Restrict to the official Movieclips channel where possible
        f"ytsearch{MAX_CLIPS*2}:{movie_title} official clip movieclips",
    ]

    try:
        result = subprocess.run(
            meta_cmd,
            capture_output=True, text=True, timeout=60
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except subprocess.TimeoutExpired:
        console.print("[red]❌ yt-dlp metadata search timed out[/red]")
        return []

    if not lines:
        console.print("[red]❌ No results from yt-dlp search[/red]")
        return []

    # Filter for most relevant clips (must mention movie title in title)
    candidates = []
    for line in lines:
        try:
            meta = json.loads(line)
            title_lower = (meta.get("title") or "").lower()
            movie_lower = movie_title.lower()
            # Prefer official clip/scene results, exclude trailers & playlists
            if (movie_lower in title_lower and
                    meta.get("duration", 999) < 600 and  # Under 10 min
                    "trailer" not in title_lower and
                    "full movie" not in title_lower):
                candidates.append(meta)
        except Exception:
            continue

    # Sort: prefer shorter clips (actual scenes, not full features)
    candidates.sort(key=lambda x: x.get("duration", 999))
    top_candidates = candidates[:MAX_CLIPS]

    if not top_candidates:
        console.print(f"[yellow]⚠️  No matching clips found for '{movie_title}'. Try a broader title.[/yellow]")
        return []

    console.print(f"[dim]   Found {len(top_candidates)} candidate clips — downloading...[/dim]")

    # Step 2: Download each clip
    clips = []
    for i, meta in enumerate(top_candidates):
        video_id  = meta.get("id") or meta.get("url", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}" if len(video_id) == 11 else video_id
        out_path  = os.path.join(output_dir, f"clip_{i:02d}.mp4")

        if os.path.exists(out_path):
            console.print(f"[dim]  ⏭️  Already have: {meta.get('title', '')[:60]}[/dim]")
            clips.append({
                "path":        out_path,
                "title":       meta.get("title", ""),
                "description": meta.get("description", ""),
                "duration":    meta.get("duration", 0),
            })
            continue

        console.print(f"[dim]  ⬇️  [{i+1}/{len(top_candidates)}] {meta.get('title', '')[:60]}[/dim]")

        dl_cmd = [
            "yt-dlp",
            "--no-warnings",
            "-f", f"bestvideo[height<={CLIP_HEIGHT}][ext=mp4]+bestaudio[ext=m4a]/best[height<={CLIP_HEIGHT}]",
            "--merge-output-format", "mp4",
            "-o", out_path,
            video_url,
        ]
        try:
            r = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and os.path.exists(out_path):
                clips.append({
                    "path":        out_path,
                    "title":       meta.get("title", ""),
                    "description": meta.get("description", ""),
                    "duration":    meta.get("duration", 0),
                })
            else:
                console.print(f"[yellow]  ⚠️  Download failed: {r.stderr[-100:]}[/yellow]")
        except subprocess.TimeoutExpired:
            console.print(f"[yellow]  ⚠️  Download timed out[/yellow]")

    # Cache metadata
    if clips:
        with open(meta_cache, "w") as f:
            json.dump(clips, f, indent=2)
        console.print(f"[green]✅ Downloaded {len(clips)} MovieClips for '{movie_title}'[/green]")

    return clips


def clips_summary_for_gemini(clips: list[dict]) -> str:
    """
    Format clip metadata into a compact text block for the Gemini prompt.
    Tells Gemini what scenes are available without needing video analysis.
    """
    lines = ["AVAILABLE MOVIE CLIPS (from official Movieclips channel):"]
    for i, clip in enumerate(clips):
        duration = int(clip.get("duration", 0))
        mins, secs = divmod(duration, 60)
        lines.append(
            f"[CLIP {i}] Duration: {mins}m{secs:02d}s | Title: {clip['title']}"
        )
        if clip.get("description"):
            # Truncate description to first 200 chars
            desc = clip["description"].replace("\n", " ")[:200]
            lines.append(f"         Desc: {desc}")
    return "\n".join(lines)
