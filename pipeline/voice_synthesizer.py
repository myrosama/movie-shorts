"""
voice_synthesizer.py
─────────────────────
Converts narration scripts to audio using Kokoro TTS — completely free, local,
Apache 2.0 licensed. No API costs, no rate limits, unlimited use.

Kokoro produces near-ElevenLabs quality at zero cost.
Voice is set to fast-paced to match the TikTok/Shorts narrator style.
"""

import os
import sys
import numpy as np
import soundfile as sf
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

# Lazy-load Kokoro so the rest of the pipeline can import without waiting
_kokoro_pipeline = None


def _get_pipeline(voice: str):
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        console.print("[cyan]🎙️  Loading Kokoro TTS model (first run may take ~30s)...[/cyan]")
        try:
            from kokoro import KPipeline
            _kokoro_pipeline = KPipeline(lang_code="a")  # 'a' = American English
        except ImportError:
            console.print("[red]❌ Kokoro not installed. Run: pip install kokoro soundfile[/red]")
            raise
    return _kokoro_pipeline


def synthesize_narration(
    text: str,
    output_path: str,
    voice: str = None,
    speed: float = None,
) -> bool:
    """
    Convert narration text to audio file using Kokoro TTS.

    Args:
        text: The narration script text
        output_path: Where to save the .wav file
        voice: Kokoro voice ID (defaults to config setting)
        speed: Speech speed multiplier (defaults to config setting)

    Returns:
        True if successful
    """
    voice = voice or config.KOKORO_VOICES["default"]
    speed = speed or config.KOKORO_SPEED

    # Clean up the narration text — remove stage directions
    import re
    clean_text = re.sub(r'\[PAUSE\]', '... ', text)
    clean_text = re.sub(r'\[[^\]]+\]', '', clean_text)  # Remove other [markers]
    clean_text = clean_text.strip()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    console.print(f"[cyan]🎙️  Synthesizing voice: {voice} @ {speed}x speed[/cyan]")
    console.print(f"[dim]   Text length: {len(clean_text)} chars[/dim]")

    try:
        pipeline = _get_pipeline(voice)

        # Generate audio in chunks for long texts
        all_audio = []
        silence = np.zeros(int(24000 * 0.15), dtype=np.float32)  # 150ms pause between sentences

        # Split on sentence boundaries for better prosody
        sentences = re.split(r'(?<=[.!?])\s+', clean_text)
        sentences = [s.strip() for s in sentences if s.strip()]

        for sentence in sentences:
            if not sentence:
                continue
            # Generate audio for this sentence
            generator = pipeline(sentence, voice=voice, speed=speed)
            for _, _, audio in generator:
                if audio is not None and len(audio) > 0:
                    all_audio.append(audio)
                    all_audio.append(silence)  # Brief pause between sentences

        if not all_audio:
            console.print("[red]❌ No audio generated[/red]")
            return False

        # Concatenate all chunks
        full_audio = np.concatenate(all_audio)

        # Save as WAV (24kHz, mono — standard for Kokoro)
        sf.write(output_path, full_audio, 24000)

        duration = len(full_audio) / 24000
        console.print(f"[green]✅ Audio generated: {output_path} ({duration:.1f}s)[/green]")
        return True

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

    Args:
        scripts: Dict of {video_type: script_dict} from script_generator.py
        movie_title: Movie title (for logging)
        output_dir: Directory to save audio files
        genre: Movie genre for voice selection

    Returns:
        Dict of {video_type: audio_file_path}
    """
    voice = config.KOKORO_VOICES.get(genre, config.KOKORO_VOICES["default"])
    audio_paths = {}

    os.makedirs(output_dir, exist_ok=True)
    console.print(f"\n[bold]🎙️  Synthesizing voice for: {movie_title}[/bold]")
    console.print(f"[dim]Voice: {voice} | Speed: {config.KOKORO_SPEED}x[/dim]\n")

    for video_type, script in scripts.items():
        narration = script.get("narration", "")
        if not narration:
            console.print(f"[yellow]⚠️  No narration for {video_type}, skipping[/yellow]")
            continue

        output_path = os.path.join(output_dir, f"{video_type}_narration.wav")
        success = synthesize_narration(
            text=narration,
            output_path=output_path,
            voice=voice,
            speed=config.KOKORO_SPEED,
        )
        if success:
            audio_paths[video_type] = output_path

    console.print(f"\n[bold green]✅ Synthesized {len(audio_paths)}/{len(scripts)} narrations[/bold green]")
    return audio_paths


def get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file in seconds."""
    try:
        data, samplerate = sf.read(audio_path)
        return len(data) / samplerate
    except Exception:
        return 0.0


if __name__ == "__main__":
    # Quick test
    test_text = """Nobody was ready for what happens in this movie.
    A man wakes up with no memory. No name. No past.
    And then he finds a photo. His own face. On a missing persons report.
    The panic sets in. Who is he? Who is looking for him?
    And most importantly... why does he not want to be found?"""

    success = synthesize_narration(
        text=test_text,
        output_path="temp/test_narration.wav",
    )
    if success:
        dur = get_audio_duration("temp/test_narration.wav")
        print(f"Generated {dur:.1f}s of audio")
