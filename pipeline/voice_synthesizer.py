"""
voice_synthesizer.py
─────────────────────
Converts narration scripts to audio using ElevenLabs API.
"""

import os
import sys
import requests
import subprocess
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

def synthesize_narration(
    text: str,
    output_path: str,
    voice: str = None,
) -> bool:
    """
    Convert narration text to audio file using ElevenLabs TTS.

    Args:
        text: The narration script text
        output_path: Where to save the audio file
        voice: ElevenLabs voice ID

    Returns:
        True if successful
    """
    if not config.ELEVENLABS_API_KEY:
        console.print("[red]❌ ELEVENLABS_API_KEY is not set in config or .env[/red]")
        return False

    voice_id = voice or config.ELEVENLABS_VOICES["default"]

    # Clean up the narration text — remove stage directions
    import re
    clean_text = re.sub(r'\[PAUSE\]', '... ', text)
    clean_text = re.sub(r'\[[^\]]+\]', '', clean_text)  # Remove other [markers]
    clean_text = clean_text.strip()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    console.print(f"[cyan]🎙️  Synthesizing voice: ElevenLabs (Voice: {voice_id})[/cyan]")
    console.print(f"[dim]   Text length: {len(clean_text)} chars[/dim]")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": config.ELEVENLABS_API_KEY
    }
    data = {
        "text": clean_text,
        "model_id": "eleven_turbo_v2_5",  # Fastest, most natural conversational model
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }

    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            
            dur = get_audio_duration(output_path)
            console.print(f"[green]✅ Audio generated: {output_path} ({dur:.1f}s)[/green]")
            return True
        else:
            console.print(f"[red]❌ ElevenLabs API error {response.status_code}: {response.text}[/red]")
            return False
    except Exception as e:
        console.print(f"[red]❌ Voice synthesis failed: {e}[/red]")
        return False


def synthesize_all_scripts(
    scripts: dict,
    movie_title: str,
    output_dir: str,
    genre: str = "default",
) -> dict[str, str]:
    """
    Synthesize audio for all generated scripts.
    """
    voice = config.ELEVENLABS_VOICES.get(genre, config.ELEVENLABS_VOICES["default"])
    audio_paths = {}

    os.makedirs(output_dir, exist_ok=True)
    console.print(f"\n[bold]🎙️  Synthesizing voice for: {movie_title}[/bold]")
    console.print(f"[dim]Voice ID: {voice}[/dim]\n")

    for video_type, script in scripts.items():
        narration = script.get("narration", "")
        if not narration:
            console.print(f"[yellow]⚠️  No narration for {video_type}, skipping[/yellow]")
            continue

        output_path = os.path.join(output_dir, f"{video_type}_narration.mp3")
        success = synthesize_narration(
            text=narration,
            output_path=output_path,
            voice=voice,
        )
        if success:
            audio_paths[video_type] = output_path

    console.print(f"\n[bold green]✅ Synthesized {len(audio_paths)}/{len(scripts)} narrations[/bold green]")
    return audio_paths


def get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file in seconds using FFmpeg/ffprobe."""
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ])
        return float(out.decode("utf-8").strip())
    except Exception:
        return 0.0


if __name__ == "__main__":
    # Quick test
    test_text = "Nobody was ready for what happens in this movie."
    success = synthesize_narration(
        text=test_text,
        output_path="temp/test_narration.mp3",
    )
    if success:
        dur = get_audio_duration("temp/test_narration.mp3")
        print(f"Generated {dur:.1f}s of audio")
