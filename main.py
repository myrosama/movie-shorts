"""
main.py
────────
Orchestrator for the Movie Shorts pipeline.

Usage:
    # Process today's trending movie automatically:
    python main.py

    # Process a specific movie (MP4 already on disk):
    python main.py --movie "Inception" --file /path/to/inception.mp4

    # Download from CinemagicHD first, then process:
    python main.py --movie "Inception" --download

    # Skip upload (generate videos only):
    python main.py --no-upload

    # First-time setup:
    python main.py --setup
"""

import os
import sys
import json
import argparse
import shutil
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Pipeline modules
from pipeline.movie_selector   import select_movie, get_movie_genre, mark_processed
from pipeline.subtitle_fetcher import fetch_subtitles, parse_srt_to_text
from pipeline.script_generator import generate_all_scripts
from pipeline.voice_synthesizer import synthesize_all_scripts
from pipeline.video_assembler   import assemble_all_videos
from pipeline.uploader          import upload_all_videos, tiktok_ready_info

import config

console = Console()


def print_banner():
    console.print(Panel.fit(
        "[bold cyan]🎬 AI Movie Shorts Pipeline[/bold cyan]\n"
        "[dim]Automated movie recap content machine[/dim]",
        border_style="cyan",
    ))


def setup_directories():
    """Ensure all required directories exist."""
    dirs = [
        config.TEMP_DIR, config.OUTPUT_DIR, config.MUSIC_DIR,
        config.PROMPTS_DIR, "queue",
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def run_pipeline(
    movie: dict,
    movie_file: str,
    download: bool = False,
    skip_upload: bool = False,
) -> bool:
    """
    Run the full pipeline for a single movie.

    Args:
        movie:       Movie metadata dict (from TMDB or manual)
        movie_file:  Path to the .mp4 file (or None if downloading)
        download:    Whether to download from CinemagicHD
        skip_upload: If True, generate videos but don't upload

    Returns:
        True if pipeline completed successfully
    """
    title   = movie.get("title", "Unknown Movie")
    tmdb_id = movie.get("id")
    overview = movie.get("overview", "")
    genre   = get_movie_genre(movie)

    safe_title = title.replace(" ", "_").replace("/", "_")
    work_dir   = os.path.join(config.TEMP_DIR, safe_title)
    out_dir    = os.path.join(config.OUTPUT_DIR, safe_title)
    srt_path   = os.path.join(work_dir, f"{safe_title}.srt")

    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    console.print(f"\n[bold]━━━ Processing: {title} ━━━[/bold]")
    console.print(f"[dim]Genre: {genre} | TMDB ID: {tmdb_id}[/dim]")

    # ── Step 1: Get movie file ────────────────────────────────────────────────
    if download and not movie_file:
        console.print("\n[bold cyan]⬇️  Step 1: Downloading movie...[/bold cyan]")
        from pipeline.movie_downloader import download_movie
        movie_file = os.path.join(work_dir, f"{safe_title}.mp4")
        success = download_movie(title, movie_file)
        if not success or not os.path.exists(movie_file):
            console.print("[red]❌ Movie download failed. Add file manually and use --file[/red]")
            return False
    elif not movie_file or not os.path.exists(movie_file):
        console.print(f"[red]❌ Movie file not found: {movie_file}[/red]")
        console.print(f"[yellow]Hint: python main.py --movie \"{title}\" --download[/yellow]")
        return False

    console.print(f"[green]✅ Movie file: {movie_file}[/green]")

    # ── Step 2: Fetch subtitles ───────────────────────────────────────────────
    console.print("\n[bold cyan]📝 Step 2: Fetching subtitles...[/bold cyan]")
    srt_ok = fetch_subtitles(title, srt_path, tmdb_id=tmdb_id)
    if not srt_ok:
        console.print("[red]❌ Could not fetch subtitles. Cannot generate scripts without them.[/red]")
        return False

    subtitle_text = parse_srt_to_text(srt_path)
    console.print(f"[dim]   Parsed {len(subtitle_text)} chars of subtitle text[/dim]")

    # ── Step 3: Generate AI scripts ───────────────────────────────────────────
    console.print("\n[bold cyan]🤖 Step 3: Generating AI scripts...[/bold cyan]")
    scripts_dir = os.path.join(work_dir, "scripts")
    scripts = generate_all_scripts(title, subtitle_text, overview, scripts_dir)

    if not scripts:
        console.print("[red]❌ No scripts generated[/red]")
        return False

    # ── Step 4: Synthesize voice ──────────────────────────────────────────────
    console.print("\n[bold cyan]🎙️  Step 4: Synthesizing voice...[/bold cyan]")
    audio_dir   = os.path.join(work_dir, "audio")
    audio_paths = synthesize_all_scripts(scripts, title, audio_dir, genre)

    if not audio_paths:
        console.print("[red]❌ Voice synthesis failed[/red]")
        return False

    # ── Step 5: Assemble videos (SHORT + LONG versions) ───────────────────────
    console.print("\n[bold cyan]🎬 Step 5: Assembling videos...[/bold cyan]")
    all_video_files = {}

    for version, version_config in config.VIDEO_VERSIONS.items():
        console.print(f"\n[bold]  Version: {version.upper()} "
                      f"({version_config['min']}–{version_config['max']}s) "
                      f"→ {', '.join(version_config['platforms'])}[/bold]")

        version_out_dir  = os.path.join(out_dir, version)
        version_work_dir = os.path.join(work_dir, version)

        # Scale audio speed for long version: same script but longer target
        # achieved by using a slower Kokoro speed for the long version
        version_audio = audio_paths
        if version == "long":
            # Re-synthesize at slower speed to fill the longer target naturally
            from pipeline.voice_synthesizer import synthesize_all_scripts as resynth
            long_audio_dir = os.path.join(work_dir, "audio_long")
            import config as _cfg
            orig_speed = _cfg.KOKORO_SPEED
            _cfg.KOKORO_SPEED = 0.9  # Slower = longer audio to fill 65-75s target
            version_audio = resynth(scripts, title, long_audio_dir, genre)
            _cfg.KOKORO_SPEED = orig_speed

        video_files = assemble_all_videos(
            movie_path=movie_file,
            scripts=scripts,
            audio_paths=version_audio,
            movie_title=title,
            output_dir=version_out_dir,
        )

        for vtype, path in video_files.items():
            all_video_files[f"{vtype}_{version}"] = path

    if not all_video_files:
        console.print("[red]❌ No videos assembled[/red]")
        return False

    # ── Step 6: Print summary table ───────────────────────────────────────────
    table = Table(title=f"Generated Videos — {title}")
    table.add_column("Video Type", style="cyan")
    table.add_column("Version", style="magenta")
    table.add_column("Platform", style="green")
    table.add_column("File")

    for key, path in all_video_files.items():
        vtype, version = (key.rsplit("_", 1) if "_" in key else (key, "short"))
        platforms = ", ".join(config.VIDEO_VERSIONS.get(version, {}).get("platforms", []))
        table.add_row(vtype.replace("_", " ").title(), version, platforms, os.path.basename(path))

    console.print(table)

    # ── Step 7: Upload ────────────────────────────────────────────────────────
    if not skip_upload:
        console.print("\n[bold cyan]📤 Step 6: Uploading to YouTube Shorts...[/bold cyan]")

        # Upload the SHORT versions to YouTube (YouTube Shorts)
        short_files = {k: v for k, v in all_video_files.items() if k.endswith("_short")}
        uploaded = upload_all_videos(short_files, scripts, title)

        # Show TikTok info for LONG versions
        long_files = {k: v for k, v in all_video_files.items() if k.endswith("_long")}
        tiktok_ready_info(long_files, title)
    else:
        console.print("\n[yellow]⏭️  Skipping upload (--no-upload flag)[/yellow]")
        for key, path in all_video_files.items():
            console.print(f"  [green]✅[/green] {key}: {path}")

    # ── Step 8: Mark as processed ─────────────────────────────────────────────
    mark_processed(movie)
    console.print(f"\n[bold green]🎉 Done! {title} processed successfully.[/bold green]")
    return True


def main():
    print_banner()
    setup_directories()

    parser = argparse.ArgumentParser(
        description="AI Movie Shorts Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--movie",      type=str,  help="Movie title to process")
    parser.add_argument("--file",       type=str,  help="Path to local .mp4 file")
    parser.add_argument("--tmdb-id",   type=int,  help="TMDB movie ID (improves subtitle matching)")
    parser.add_argument("--download",   action="store_true", help="Download from CinemagicHD bot")
    parser.add_argument("--no-upload",  action="store_true", help="Generate videos but skip upload")
    parser.add_argument("--setup",      action="store_true", help="Run first-time setup")
    args = parser.parse_args()

    # ── First-time setup mode ────────────────────────────────────────────────
    if args.setup:
        console.print("[bold cyan]🔧 First-time Setup[/bold cyan]\n")
        console.print("1. Setting up Playwright browser for CinemagicHD...")
        import asyncio
        from pipeline.movie_downloader import setup_session
        asyncio.run(setup_session())

        console.print("\n2. Authenticating YouTube...")
        from pipeline.uploader import get_authenticated_service
        get_authenticated_service()

        console.print("\n[bold green]✅ Setup complete! Run: python main.py[/bold green]")
        return

    # ── Select or use provided movie ─────────────────────────────────────────
    if args.movie:
        movie = {
            "id":       args.tmdb_id or 0,
            "title":    args.movie,
            "overview": "",
            "genre_ids": [],
        }
    else:
        console.print("[cyan]📊 Selecting today's trending movie...[/cyan]")
        movie = select_movie()
        if not movie:
            console.print("[red]❌ No movie to process today[/red]")
            sys.exit(1)

    # ── Run pipeline ─────────────────────────────────────────────────────────
    success = run_pipeline(
        movie=movie,
        movie_file=args.file,
        download=args.download,
        skip_upload=args.no_upload,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
