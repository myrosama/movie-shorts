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
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
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


def get_background_music() -> str | None:
    """Return path to a random background music track, or None."""
    music_dir = config.MUSIC_DIR
    if not os.path.exists(music_dir):
        return None
    tracks = [f for f in os.listdir(music_dir) if f.endswith(('.mp3', '.wav', '.m4a'))]
    if not tracks:
        return None
    import random
    return os.path.join(music_dir, random.choice(tracks))


def assemble_video(
    movie_path: str,
    audio_path: str,
    clips: list[dict],
    narration: str,
    output_path: str,
    work_dir: str,
) -> bool:
    """
    Full video assembly pipeline:
    1. Extract clips at timestamps
    2. Apply copyright rules (cut into 3s, delete alternates, flip alternates, speed)
    3. Concatenate surviving clips
    4. Overlay narration audio + background music
    5. Burn ASS captions
    6. Output final vertical video

    Args:
        movie_path:  Source .mp4 movie file
        audio_path:  Narration .wav from Kokoro TTS
        clips:       List of {start, end, label} dicts from script
        narration:   Narration text (for captions)
        output_path: Final output file path
        work_dir:    Temporary working directory
    """
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    console.print(f"[cyan]🎬 Assembling video → {os.path.basename(output_path)}[/cyan]")
    console.print(f"[dim]   Source clips: {len(clips)}, Movie: {os.path.basename(movie_path)}[/dim]")

    # ── Step 1: Get narration duration (drives target video length) ──────────
    try:
        import soundfile as sf
        data, sr = sf.read(audio_path)
        narration_duration = len(data) / sr
    except Exception:
        narration_duration = 35.0  # Safe default ~35s
    console.print(f"[dim]   Narration duration: {narration_duration:.1f}s[/dim]")

    # ── Step 2: Extract raw clips from movie ────────────────────────────────
    raw_clips = []
    for i, clip in enumerate(clips):
        start = _ts_to_seconds(clip.get("start", "0"))
        end   = _ts_to_seconds(clip.get("end", "0"))
        if end <= start:
            continue

        raw_path = os.path.join(work_dir, f"raw_{i:03d}.mp4")
        if extract_clip(movie_path, start, end, raw_path):
            raw_clips.append(raw_path)

    if not raw_clips:
        console.print("[red]❌ No clips extracted[/red]")
        return False

    console.print(f"[dim]   Extracted {len(raw_clips)} raw clips[/dim]")

    # ── Step 3: Slice each raw clip into 3-second segments (RULE 2: CUT) ────
    three_sec_clips = []
    for raw_path in raw_clips:
        # Get duration of this clip
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", raw_path],
            capture_output=True, text=True
        )
        try:
            clip_dur = float(probe.stdout.strip())
        except Exception:
            clip_dur = 10.0

        seg_duration = 3.0
        offset = 0.0
        seg_idx = 0
        while offset < clip_dur:
            seg_path = os.path.join(
                work_dir,
                f"seg_{len(three_sec_clips):04d}.mp4"
            )
            actual_dur = min(seg_duration, clip_dur - offset)
            ok = ffmpeg([
                "-ss", str(offset),
                "-i", raw_path,
                "-t", str(actual_dur),
                "-c:v", "copy", "-an",
                seg_path,
            ])
            if ok and os.path.exists(seg_path):
                three_sec_clips.append((seg_path, seg_idx))
                seg_idx += 1
            offset += seg_duration

    console.print(f"[dim]   Sliced into {len(three_sec_clips)} × 3s segments[/dim]")

    # ── Step 4: RULE 3 — Delete alternate segments (keep 0,2,4... drop 1,3,5) ──
    kept_clips = [
        (path, seg_idx)
        for path, seg_idx in three_sec_clips
        if seg_idx % 2 == 0   # Keep even-indexed segments, delete odd
    ]
    console.print(f"[dim]   After alternating delete: {len(kept_clips)} segments kept[/dim]")

    if not kept_clips:
        console.print("[red]❌ All clips were deleted — using all segments[/red]")
        kept_clips = three_sec_clips[:8]  # Fallback

    # ── Step 5: Apply copyright rules + scale to each kept clip ─────────────
    processed_clips = []
    for i, (clip_path, seg_idx) in enumerate(kept_clips):
        out_path = os.path.join(work_dir, f"proc_{i:04d}.mp4")
        if apply_copyright_rules(clip_path, out_path, i):
            processed_clips.append(out_path)

    console.print(f"[dim]   Processed {len(processed_clips)} clips[/dim]")

    # ── Step 6: Create concat list and join all clips ────────────────────────
    concat_list = os.path.join(work_dir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in processed_clips:
            f.write(f"file '{os.path.abspath(p)}'\n")

    concat_raw = os.path.join(work_dir, "concat_raw.mp4")
    if not ffmpeg([
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-r", str(config.VIDEO_FPS),
        concat_raw,
    ], "Concatenating clips"):
        return False

    # ── Step 7: Generate ASS caption file ───────────────────────────────────
    ass_path = os.path.join(work_dir, "captions.ass")
    generate_ass_subtitles(narration, narration_duration, ass_path)

    # ── Step 8: Mix narration + background music + burn captions ────────────
    bg_music = get_background_music()

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
        "-c:v", "libx264",
        "-preset", "slow",        # Better quality for final output
        "-crf", "20",
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
) -> dict[str, str]:
    """
    Assemble all video types for a movie.

    Returns:
        Dict of {video_type: output_path} for successfully assembled videos
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
        ok = assemble_video(
            movie_path=movie_path,
            audio_path=audio,
            clips=script.get("clips", []),
            narration=script.get("narration", ""),
            output_path=output_path,
            work_dir=work_dir,
        )
        if ok:
            results[video_type] = output_path

    console.print(f"\n[bold green]✅ Assembled {len(results)}/{len(config.VIDEO_TYPES)} videos[/bold green]")
    return results
