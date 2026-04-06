"""
script_generator.py  V4
────────────────────────
Algorithm:
  1. scene_analyzer.py scores EVERY scene in the full movie SRT
     for romantic tension, drama, mystery, conflict — 90s windows
  2. TMDB metadata provides real character names, plot summary, genres
  3. Gemini Phase 1: given scored scenes + TMDB plot, picks the 5 best
     story beats in chronological order (intro → conflict → climax → end)
  4. Gemini Phase 2 (with Narrator system prompt): writes the full
     character-driven 60s narration, referencing real names + real moments

YouTube clips are now only used as visual reference hints, NOT for timing.
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
    " retells a film — emotionally engaging, character-first, never robotic or listicle-style.\n\n"
    "Your style:\n"
    "- Open mid-scene with a character: 'This is [Name]. [One arresting fact about them].'\n"
    "- Build their world before introducing conflict\n"
    "- Present tense for immediacy: 'She walks in. The room goes silent.'\n"
    "- Emotional spike every 10-15 seconds\n"
    "- Short punchy sentences mixed with flowing ones\n"
    "- Never use: hey guys, welcome, today, basically, in conclusion\n"
    "- Contractions: he's, they've, didn't, what's\n"
    "- Pauses with '...' where the listener needs a beat\n"
    "- End on something that makes them want to watch the actual film\n"
    "- ~60 seconds total at natural TTS pace\n"
    "- Highlight any romantic tension or flirty/charged moments — those keep viewers hooked"
)

# ── Phase 1: Scene & Character Selection ─────────────────────────────────────
SCENE_SELECTOR_PROMPT = """You are a film editor selecting story beats for a 60-second recap.

MOVIE: {movie_title}
PLOT SUMMARY: {overview}
TAGLINE: {tagline}
GENRE: {genres}

CAST:
{cast_text}

SCORED SCENES (from full movie subtitle analysis, sorted chronologically):
{scenes_text}

Task:
1. Identify the 2-3 main characters by name from the cast list and dialogue
2. Select 5 scenes that together tell the complete emotional arc:
   - Scene 0: Best character INTRO (who are they, what's their world)
   - Scene 1: INCITING MOMENT (when everything changes)
   - Scene 2: RISING TENSION (conflict/romance escalates)
   - Scene 3: CLIMAX (peak emotional moment)
   - Scene 4: RESOLUTION/HOOK (ending — tease without full spoiler)

Prefer scenes with romantic/tension categories. Pick scenes in story order.

Return ONLY valid JSON:
{{
  "characters": [
    {{"name": "Character name from cast", "role": "their role in the story", "trait": "one defining quality"}}
  ],
  "story_beats": [
    {{"scene_index": 0, "beat": "intro", "start_ts": "HH:MM:SS.mmm", "end_ts": "HH:MM:SS.mmm", "why": "why this beat matters"}}
  ]
}}"""

# ── Phase 2: Narrative Script ─────────────────────────────────────────────────
SCRIPT_PROMPT = """Write a 60-second cinematic movie recap narration.

MOVIE: {movie_title}
GENRE: {genres}

CHARACTERS (use these exact names):
{chars_text}

STORY BEATS TO COVER (in this order):
{beats_text}

STRUCTURE:
1. Character intro — start mid-scene, present tense
   e.g. "This is Levi. Elite sniper. He's been stationed alone at this gorge for 500 days."
2. Their world (10s) — what's normal for them before everything changes
3. The meeting or inciting moment (10s) — when their world shifts
4. Rising tension / romantic or dramatic escalation (15s) — make it feel urgent
5. Climax (10s) — their highest stakes moment
6. Ending hook (8s) — tease the resolution, don't fully spoil it

VOICE: AI TTS (ElevenLabs Daniel — deep, authoritative)
Use '...' for natural pauses. Include *[CAMERA: zoom/cut/etc]* notes inline.
End with: "Follow for more."

Return ONLY valid JSON:
{{
  "video_type": "story_recap",
  "title": "Punchy title under 80 chars (story-first, not clickbait)",
  "description": "2 sentences teasing the story + relevant hashtags",
  "narration": "Full narration here...",
  "clips": [
    {{"start": "HH:MM:SS", "end": "HH:MM:SS", "label": "scene description"}}
  ],
  "tags": ["movierecap", "shorts", "{safetitle}", "film", "mustwatch"]
}}"""


def _sanitize(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)
    return re.sub(r' +', ' ', text).strip()


def _parse_json(raw: str) -> dict:
    if "```" in raw:
        raw = re.sub(r'```(?:json)?\s*', '', raw)
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', raw)
    return json.loads(raw.strip())


def generate_story_recap(movie_title, subtitle_text, tmdb_meta=None, clips=None, overview=""):
    """
    Full algorithm:
      - Score all scenes from SRT
      - Fetch TMDB metadata for real character names
      - Phase 1: Gemini selects best 5 story beats
      - Phase 2: Gemini writes the narration
    """
    from pipeline.scene_analyzer import find_best_scenes, build_scene_context

    console.print(f"[cyan]🤖 Analyzing full movie scenes for '{movie_title}'...[/cyan]")

    # ── Scene scoring from full SRT ───────────────────────────────────────────
    top_scenes = find_best_scenes(subtitle_text, n_scenes=12)
    if not top_scenes:
        console.print("[yellow]⚠️  No scenes scored — using raw subtitles[/yellow]")
        top_scenes = []

    scenes_text = build_scene_context(top_scenes, max_chars_per_scene=350) if top_scenes else "No scored scenes available."

    # ── TMDB metadata ─────────────────────────────────────────────────────────
    meta = tmdb_meta or {}
    plot     = _sanitize(meta.get("overview", overview or ""))
    tagline  = _sanitize(meta.get("tagline", ""))
    genres   = ", ".join(meta.get("genres", [])) or "Drama"
    cast     = meta.get("cast", [])
    cast_text = "\n".join(f"- {c['name']} as {c['character']}" for c in cast) or "- Cast not available"

    client = _get_client()
    safe_title = re.sub(r'[^a-zA-Z0-9]', '', movie_title).lower()

    # ── Phase 1: Select story beats ───────────────────────────────────────────
    p1 = SCENE_SELECTOR_PROMPT.format(
        movie_title=movie_title,
        overview=plot[:800],
        tagline=tagline,
        genres=genres,
        cast_text=cast_text,
        scenes_text=scenes_text,
    )

    characters = []
    story_beats = []
    try:
        r1 = _get_client().models.generate_content(
            model=GEMINI_MODEL, contents=p1,
            config=types.GenerateContentConfig(
                temperature=0.25, max_output_tokens=2048,
                response_mime_type="application/json"))
        d1 = _parse_json(r1.text)
        characters  = d1.get("characters", [])
        story_beats = d1.get("story_beats", [])
        console.print(f"[dim]   Characters: {[c['name'] for c in characters]}[/dim]")
        console.print(f"[dim]   Story beats: {len(story_beats)} selected[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠️  Phase 1 fallback ({e}) — using top scored scenes[/yellow]")
        characters = [{"name": "the protagonist", "role": "main character", "trait": "determined"}]
        story_beats = [
            {"scene_index": i, "beat": s.get("categories", {}) or "scene",
             "start_ts": s["start_ts"], "end_ts": s["end_ts"], "why": ""}
            for i, s in enumerate(top_scenes[:5])
        ]

    chars_text = "\n".join(
        f"- {c['name']}: {c['role']} — {c.get('trait','')}" for c in characters
    ) or f"- Main character from {movie_title}"

    beats_text = "\n".join(
        f"- Beat {i+1} [{b.get('beat','scene')}]: {b.get('start_ts','')} → {b.get('end_ts','')} | {b.get('why','')}"
        for i, b in enumerate(story_beats)
    )

    # ── Phase 2: Write narration ──────────────────────────────────────────────
    p2 = SCRIPT_PROMPT.format(
        movie_title=movie_title,
        genres=genres,
        chars_text=chars_text,
        beats_text=beats_text,
        safetitle=safe_title,
    )

    try:
        r2 = client.models.generate_content(
            model=GEMINI_MODEL, contents=p2,
            config=types.GenerateContentConfig(
                system_instruction=NARRATOR_SYSTEM,
                temperature=0.9, max_output_tokens=8192,
                response_mime_type="application/json"))
        result = _parse_json(r2.text)
        console.print(f"[green]✅ Narration: {len(result.get('narration',''))} chars[/green]")
        return result
    except Exception as e:
        console.print(f"[red]❌ Script writing failed: {e}[/red]")
        return None


def generate_all_scripts(movie_title, subtitle_text, overview="", clips=None, work_dir="", tmdb_meta=None):
    """Generate story_recap. Cache to disk."""
    results = {}
    scripts_dir = os.path.join(work_dir, "scripts") if work_dir else "temp/scripts"
    os.makedirs(scripts_dir, exist_ok=True)

    for vt in config.VIDEO_TYPES:
        cache = os.path.join(scripts_dir, f"{vt}_script.json")
        if os.path.exists(cache):
            with open(cache) as f:
                results[vt] = json.load(f)
            console.print(f"[dim]⏭️  Cached: {vt}[/dim]")
            continue

        script = generate_story_recap(movie_title, subtitle_text, tmdb_meta, clips, overview)

        if script:
            results[vt] = script
            with open(cache, "w") as f:
                json.dump(script, f, indent=2, ensure_ascii=False)
            console.print(f"[dim]💾 Saved: {cache}[/dim]")
        else:
            console.print(f"[yellow]⚠️  Failed: {vt}[/yellow]")

    console.print(f"\n[bold green]✅ Generated {len(results)}/{len(config.VIDEO_TYPES)} scripts[/bold green]")
    return results
