"""
config.py — Centralized settings and API keys.
Fill in your keys below or use a .env file.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── API Keys ───────────────────────────────────────────────────────────────
GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
TMDB_API_KEY         = os.getenv("TMDB_API_KEY", "YOUR_TMDB_API_KEY_HERE")  # Free at themoviedb.org
OPENSUBTITLES_API_KEY = os.getenv("OPENSUBTITLES_API_KEY", "YOUR_OPENSUBS_KEY_HERE")  # Free

# YouTube OAuth2 — path to the client_secrets.json you download from Google Cloud Console
YOUTUBE_CLIENT_SECRETS = os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secrets.json")
YOUTUBE_TOKEN_FILE     = "youtube_token.json"  # Auto-created after first login

# ─── CinemagicHD Web App ────────────────────────────────────────────────────
CINEMAGIC_BOT_URL = "https://t.me/cinemagic_hd_bot"
# Path to your Telegram browser session (Playwright persistent context)
TELEGRAM_SESSION_DIR = os.path.expanduser("~/.movie_shorts_telegram_session")

# ─── Video Settings ──────────────────────────────────────────────────────────
VIDEO_QUALITY = "720p"          # Quality to request from CinemagicHD
OUTPUT_RESOLUTION = (1080, 1920)  # 9:16 vertical for Shorts
VIDEO_FPS = 30
MAX_CLIP_DURATION = 30          # Max seconds per individual clip (fair use)

# Video types to generate per movie
VIDEO_TYPES = ["full_recap", "shocking_moments", "ending_explained", "hidden_details"]

# ─── Platform Monetization Rules ────────────────────────────────────────────
# TikTok Creator Rewards: MINIMUM 60 seconds to earn anything
# YouTube Shorts: No minimum length (any duration earns ad share)
# Instagram Reels: No minimum (invite-only bonuses anyway)
#
# Strategy: generate TWO versions per video type
#   _short → 35-45s   (YouTube Shorts + Instagram Reels)
#   _long  → 65-75s   (TikTok — crosses the 60s monetization threshold)

VIDEO_VERSIONS = {
    "short": {"min": 35, "max": 45, "platforms": ["youtube", "instagram"]},
    "long":  {"min": 65, "max": 75, "platforms": ["tiktok"]},
}

# Target durations per video type per version (seconds)
VIDEO_DURATIONS = {
    "short": {
        "full_recap":        42,
        "shocking_moments":  38,
        "ending_explained":  40,
        "hidden_details":    35,
    },
    "long": {
        "full_recap":        72,
        "shocking_moments":  68,
        "ending_explained":  70,
        "hidden_details":    65,
    },
}

# ─── Voice Settings ──────────────────────────────────────────────────────────
# Kokoro TTS voice per genre
KOKORO_VOICES = {
    "default":  "bm_george",   # Deep British male — great for most movies
    "romance":  "af_sky",      # Warm female narrator
    "horror":   "bm_lewis",    # Darker male voice
    "comedy":   "af_nicole",   # Lighter female voice
}
KOKORO_SPEED = 1.1              # Slightly faster for punchy shorts pacing

# ─── Copyright Shield Settings ───────────────────────────────────────────────
# Applied by copyright_shield.py
COLOR_GRADE_FILTER = "eq=brightness=0.05:contrast=1.15:saturation=1.1"
SPEED_ADJUST       = 0.97        # 3% speed adjustment (imperceptible)
MUSIC_VOLUME_DB    = -18         # Background music ducked well under voice
WATERMARK_TEXT     = "Movie Recap | Commentary"

# ─── Scheduling ──────────────────────────────────────────────────────────────
UPLOAD_TIME = "09:00"          # Daily upload time (24h format)
MOVIES_QUEUE_FILE   = "queue/movies.json"
PROCESSED_FILE      = "queue/processed.json"

# ─── YouTube Upload Settings ─────────────────────────────────────────────────
YOUTUBE_CATEGORY_ID = "1"       # Film & Animation
YOUTUBE_PRIVACY     = "public"
YOUTUBE_TAGS_BASE   = ["movie recap", "movie summary", "shorts", "movieshorts", "filmreview"]

# ─── Paths ───────────────────────────────────────────────────────────────────
TEMP_DIR   = "temp"
OUTPUT_DIR = "output"
MUSIC_DIR  = "assets/music"
PROMPTS_DIR = "prompts"
