"""
script_generator.py
────────────────────
Uses Gemini 2.0 Flash (free tier) to analyze movie subtitles and generate
4 narration scripts — each with timestamps for where to cut the video clips.

Output per script: JSON with narration text + list of {start, end, label} clips
"""

import os
import sys
import json
import re
from google import genai
from google.genai import types
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

console = Console()

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client

GEMINI_MODEL = "gemini-2.5-flash"  # Has free quota on this project


# ─── Prompt Templates ────────────────────────────────────────────────────────

PROMPTS = {
    "full_recap": """You are a viral short-form video scriptwriter for YouTube Shorts and TikTok.

Given the following movie subtitles with timestamps, write a 90-second NARRATION SCRIPT for:
"The ENTIRE story of [MOVIE_TITLE] in 90 seconds"

RULES:
- Hook in the FIRST 5 words — make it impossible to scroll past
- Write in SHORT, punchy sentences (max 10 words each)
- Build tension → escalation → shocking resolution
- Include [PAUSE] markers where the narrator should pause (1 second)
- End with a gut-punch or twist reveal
- NEVER give away the ending in the first 10 seconds
- Tone: dramatic movie trailer narrator — deep, urgent

MOVIE: [MOVIE_TITLE]
OVERVIEW: [OVERVIEW]

SUBTITLES (with timestamps):
[SUBTITLES]

Return ONLY valid JSON in this exact format:
{
  "video_type": "full_recap",
  "title": "The ENTIRE story of [Movie Title] in 90 seconds 🎬",
  "description": "YouTube description with 3-4 sentences + hashtags",
  "narration": "Full narration script here...",
  "clips": [
    {"start": "00:05:23", "end": "00:05:45", "label": "Opening hook scene"},
    {"start": "00:23:10", "end": "00:23:35", "label": "Main conflict begins"},
    {"start": "00:45:00", "end": "00:45:20", "label": "Everything goes wrong"},
    {"start": "01:12:30", "end": "01:13:00", "label": "Climax moment"},
    {"start": "01:28:10", "end": "01:28:40", "label": "Final resolution"}
  ],
  "tags": ["movierecap", "moviesummary", "shorts"]
}""",

    "shocking_moments": """You are a viral short-form video scriptwriter for YouTube Shorts and TikTok.

Given the following movie subtitles, write a 60-second narration script for:
"[MOVIE_TITLE]'s 3 most jaw-dropping moments 🤯"

RULES:
- Start with: "This scene had people running out of theaters..."
  OR "Nobody was ready for what happens at [timestamp]..."
- Cover exactly 3 specific shocking/iconic moments
- Each moment gets a 1-sentence setup + dramatic reveal
- Use present tense for immediacy ("He walks in and...")
- Reactions should feel visceral — "nobody saw this coming", "the room goes silent"
- End with: "Which moment shocked YOU the most? Comment below 👇"

MOVIE: [MOVIE_TITLE]
OVERVIEW: [OVERVIEW]

SUBTITLES (with timestamps):
[SUBTITLES]

Return ONLY valid JSON:
{
  "video_type": "shocking_moments",
  "title": "[Movie Title]'s 3 most insane moments 🤯 #shorts",
  "description": "...",
  "narration": "...",
  "clips": [
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "Moment 1"},
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "Moment 2"},
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "Moment 3"}
  ],
  "tags": ["shocking", "moviemoments", "shorts"]
}""",

    "ending_explained": """You are a viral short-form video scriptwriter for YouTube Shorts and TikTok.

Given the following movie subtitles, write a 60-second narration for:
"The ending of [MOVIE_TITLE] EXPLAINED"

RULES:
- Start with: "The ending hits different when you realize..."
  OR "Here's what actually happens at the end of [Movie]..."
- Explain WHAT happened + WHY it matters + what it means for the characters
- Include any hidden symbolism or foreshadowing paid off
- Don't just describe events — explain the MEANING
- End: "Did you catch this the first time? Drop a 🔥 if you did"

MOVIE: [MOVIE_TITLE]
OVERVIEW: [OVERVIEW]

SUBTITLES (with timestamps):
[SUBTITLES]

Return ONLY valid JSON:
{
  "video_type": "ending_explained",
  "title": "The ending of [Movie Title] EXPLAINED 👁️ #shorts",
  "description": "...",
  "narration": "...",
  "clips": [
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "Final act setup"},
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "The ending revelation"},
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "Post-credits / final shot"}
  ],
  "tags": ["endingexplained", "movieending", "shorts"]
}""",

    "hidden_details": """You are a viral short-form video scriptwriter for YouTube Shorts and TikTok.

Given the following movie subtitles, write a 45-second narration for:
"You missed THIS detail in [MOVIE_TITLE]"

RULES:
- Start with: "Most people watch this 3 times before they notice..."
  OR "There's a detail in [Movie] that changes everything..."
- Focus on 1-2 subtle details: hidden foreshadowing, background clues, plot holes explained, subtle callbacks
- Make the viewer feel SMART for watching this
- End: "Rewatch this scene. You'll never see it the same way again."

MOVIE: [MOVIE_TITLE]
OVERVIEW: [OVERVIEW]

SUBTITLES (with timestamps):
[SUBTITLES]

Return ONLY valid JSON:
{
  "video_type": "hidden_details",
  "title": "You missed THIS in [Movie Title] 👀 #shorts",
  "description": "...",
  "narration": "...",
  "clips": [
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "The hidden detail scene"},
    {"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "Callback / payoff"}
  ],
  "tags": ["moviedetails", "youmissed", "shorts"]
}""",
}


def _truncate_subtitles(subtitle_text: str, max_chars: int = 25_000) -> str:
    """Truncate subtitles to fit within Gemini context window while keeping structure."""
    if len(subtitle_text) <= max_chars:
        return subtitle_text
    # Keep the beginning (setup) and end (climax/resolution) — most important for all 4 types
    half = max_chars // 3
    return subtitle_text[:half * 2] + "\n...[middle condensed]...\n" + subtitle_text[-half:]


def generate_script(
    video_type: str,
    movie_title: str,
    subtitle_text: str,
    overview: str = "",
) -> dict | None:
    """
    Generate a narration script for one video type using Gemini.
    Returns parsed JSON dict or None on failure.
    """
    if video_type not in PROMPTS:
        raise ValueError(f"Unknown video type: {video_type}")

    console.print(f"[cyan]🤖 Generating script: {video_type} for '{movie_title}'...[/cyan]")

    prompt = (
        PROMPTS[video_type]
        .replace("[MOVIE_TITLE]", movie_title)
        .replace("[OVERVIEW]", overview or "")
        .replace("[SUBTITLES]", _truncate_subtitles(subtitle_text))
    )

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.8,
                max_output_tokens=8192,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()

        # Strip markdown code fences if present
        if '```' in raw:
            # Remove ```json ... ``` wrapping
            raw = re.sub(r'```(?:json)?\s*', '', raw).strip()

        # Try direct parse first, then regex extraction
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[\s\S]+\}', raw)
            if not json_match:
                console.print(f"[red]❌ No JSON found in Gemini response[/red]")
                console.print(f"[dim]{raw[:200]}[/dim]")
                return None
            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError:
                # Last resort: truncated JSON — try to find last complete field
                console.print(f"[red]❌ JSON truncated or malformed — try reducing subtitle size[/red]")
                return None

        console.print(f"[green]✅ Script generated: {len(result.get('narration', ''))} chars, "
                      f"{len(result.get('clips', []))} clips[/green]")
        return result

    except json.JSONDecodeError as e:
        console.print(f"[red]❌ JSON parse error: {e}[/red]")
        return None
    except Exception as e:
        console.print(f"[red]❌ Gemini API error: {e}[/red]")
        return None


def generate_all_scripts(
    movie_title: str,
    subtitle_text: str,
    overview: str = "",
    output_dir: str = None,
) -> dict[str, dict]:
    """
    Generate all 4 scripts for a movie.
    Returns dict of {video_type: script_dict}
    Optionally saves each script as a JSON file to output_dir.
    """
    results = {}
    for vtype in config.VIDEO_TYPES:
        script = generate_script(vtype, movie_title, subtitle_text, overview)
        if script:
            results[vtype] = script
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
                path = os.path.join(output_dir, f"{vtype}_script.json")
                with open(path, "w") as f:
                    json.dump(script, f, indent=2)
                console.print(f"[dim]💾 Saved: {path}[/dim]")
        else:
            console.print(f"[yellow]⚠️  Failed to generate script for: {vtype}[/yellow]")

    console.print(f"\n[bold green]✅ Generated {len(results)}/4 scripts for '{movie_title}'[/bold green]")
    return results


if __name__ == "__main__":
    # Quick test with a fake subtitle
    test_srt = """[00:01:00] A young man arrives in a strange city.
[00:05:30] He discovers a dark secret about his past.
[00:15:00] The truth begins to unravel — nothing is what it seems.
[00:45:00] The villain reveals himself. Everything was planned.
[01:15:00] In a desperate final move, he risks everything.
[01:25:00] The twist nobody saw coming — he was the villain all along."""

    scripts = generate_all_scripts(
        movie_title="Test Movie",
        subtitle_text=test_srt,
        overview="A man discovers he is not who he thinks he is.",
        output_dir="temp/test_scripts",
    )
    for vtype, s in scripts.items():
        print(f"\n--- {vtype} ---")
        print(f"Title: {s.get('title')}")
        print(f"Narration preview: {s.get('narration', '')[:200]}...")
