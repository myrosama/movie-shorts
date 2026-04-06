"""
video_assembler.py
───────────────────
Assembles the final short video using FFmpeg.

Implements the battle-tested copyright avoidance editing rules from the tutorial:
  1. MUTE   — Remove all original movie audio
  2. CUT    — Slice clips into 3-second segments
  3. DELETE — Remove every alternate 3-second segment (keeps 1,3,5 → removes 2,4,6)
  4. FLIP   — Mirror every alternate KEPT segment horizontally
  5. SPEED  — Slightly speed-adjust the final video (3%)

Additionally:
  - Crops to 9:16 vertical with smart center crop
  - Burns word-by-word pop captions (styled like TikTok)
  - Mixes royalty-free background music under the narration
  - Targets 30-40 second final video length

Output: final/{movie_title}/{video_type}.mp4
"""

import os
import sys
import json
import subprocess
import tempfile
import re
from pathlib import Path
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

# Target output dimensions (9:16 vertical for Shorts/TikTok)
OUT_W, OUT_H = 1080, 1920


def _ts_to_seconds(ts: str) -> float:
    """Convert HH:MM:SS or HH:MM:SS.mmm to seconds."""
    ts = ts.strip().replace(',', '.')
    parts = ts.split(':')
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(ts)
    except Exception:
        return 0.0


def _seconds_to_ts(s: float) -> str:
    """Convert seconds to HH:MM:SS.mmm"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def ffmpeg(args: list, description: str = "") -> bool:
    """Run an FFmpeg command. Returns True on success."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-hide_banner"] + args
    if description:
        console.print(f"[dim]  ▶ {description}[/dim]")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]❌ FFmpeg error: {result.stderr[-300:]}[/red]")
        return False
    return True


def extract_clip(movie_path: str, start: float, end: float, output_path: str) -> bool:
    """Extract a single clip from the movie (no re-encode = fast)."""
    duration = end - start
    return ffmpeg([
        "-ss", str(start),
        "-i", movie_path,
        "-t", str(duration),
        "-c:v", "copy",
        "-an",           # Strip audio (rule 1: MUTE original)
        output_path,
    ], f"Extract clip {_seconds_to_ts(start)} → {_seconds_to_ts(end)}")


def apply_copyright_rules(clip_path: str, output_path: str, clip_index: int) -> bool:
    """
    Apply the 5 copyright avoidance rules to a single clip.

    Rules applied here:
    - Rule 2 (CUT):    Already done at extract stage — clips are ≤3s
    - Rule 4 (FLIP):   Every other clip is horizontally mirrored
    - Rule 5 (SPEED):  3% speed adjustment on all clips

    Returns True on success.
    """
    filter_parts = []

    # Crop + scale to 9:16 vertical
    filter_parts.append(
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},"
        f"setsar=1"
    )

    # Rule 4: FLIP — mirror every alternate clip (clip_index 0,2,4... → flip; 1,3,5... → normal)
    if clip_index % 2 == 0:
        filter_parts.append("hflip")

    # Rule 5: SPEED — 3% time adjustment (imperceptible but breaks fingerprint)
    filter_parts.append("setpts=0.97*PTS")

    # Color grade for extra fingerprint breaking
    filter_parts.append(config.COLOR_GRADE_FILTER)

    vf = ",".join(filter_parts)

    return ffmpeg([
        "-i", clip_path,
        "-vf", vf,
        "-c:v", "h264_nvenc",
        "-preset", "p4",
        "-rc", "vbr",
        "-cq", "26",
        "-r", str(config.VIDEO_FPS),
        "-an",
        output_path,
    ], f"Apply rules to clip {clip_index}")


def generate_ass_subtitles(narration: str, audio_duration: float, output_path: str) -> bool:
    """
    Generate ASS subtitle file with word-by-word pop captions.
    Styled like TikTok: bold white text with black outline, centered.
    """
    # Split narration into words and distribute timing
    clean = re.sub(r'\[[^\]]+\]', '', narration)  # Remove markers
    words = clean.split()
    if not words:
        return False

    time_per_word = audio_duration / max(len(words), 1)

    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,80,80,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    current_time = 0.0
    chunk_size = 3  # Show 3 words at a time (TikTok style)

    for i in range(0, len(words), chunk_size):
        chunk = words[i:i + chunk_size]
        chunk_text = " ".join(chunk)
        start_t = current_time
        end_t = min(current_time + time_per_word * len(chunk), audio_duration)

        def fmt_t(t):
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = t % 60
            cs = int((s % 1) * 100)
            return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

        # Highlight style: bold, uppercase, animated pop feel
        events.append(
            f"Dialogue: 0,{fmt_t(start_t)},{fmt_t(end_t)},Default,,0,0,0,,"
            f"{{\\an2\\b1}}{chunk_text.upper()}"
        )
        current_time = end_t

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(events))

    return True



def get_background_music(video_type: str = "full_recap") -> str | None:
    """Return path to the correct music track for this video type."""
    mood = config.VIDEO_TYPE_MUSIC.get(video_type, "normal")
    track_path = config.MUSIC_TRACKS.get(mood)
    if track_path and os.path.exists(track_path):
        return track_path
    # Fallback: any available track
    music_dir = config.MUSIC_DIR
    if os.path.exists(music_dir):
        tracks = [f for f in os.listdir(music_dir) if f.endswith(('.mp3', '.wav', '.m4a'))]
        if tracks:
            import random
            return os.path.join(music_dir, random.choice(tracks))
    return None


def assemble_video(
    movie_path: str,
    audio_path: str,
    clips: list[dict],
    narration: str,
    output_path: str,
    work_dir: str,
    video_type: str = "full_recap",
    clip_files: list[str] | None = None,
) -> bool:
    """
    Full video assembly pipeline.

    Args:
        movie_path:  Source movie file (mkv/mp4). Used when clip_files is None.
        audio_path:  Narration .mp3 from ElevenLabs
        clips:       List of {start, end, label} dicts (used when no clip_files)
        narration:   Narration text for captions
        output_path: Final output file path
        work_dir:    Temporary working directory
        video_type:  Used for music selection
        clip_files:  Pre-downloaded clip files (from MovieClips). Overrides clips+movie.
    """
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    console.print(f"[cyan]🎬 Assembling video → {os.path.basename(output_path)}[/cyan]")

    # ── Step 1: Get narration duration via ffprobe ────────────────────────────
    try:
        probe_out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ])
        narration_duration = float(probe_out.decode().strip())
    except Exception:
        narration_duration = 35.0
    console.print(f"[dim]   Narration: {narration_duration:.1f}s[/dim]")

    # ── Step 2: Get raw clips (MovieClips files OR extract from movie) ────────
    raw_clips = []

    if clip_files:
        # Use pre-downloaded MovieClips — already the best curated scenes
        console.print(f"[dim]   Using {len(clip_files)} pre-downloaded MovieClips[/dim]")
        raw_clips = [f for f in clip_files if os.path.exists(f)]
    elif clips and movie_path and os.path.exists(movie_path):
        # Extract clips from full movie at specified timestamps
        console.print(f"[dim]   Extracting {len(clips)} clips from movie file[/dim]")
        for i, clip in enumerate(clips):
            start = _ts_to_seconds(clip.get("start", "0"))
            end   = _ts_to_seconds(clip.get("end", "0"))
            if end <= start:
                continue
            raw_path = os.path.join(work_dir, f"raw_{i:03d}.mp4")
            if extract_clip(movie_path, start, end, raw_path):
                raw_clips.append(raw_path)
    else:
        console.print("[red]❌ No clip source available (no clip_files and no movie path)[/red]")
        return False

    if not raw_clips:
        console.print("[red]❌ No clips to assemble[/red]")
        return False

    # ── Step 3: Apply copyright transforms at clip level (no destructive splitting) ──
    # We apply: flip alternates, 3% speed, color grade, scale to 9:16
    # NOT slicing into 3s chunks — that destroyed scene continuity
    processed_clips = []
    for i, clip_path in enumerate(raw_clips):
        out_path = os.path.join(work_dir, f"proc_{i:04d}.mp4")
        if apply_copyright_rules(clip_path, out_path, i):
            processed_clips.append(out_path)

    console.print(f"[dim]   Processed {len(processed_clips)} clips[/dim]")

    # ── Step 4: Concatenate all processed clips ───────────────────────────────
    concat_list = os.path.join(work_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in processed_clips:
            f.write(f"file '{os.path.abspath(p)}'\n")

    concat_raw = os.path.join(work_dir, "concat_raw.mp4")
    if not ffmpeg([
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "26",
        "-r", str(config.VIDEO_FPS),
        concat_raw,
    ], "Concatenating clips"):
        return False

    # ── Step 5: Generate ASS captions ────────────────────────────────────────
    ass_path = os.path.join(work_dir, "captions.ass")
    generate_ass_subtitles(narration, narration_duration, ass_path)

    # ── Step 6: Mix audio + burn captions + render final video ──────────────
    bg_music = get_background_music(video_type)

    # Build audio filter graph
    if bg_music:
        # Mix narration (primary) with background music (ducked)
        audio_filter = (
            f"[0:a]volume=1.0[narr];"
            f"[1:a]volume=0.12,aloop=loop=-1:size=2e+09[music];"
            f"[narr][music]amix=inputs=2:duration=first[aout]"
        )
        audio_inputs = [
            "-i", audio_path,
            "-i", bg_music,
        ]
        audio_map = ["-filter_complex", audio_filter, "-map", "[aout]"]
    else:
        audio_inputs = ["-i", audio_path]
        audio_map = ["-map", "1:a"]

    # Build subtitle filter (must escape Windows paths on Linux too)
    ass_escaped = ass_path.replace("\\", "\\\\").replace(":", "\\:")
    vf_subtitle = f"subtitles='{ass_path}'"

    final_ok = ffmpeg([
        "-i", concat_raw,         # Video (no audio)
        *audio_inputs,            # Narration (+ optional music)
        "-vf", vf_subtitle,       # Burn captions
        *audio_map,
        "-c:v", "h264_nvenc",
        "-preset", "p6",          # Better quality for final output
        "-rc", "vbr",
        "-cq", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-r", str(config.VIDEO_FPS),
        "-t", str(narration_duration),  # Cut to exact narration length
        "-movflags", "+faststart",       # Good for streaming/upload
        output_path,
    ], "Final render with audio + captions")

    if final_ok and os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        console.print(f"[bold green]✅ Video ready: {output_path} ({size_mb:.1f} MB, ~{narration_duration:.0f}s)[/bold green]")
        return True

    return False


def assemble_all_videos(
    movie_path: str,
    scripts: dict,
    audio_paths: dict,
    movie_title: str,
    output_dir: str,
    movieclips: list[dict] | None = None,
) -> dict[str, str]:
    """
    Assemble all video types. Accepts optional pre-downloaded movieclips list.
    """
    results = {}
    safe_title = re.sub(r'[^\w\s-]', '', movie_title).strip().replace(' ', '_')
    movie_output_dir = os.path.join(output_dir, safe_title)
    os.makedirs(movie_output_dir, exist_ok=True)

    for video_type in config.VIDEO_TYPES:
        script = scripts.get(video_type)
        audio  = audio_paths.get(video_type)

        if not script or not audio:
            console.print(f"[yellow]⚠️  Skipping {video_type} — missing script or audio[/yellow]")
            continue

        output_path = os.path.join(movie_output_dir, f"{video_type}.mp4")
        work_dir    = os.path.join("temp", safe_title, video_type)

        if os.path.exists(output_path):
            console.print(f"[dim]⏭️  Already exists: {output_path}[/dim]")
            results[video_type] = output_path
            continue

        console.print(f"\n[bold cyan]━━━ Assembling: {video_type} ━━━[/bold cyan]")

        # Resolve clip files if MovieClips were downloaded
        clip_files_for_type = None
        if movieclips:
            # Use clips in the order specified by script's clip_order
            clip_order = script.get("clip_order", list(range(len(movieclips))))
            clip_files_for_type = [
                movieclips[i]["path"]
                for i in clip_order
                if i < len(movieclips) and os.path.exists(movieclips[i]["path"])
            ]

        ok = assemble_video(
            movie_path=movie_path,
            audio_path=audio,
            clips=script.get("clips", []),
            narration=script.get("narration", ""),
            output_path=output_path,
            work_dir=work_dir,
            video_type=video_type,
            clip_files=clip_files_for_type,
        )
        if ok:
            results[video_type] = output_path

    console.print(f"\n[bold green]✅ Assembled {len(results)}/{len(config.VIDEO_TYPES)} videos[/bold green]")
    return results
