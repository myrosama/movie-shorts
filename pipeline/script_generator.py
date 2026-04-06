"""
script_generator.py  V3
────────────────────────
Character-driven story recap generator.

Structure per video:
  - Character intro: "This is [Name]. He/She is [role]..."
  - Their world: set the scene and stakes
  - The meeting / inciting incident
  - Main conflict escalation
  - Climax moment
  - Ending (without fully spoiling — leave a hook)

Two-phase Gemini:
  Phase 1: Extract key characters and 5 best emotional scenes from clip metadata
  Phase 2: Write the full character-driven narrative script
"""
import os, sys, json, re
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

GEMINI_MODEL = "gemini-2.5-flash"

# ── System Instruction ────────────────────────────────────────────────────────
NARRATOR_SYSTEM = (
    "You are a cinematic short-form storyteller. You narrate movies the way a captivating friend"
    " retells a film — emotionally engaging, character-first, never robotic or listicle-style."
    "\n\nYour narration style:"
    "\n- Always introduce the main character(s) as real people first: 'This is Marcus. He hasn't"
    " slept in three days and he doesn't know why.'"
    "\n- Build the world around them before introducing conflict"
    "\n- Use present tense for immediacy: 'He walks in and the room goes dead silent.'"
    "\n- Emotional spikes every 10-15 seconds — a reveal, a twist, a gut punch"
    "\n- Short punchy sentences mixed with longer flowing ones"
    "\n- Never use: 'Hey guys', 'welcome', 'today we', 'in conclusion', 'basically', 'literally'"
    "\n- Natural spoken contractions: 'he's', 'they've', 'didn't', 'what's'"
    "\n- Pauses marked with '...' where the listener needs to absorb something"
    "\n- End on something that makes the viewer want to watch the actual movie"
    "\n- Total read time: approximately 60 seconds (AI TTS at natural pace)"
)

# ── Phase 1: Character & Scene Extraction ────────────────────────────────────
CHAR_EXTRACTOR_PROMPT = """You are analyzing movie clips to extract key story information.

MOVIE: {movie_title}
MOVIE OVERVIEW: {overview}

AVAILABLE CLIPS:
{clips_summary}

Extract:
1. The 2-3 MAIN characters (name, role, one-line personality description)
2. The 5 most emotionally significant clips for telling the full story arc
   (need: intro scene + conflict scene + climax scene + resolution scene)

Return ONLY valid JSON:
{{
  "characters": [
    {{"name": "Character name", "role": "e.g. elite sniper", "trait": "one-line character essence"}},
    {{"name": "Character name", "role": "e.g. former intelligence agent", "trait": "one-line character essence"}}
  ],
  "selected_clips": [
    {{"clip_index": 0, "story_beat": "character intro", "reason": "Shows who they are before everything changes"}},
    {{"clip_index": 2, "story_beat": "inciting incident", "reason": "The moment two worlds collide"}},
    {{"clip_index": 4, "story_beat": "main conflict", "reason": "Stakes are highest here"}},
    {{"clip_index": 6, "story_beat": "climax", "reason": "Everything comes to a head"}},
    {{"clip_index": 7, "story_beat": "resolution/hook", "reason": "Leaves viewer wanting more"}}
  ]
}}"""

# ── Phase 2: Narrative Script ─────────────────────────────────────────────────
SCRIPT_PROMPT = """Write a 60-second cinematic narration for this movie.

MOVIE: {movie_title}

MAIN CHARACTERS:
{characters_text}

STORY BEATS TO COVER (in order):
{beats_text}

STRUCTURE TO FOLLOW:
1. Open with character intro (not a hook question — start mid-story, present tense)
   Example: "This is Levi. Elite sniper. Stationed alone at a gorge no one's supposed to know about."
2. Establish their world and what's at stake (10-15 seconds)
3. The meeting or inciting incident — when their world changes (10 seconds)
4. Main conflict builds — tension escalates (15 seconds)  
5. Climax moment — the peak emotional beat (10 seconds)
6. Ending hook — don't fully spoil, leave them wanting the movie (5-8 seconds)
   Example: "And what they find at the bottom of that gorge... changes everything."

Platform: TikTok / YouTube Shorts / Instagram Reels
Voice: AI TTS (ElevenLabs) — use '...' for natural pauses
CTA at the end: "Follow for more movie recaps."

Return ONLY valid JSON:
{{
  "video_type": "story_recap",
  "title": "Short punchy YouTube title that teases the story (max 80 chars)",
  "description": "2 sentence teaser + 5 hashtags",
  "narration": "Full 60-second spoken narration. Include *[CAMERA: zoom / cut / etc]* notes for the editor. Use ... for pauses.",
  "clip_order": [0, 1, 2, 3, 4],
  "tags": ["movierecap", "shorts", "{safetitle}", "movieclips", "mustwatch"]
}}"""


def generate_story_recap(movie_title, clips, overview=""):
    """Two-phase character-driven story recap generation."""
    console.print(f"[cyan]🤖 Generating story recap for '{movie_title}'...[/cyan]")
    client = _get_client()

    # Build clips summary
    lines = ["AVAILABLE CLIPS:"]
    for i, c in enumerate(clips):
        d = int(c.get("duration", 0)); m, s = divmod(d, 60)
        lines.append(f"[CLIP {i}] {m}m{s:02d}s | {c.get('title','')}")
        if c.get("description"):
            lines.append(f"         {c['description'].replace(chr(10),' ')[:180]}")
    clips_text = "\n".join(lines)

    # ── Phase 1: Extract characters and best clips ────────────────────────────
    p1 = CHAR_EXTRACTOR_PROMPT.format(
        movie_title=movie_title,
        overview=overview or "Not available",
        clips_summary=clips_text,
    )
    try:
        r1 = client.models.generate_content(
            model=GEMINI_MODEL, contents=p1,
            config=types.GenerateContentConfig(
                temperature=0.2, max_output_tokens=1024,
                response_mime_type="application/json"))
        story_data = json.loads(r1.text.strip())
        characters = story_data.get("characters", [])
        selected   = story_data.get("selected_clips", [])
        console.print(f"[dim]   Characters found: {[c['name'] for c in characters]}[/dim]")
        console.print(f"[dim]   Story beats selected: {len(selected)} clips[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠️  Phase 1 fallback: {e}[/yellow]")
        characters = []
        selected = [{"clip_index": i, "story_beat": "scene", "reason": ""} for i in range(min(len(clips), 5))]

    # Build text for phase 2
    chars_text = "\n".join(
        f"- {c['name']}: {c['role']} — {c.get('trait','')}"
        for c in characters
    ) or f"- Main characters from {movie_title}"

    beats_text = "\n".join(
        f"- Beat {i+1} [{s.get('story_beat','scene')}]: "
        f"Clip {s.get('clip_index',i)} — {clips[s.get('clip_index',i)]['title'][:70] if s.get('clip_index',0) < len(clips) else ''}"
        f" ({s.get('reason','')})"
        for i, s in enumerate(selected)
    )

    safe_title = re.sub(r'[^a-zA-Z0-9]', '', movie_title).lower()

    # ── Phase 2: Write the narrative script ──────────────────────────────────
    p2 = SCRIPT_PROMPT.format(
        movie_title=movie_title,
        characters_text=chars_text,
        beats_text=beats_text,
        safetitle=safe_title,
    )
    try:
        r2 = client.models.generate_content(
            model=GEMINI_MODEL, contents=p2,
            config=types.GenerateContentConfig(
                system_instruction=NARRATOR_SYSTEM,
                temperature=0.85, max_output_tokens=8192,
                response_mime_type="application/json"))
        raw = r2.text.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        result = json.loads(raw)
        console.print(f"[green]✅ Narration: {len(result.get('narration',''))} chars[/green]")
        return result
    except Exception as e:
        console.print(f"[red]❌ Script generation failed: {e}[/red]")
        return None


def generate_story_from_subtitles(movie_title, subtitle_text, overview=""):
    """Fallback when no clips available — use subtitle timestamps."""
    console.print(f"[yellow]📝 Subtitle fallback for '{movie_title}'...[/yellow]")
    client = _get_client()

    safe_title = re.sub(r'[^a-zA-Z0-9]', '', movie_title).lower()

    prompt = (
        f"Movie: {movie_title}\nOverview: {overview or 'Not available'}\n\n"
        f"Subtitles (with timestamps):\n{subtitle_text[:22000]}\n\n"
        f"Write a 60-second character-driven narration for this movie following this structure:\n"
        f"1. Introduce the main character(s) by name and role\n"
        f"2. Establish their world and what's at stake\n"
        f"3. The inciting incident that changes everything\n"
        f"4. Main conflict escalation\n"
        f"5. Climax moment\n"
        f"6. Ending hook that leaves them wanting to watch\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"video_type":"story_recap","title":"Short punchy title max 80 chars",'
        f'"description":"2 sentence teaser + hashtags",'
        f'"narration":"Full 60s narration with *[CAMERA: notes]* and ... pauses. CTA at end.",'
        f'"clips":[{{"start":"HH:MM:SS","end":"HH:MM:SS","label":"scene desc"}}],'
        f'"tags":["movierecap","shorts","{safe_title}","movieclips","mustwatch"]}}'
    )
    try:
        r = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=NARRATOR_SYSTEM,
                temperature=0.85, max_output_tokens=8192,
                response_mime_type="application/json"))
        raw = r.text.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        result = json.loads(raw)
        console.print(f"[green]✅ Narration: {len(result.get('narration',''))} chars[/green]")
        return result
    except Exception as e:
        console.print(f"[red]❌ Subtitle fallback failed: {e}[/red]")
        return None


def generate_all_scripts(movie_title, subtitle_text, overview="", clips=None, work_dir=""):
    """Generate story_recap script. Cache to disk."""
    results = {}
    scripts_dir = os.path.join(work_dir, "scripts") if work_dir else "temp/scripts"
    os.makedirs(scripts_dir, exist_ok=True)

    for vt in config.VIDEO_TYPES:
        cache = os.path.join(scripts_dir, f"{vt}_script.json")
        if os.path.exists(cache):
            with open(cache) as f:
                results[vt] = json.load(f)
            console.print(f"[dim]⏭️  Cached script: {vt}[/dim]")
            continue

        if clips:
            script = generate_story_recap(movie_title, clips, overview)
        else:
            script = generate_story_from_subtitles(movie_title, subtitle_text, overview)

        if script:
            results[vt] = script
            with open(cache, "w") as f:
                json.dump(script, f, indent=2, ensure_ascii=False)
            console.print(f"[dim]💾 Saved: {cache}[/dim]")
        else:
            console.print(f"[yellow]⚠️  Failed: {vt}[/yellow]")

    console.print(f"\n[bold green]✅ Generated {len(results)}/{len(config.VIDEO_TYPES)} scripts[/bold green]")
    return results
