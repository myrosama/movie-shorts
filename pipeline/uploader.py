"""
uploader.py
────────────
Uploads final videos to YouTube Shorts using the official YouTube Data API v3.

FIRST-TIME SETUP:
1. Go to https://console.cloud.google.com
2. Create a project → Enable "YouTube Data API v3"
3. Create OAuth 2.0 credentials → Download as client_secrets.json
4. Place client_secrets.json in the movie-shorts/ root
5. Run: python pipeline/uploader.py --auth
   (Opens browser for one-time Google login → saves token)
"""

import os
import sys
import json
import pickle
import re
from datetime import datetime
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PATH = config.YOUTUBE_TOKEN_FILE


def get_authenticated_service():
    """Get authenticated YouTube API service. Handles token refresh automatically."""
    creds = None

    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            console.print("[cyan]🔄 Refreshing YouTube token...[/cyan]")
            creds.refresh(Request())
        else:
            if not os.path.exists(config.YOUTUBE_CLIENT_SECRETS):
                console.print(f"[red]❌ Missing: {config.YOUTUBE_CLIENT_SECRETS}[/red]")
                console.print("[yellow]Download OAuth2 credentials from Google Cloud Console[/yellow]")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                config.YOUTUBE_CLIENT_SECRETS, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
        console.print("[green]✅ YouTube authentication saved[/green]")

    return build("youtube", "v3", credentials=creds)


def build_metadata(script: dict, movie_title: str, video_type: str, version: str) -> dict:
    """Build YouTube upload metadata from the script."""
    title = script.get("title", f"{movie_title} - {video_type.replace('_', ' ').title()}")

    # Truncate title to YouTube's 100 char limit
    if len(title) > 100:
        title = title[:97] + "..."

    # Description: script description + platform-safe disclaimer
    base_description = script.get("description", f"Movie recap of {movie_title}")
    disclaimer = (
        "\n\n⚠️ This video is a transformative commentary and review of the film. "
        "All clips are used for commentary, criticism, and educational purposes under fair use. "
        "Original film rights belong to their respective owners.\n"
    )
    description = base_description + disclaimer

    # Tags: base tags + movie-specific + type-specific
    tags = list(config.YOUTUBE_TAGS_BASE)
    tags += script.get("tags", [])
    tags += [
        movie_title.lower(),
        movie_title.lower().replace(" ", ""),
        video_type.replace("_", " "),
        "#shorts",
        "filmreview",
        "movieclips",
    ]
    # Deduplicate and limit to 500 chars total (YouTube limit)
    seen = set()
    final_tags = []
    for t in tags:
        clean = t.strip().lstrip('#').lower()
        if clean and clean not in seen:
            seen.add(clean)
            final_tags.append(t)

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": final_tags[:30],  # YouTube allows up to 30 tags effectively
            "categoryId": config.YOUTUBE_CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": config.YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
            # Mark as altered/synthetic content (YouTube AI disclosure requirement)
            "containsSyntheticMedia": True,
        },
    }


def upload_to_youtube(
    video_path: str,
    script: dict,
    movie_title: str,
    video_type: str,
    version: str = "short",
) -> str | None:
    """
    Upload a video to YouTube Shorts.

    Args:
        video_path: Path to the .mp4 file
        script:     The script dict (contains title, description, tags)
        movie_title: Movie title for metadata
        video_type:  e.g. "full_recap"
        version:     "short" or "long"

    Returns:
        YouTube video ID if successful, None otherwise
    """
    console.print(f"\n[cyan]📤 Uploading to YouTube: {os.path.basename(video_path)}[/cyan]")

    youtube = get_authenticated_service()
    if not youtube:
        return None

    metadata = build_metadata(script, movie_title, video_type, version)
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 8,  # 8MB chunks
    )

    try:
        request = youtube.videos().insert(
            part="snippet,status",
            body=metadata,
            media_body=media,
        )

        response = None
        last_progress = -1
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                if progress != last_progress and progress % 10 == 0:
                    console.print(f"[dim]   Upload progress: {progress}%[/dim]")
                    last_progress = progress

        video_id = response.get("id")
        url = f"https://youtube.com/shorts/{video_id}"
        console.print(f"[bold green]✅ Uploaded! {url}[/bold green]")

        # Log the upload
        _log_upload(video_id, url, movie_title, video_type, version, metadata["snippet"]["title"])
        return video_id

    except Exception as e:
        console.print(f"[red]❌ Upload failed: {e}[/red]")
        return None


def _log_upload(video_id: str, url: str, movie: str, vtype: str, version: str, title: str):
    """Append upload record to upload_log.json."""
    log_file = "upload_log.json"
    log = []
    if os.path.exists(log_file):
        with open(log_file) as f:
            log = json.load(f)

    log.append({
        "date": datetime.now().isoformat(),
        "movie": movie,
        "video_type": vtype,
        "version": version,
        "title": title,
        "video_id": video_id,
        "url": url,
    })
    with open(log_file, "w") as f:
        json.dump(log, f, indent=2)


def upload_all_videos(
    video_files: dict,
    scripts: dict,
    movie_title: str,
) -> dict[str, str]:
    """
    Upload all generated videos to YouTube.

    Args:
        video_files: Dict of {video_type_version: file_path}
                     e.g. {"full_recap_short": "/path/full_recap_short.mp4"}
        scripts:     Dict of {video_type: script_dict}
        movie_title: Movie title

    Returns:
        Dict of {video_type_version: youtube_url}
    """
    uploaded = {}

    for key, video_path in video_files.items():
        if not os.path.exists(video_path):
            console.print(f"[yellow]⚠️  File not found, skipping: {video_path}[/yellow]")
            continue

        # Parse key like "full_recap_short" → video_type="full_recap", version="short"
        if key.endswith("_short"):
            video_type = key[:-6]
            version = "short"
        elif key.endswith("_long"):
            video_type = key[:-5]
            version = "long"
        else:
            video_type = key
            version = "short"

        script = scripts.get(video_type, {})

        video_id = upload_to_youtube(
            video_path=video_path,
            script=script,
            movie_title=movie_title,
            video_type=video_type,
            version=version,
        )
        if video_id:
            uploaded[key] = f"https://youtube.com/shorts/{video_id}"

    console.print(f"\n[bold green]✅ Uploaded {len(uploaded)}/{len(video_files)} videos to YouTube[/bold green]")
    return uploaded


def tiktok_ready_info(video_files: dict, movie_title: str):
    """Print instructions for TikTok upload (the '_long' versions)."""
    long_videos = {k: v for k, v in video_files.items() if k.endswith("_long")}
    if not long_videos:
        return

    console.print("\n[bold yellow]📱 TikTok Upload (Manual or API):[/bold yellow]")
    console.print("[dim]These '_long' versions (65-75s) meet TikTok's 60s monetization minimum:[/dim]")
    for key, path in long_videos.items():
        console.print(f"  • {key}: {path}")
    console.print("[dim]Upload at: https://www.tiktok.com/upload (or use TikTok Content Posting API)[/dim]")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true", help="Authenticate with YouTube")
    args = parser.parse_args()

    if args.auth:
        svc = get_authenticated_service()
        if svc:
            console.print("[bold green]✅ YouTube authentication successful![/bold green]")
