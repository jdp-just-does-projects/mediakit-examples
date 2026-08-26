"""Screenplay step: ask the LLM for a structured plan, validate it, and turn each
shot into a Seedance 2.5 prompt whose @ImageN tokens line up with the reference
images the pipeline attaches.

Two constraints drive everything here:

1. Seedance has no memory between shots. Consistency therefore comes from (a) a
   `style_bible` paragraph repeated verbatim in every image and video prompt and
   (b) one Seedream character sheet per character, attached to every shot that
   character appears in and addressed as `@Image1`, `@Image2`, ... in the order
   the images appear in the request.

2. Seedance 2.5 infers the *task type* from prompt wording. Words like
   edit/add/remove/extend/continue mark a request as "video editing" or "video
   extension", which silently forces `ratio: adaptive` and/or `duration: -1` —
   the request is accepted, billed, and produces the wrong thing. So those words
   are forbidden everywhere and the output is linted for them.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable

# --------------------------------------------------------------------- forbidden

FORBIDDEN = ["edit", "add", "insert", "remove", "delete", "modify", "replace", "change to", "extend", "continue"]

# Stem-aware so "adds", "editing", "continued", "modifying", "changed into" are caught too.
FORBIDDEN_RE = re.compile(
    r"\b(edit(?:s|ed|ing)?|add(?:s|ed|ing)?|insert(?:s|ed|ing)?|remov(?:e|es|ed|ing)|delet(?:e|es|ed|ing)"
    r"|modif(?:y|ies|ied|ying)|replac(?:e|es|ed|ing)|chang(?:e|es|ed|ing)\s+(?:in)?to|extend(?:s|ed|ing)?"
    r"|continu(?:e|es|ed|ing))\b",
    re.IGNORECASE,
)

_SCRUB = {
    "edit": "adjust", "add": "bring in", "insert": "slide in", "remov": "take away", "delet": "clear",
    "modif": "reshape", "replac": "swap", "chang": "become", "extend": "stretch", "continu": "carry on",
}


def scrub_forbidden(text: str) -> str:
    """Last-resort word swap when a forbidden word survives validation and repair."""
    def sub(m: re.Match) -> str:
        word = m.group(0).lower()
        for stem, repl in _SCRUB.items():
            if word.startswith(stem):
                return repl
        return "adjust"
    return FORBIDDEN_RE.sub(sub, text)


def forbidden_hits(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in FORBIDDEN_RE.finditer(text or "")})


# ------------------------------------------------------------------------ model

CHAR_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]{1,15})\}")

MAX_ACTION_CHARS = 260
MAX_FIELD_CHARS = 220
MAX_STYLE_WORDS = 100
MAX_WINDOWS = 5
MIN_WINDOW_SECONDS = 4


@dataclass
class Character:
    id: str
    name: str
    description: str
    wardrobe: str
    distinguishing_feature: str
    voice: str = ""


@dataclass
class TimelineWindow:
    start: int
    end: int
    action: str


@dataclass
class DialogueLine:
    character: str
    start: int
    end: int
    line: str


@dataclass
class Shot:
    index: int
    summary: str
    location: str
    time_of_day: str
    characters: list[str]
    timeline: list[TimelineWindow]
    camera: str
    continuity: str
    audio: str
    dialogue: list[DialogueLine] = field(default_factory=list)
    duration: int = 0
    # Filled in by compose_shot_prompt(); kept in screenplay.json so hand edits stick.
    seedance_prompt: str = ""
    reference_ids: list[str] = field(default_factory=list)


@dataclass
class Screenplay:
    title: str
    logline: str
    style_bible: str
    characters: list[Character]
    shots: list[Shot]

    def character(self, cid: str) -> Character | None:
        return next((c for c in self.characters if c.id == cid), None)


# ---------------------------------------------------------------------- prompts

SCHEMA_TEXT = """{
  "title": "string",
  "logline": "one sentence",
  "style_bible": "50-80 words: medium and rendering look (stylised, non-photoreal), palette, lighting, lens habits, era/setting texture. Reused verbatim in every prompt.",
  "characters": [
    {"id": "hero", "name": "string", "description": "one sentence of stable physical identity: age, build, skin tone, hair colour and cut, face shape, eye colour",
     "wardrobe": "one outfit worn in every shot", "distinguishing_feature": "one always-visible marker", "voice": "how they sound"}
  ],
  "shots": [
    {"index": 1, "summary": "one line", "location": "string", "time_of_day": "string",
     "characters": ["hero"],
     "timeline": [{"start": 0, "end": 8, "action": "what {hero} does, using placeholders only"}],
     "camera": "lens, movement, framing for the whole shot",
     "continuity": "light, palette, props and wardrobe state that must match neighbouring shots",
     "audio": "ambience and music bed",
     "dialogue": [{"character": "hero", "start": 3, "end": 6, "line": "under 12 words"}]}
  ]
}"""


def system_prompt() -> str:
    return (
        "You are a screenwriter and storyboard artist producing a short AI-generated film. Each shot will be "
        "rendered by a text-and-reference-image-to-video model as an independent generation; the model has no "
        "memory between shots. Return ONLY a JSON object matching the schema in the user message - no markdown "
        "fences, no commentary.\n\n"
        "Rules:\n"
        "1. Every shot must stand alone: restate location, time of day and light in each shot's fields. Never "
        "write \"as before\" or \"the previous shot\".\n"
        "2. Refer to characters ONLY by placeholder - {hero}, {mentor} - never by name inside timeline actions, "
        "camera, continuity or dialogue. Names go in the characters list only. Use lowercase snake_case ids.\n"
        "3. Character identity is fixed: one wardrobe, one distinguishing feature, no costume or hair changes.\n"
        "4. The timeline for each shot is gapless integer seconds from 0 to SHOT_SECONDS, at most "
        f"{MAX_WINDOWS} windows, each at least {MIN_WINDOW_SECONDS} seconds. Describe one clear action and one "
        "camera motion per window, and keep every action under 200 characters.\n"
        "5. The following words are forbidden anywhere in the JSON, in any form (add, adds, adding...): "
        + ", ".join(FORBIDDEN)
        + ". Say \"brings in\", \"takes away\", \"becomes\", \"carries on\" instead.\n"
        "6. No on-screen text, captions, subtitles or logos. Dialogue is spoken, short (under 12 words per line), "
        "at most 2 lines per shot, and each line has a time window inside the shot.\n"
        "7. The style bible must describe a stylised, non-photorealistic look (animation, painterly, graphic "
        "novel, claymation...) unless the user's idea demands otherwise; it is reused verbatim everywhere, so "
        "keep it under 80 words.\n"
        "8. Write in English only."
    )


def _beats(n: int) -> str:
    if n <= 1:
        return "Structure: a single self-contained scene with a beginning, a turn and a final image."
    beats = ["shot 1 establishes place and protagonist"]
    for i in range(2, n - 1):
        beats.append(f"shot {i} raises the stakes or brings in a complication")
    if n >= 3:
        beats.append(f"shot {n - 1} is the turn or confrontation")
    beats.append(f"shot {n} resolves with a final image that echoes shot 1")
    return "Structure: " + "; ".join(beats) + "."


def user_prompt(idea: str, *, shots: int, shot_seconds: int, ratio: str, max_characters: int,
                style_hint: str | None = None) -> str:
    lines = [
        f"Story idea: {idea}",
        f"Format: {shots} shots, each exactly {shot_seconds} seconds (SHOT_SECONDS = {shot_seconds}), aspect "
        f"{ratio}, total {shots * shot_seconds} seconds.",
        f"Cast: at most {max_characters} characters. Fewer is better for consistency; every character must appear "
        "in at least two shots.",
    ]
    if style_hint:
        lines.append(f"Style direction from the director: {style_hint}")
    lines.append(_beats(shots))
    lines.append("Return this JSON schema exactly:\n" + SCHEMA_TEXT)
    return "\n".join(lines)


def portrait_prompt(c: Character, style_bible: str) -> str:
    """Seedream prompt for the character sheet that becomes @ImageN in every shot."""
    return (
        f"{style_bible.strip().rstrip('.')}. Full-body character reference sheet of {c.name}: {c.description.strip().rstrip('.')}. "
        f"Wearing {c.wardrobe.strip().rstrip('.')}. {c.distinguishing_feature.strip().rstrip('.')}. "
        "Standing, neutral pose, facing camera, arms relaxed, plain seamless studio background, even soft light, "
        "single person, whole body in frame, no text, no logo."
    )


# ---------------------------------------------------------------------- parsing


def extract_json(text: str) -> dict:
    """Tolerate ``` fences and prose around the object."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in the model output")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def _int(v, default=0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def parse_screenplay(data: dict) -> Screenplay:
    characters = [
        Character(
            id=_str(c.get("id")).lower(), name=_str(c.get("name")), description=_str(c.get("description")),
            wardrobe=_str(c.get("wardrobe")), distinguishing_feature=_str(c.get("distinguishing_feature")),
            voice=_str(c.get("voice")),
        )
        for c in (data.get("characters") or []) if isinstance(c, dict)
    ]
    shots = []
    for i, s in enumerate(data.get("shots") or [], start=1):
        if not isinstance(s, dict):
            continue
        timeline = [TimelineWindow(_int(w.get("start")), _int(w.get("end")), _str(w.get("action")))
                    for w in (s.get("timeline") or []) if isinstance(w, dict)]
        dialogue = [DialogueLine(_str(d.get("character")).lower(), _int(d.get("start")), _int(d.get("end")), _str(d.get("line")))
                    for d in (s.get("dialogue") or []) if isinstance(d, dict) and _str(d.get("line"))]
        chars = s.get("characters") or []
        if isinstance(chars, str):
            chars = [chars]
        shots.append(Shot(
            index=_int(s.get("index"), i), summary=_str(s.get("summary")), location=_str(s.get("location")),
            time_of_day=_str(s.get("time_of_day")), characters=list(dict.fromkeys(_str(c).lower() for c in chars)),
            timeline=timeline, camera=_str(s.get("camera")), continuity=_str(s.get("continuity")),
            audio=_str(s.get("audio")), dialogue=dialogue,
            duration=max((w.end for w in timeline), default=0),
            seedance_prompt=_str(s.get("seedance_prompt")), reference_ids=list(s.get("reference_ids") or []),
        ))
    return Screenplay(title=_str(data.get("title")), logline=_str(data.get("logline")),
                      style_bible=_str(data.get("style_bible")), characters=characters, shots=shots)


# ------------------------------------------------------------------- validation


def validate(sp: Screenplay, *, shots: int, shot_seconds: int, max_characters: int) -> list[str]:
    v: list[str] = []
    ids = [c.id for c in sp.characters]
    if not 1 <= len(ids) <= max_characters:
        v.append(f"need 1..{max_characters} characters, got {len(ids)}")
    if len(set(ids)) != len(ids):
        v.append(f"duplicate character ids: {ids}")
    for c in sp.characters:
        if not CHAR_ID_RE.match(c.id):
            v.append(f"character id {c.id!r} must match {CHAR_ID_RE.pattern}")
        for fname in ("name", "description", "wardrobe", "distinguishing_feature"):
            if not getattr(c, fname):
                v.append(f"character {c.id!r}: {fname} is empty")
    if not sp.style_bible:
        v.append("style_bible is empty")
    elif len(sp.style_bible.split()) > MAX_STYLE_WORDS:
        v.append(f"style_bible is {len(sp.style_bible.split())} words; keep it under 80")

    if len(sp.shots) != shots:
        v.append(f"need exactly {shots} shots, got {len(sp.shots)}")
    if [s.index for s in sp.shots] != list(range(1, len(sp.shots) + 1)):
        v.append(f"shot indices must be 1..{shots} in order, got {[s.index for s in sp.shots]}")

    for s in sp.shots:
        tag = f"shot {s.index}"
        unknown = [c for c in s.characters if c not in ids]
        if unknown:
            v.append(f"{tag}: unknown character ids {unknown}")
        if not s.timeline:
            v.append(f"{tag}: timeline is empty")
        else:
            if s.timeline[0].start != 0:
                v.append(f"{tag}: timeline must start at 0")
            if s.timeline[-1].end != shot_seconds:
                v.append(f"{tag}: timeline must end at exactly {shot_seconds}s, ends at {s.timeline[-1].end}s")
            if len(s.timeline) > MAX_WINDOWS:
                v.append(f"{tag}: at most {MAX_WINDOWS} timeline windows")
            for a, b in zip(s.timeline, s.timeline[1:]):
                if a.end != b.start:
                    v.append(f"{tag}: timeline gap/overlap between {a.end}s and {b.start}s")
            for w in s.timeline:
                if w.end - w.start < MIN_WINDOW_SECONDS:
                    v.append(f"{tag}: window [{w.start}-{w.end}] shorter than {MIN_WINDOW_SECONDS}s")
                if not w.action:
                    v.append(f"{tag}: window [{w.start}-{w.end}] has no action")
                elif len(w.action) > MAX_ACTION_CHARS:
                    v.append(f"{tag}: action in [{w.start}-{w.end}] is {len(w.action)} chars; keep under 200")
        for fname in ("camera", "continuity", "audio"):
            if len(getattr(s, fname)) > MAX_FIELD_CHARS:
                v.append(f"{tag}: {fname} is too long; keep under 200 characters")
        if len(s.dialogue) > 2:
            v.append(f"{tag}: at most 2 dialogue lines")
        for d in s.dialogue:
            if d.character not in s.characters:
                v.append(f"{tag}: dialogue by {d.character!r} who is not in this shot's characters")
            if not (0 <= d.start < d.end <= shot_seconds):
                v.append(f"{tag}: dialogue window [{d.start}-{d.end}] outside 0..{shot_seconds}")
            if len(d.line.split()) > 14:
                v.append(f"{tag}: dialogue line too long: {d.line!r}")
        texts = [w.action for w in s.timeline] + [s.camera, s.continuity, s.summary] + [d.line for d in s.dialogue]
        for t in texts:
            for ph in PLACEHOLDER_RE.findall(t):
                if ph not in s.characters:
                    v.append(f"{tag}: placeholder {{{ph}}} is not in this shot's characters {s.characters}")
        action_text = " ".join([w.action for w in s.timeline] + [s.camera, s.continuity] + [d.line for d in s.dialogue])
        for c in sp.characters:
            if c.name and re.search(rf"\b{re.escape(c.name)}\b", action_text):
                print(f"[screenplay] warning: {tag} mentions {c.name!r} by name; use {{{c.id}}}", file=sys.stderr)

    all_text = " ".join(
        [sp.style_bible, sp.logline] + [c.description + " " + c.wardrobe + " " + c.distinguishing_feature for c in sp.characters]
        + [" ".join([s.summary, s.camera, s.continuity, s.audio] + [w.action for w in s.timeline] + [d.line for d in s.dialogue]) for s in sp.shots]
    )
    hits = forbidden_hits(all_text)
    if hits:
        v.append(f"forbidden words present: {hits} (they change how Seedance interprets the request)")
    return v


# ------------------------------------------------------------------ composition


def _window(start: int, end: int) -> str:
    return f"[{start}s–{end}s]"


def compose_shot_prompt(shot: Shot, sp: Screenplay) -> tuple[str, list[str]]:
    """Return (prompt, ordered character ids). The ids are the exact order in which
    the pipeline attaches reference images, so @ImageN can't drift from content[]."""
    ordered = [cid for cid in shot.characters if sp.character(cid) is not None]
    ordinal = {cid: f"@Image{n}" for n, cid in enumerate(ordered, start=1)}

    def sub(text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: ordinal.get(m.group(1), m.group(0)), text)

    lines: list[str] = []
    for cid in ordered:
        c = sp.character(cid)
        lines.append(
            f"{ordinal[cid]} is {c.name}: {c.description.rstrip('.')}, wearing {c.wardrobe.rstrip('.')}; "
            f"{c.distinguishing_feature.rstrip('.')}. Keep this exact face, hair and outfit."
        )
    if ordered:
        lines.append("Each referenced person appears exactly once in frame.")
    lines.append(f"{shot.summary.rstrip('.')}. Location: {shot.location.rstrip('.')}, {shot.time_of_day.rstrip('.')}.")
    for w in shot.timeline:
        lines.append(f"{_window(w.start, w.end)} {sub(w.action)}")
    if shot.camera:
        lines.append(f"Camera: {sub(shot.camera)}")
    if shot.continuity:
        lines.append(f"Continuity: {sub(shot.continuity)}")
    audio = f"Audio: {shot.audio.rstrip('.')}." if shot.audio else "Audio: natural ambience only."
    if shot.dialogue:
        parts = []
        for d in shot.dialogue:
            c = sp.character(d.character)
            voice = f" ({c.voice})" if c and c.voice else ""
            parts.append(f"{_window(d.start, d.end)} {ordinal.get(d.character, d.character)} says \"{d.line}\"{voice}")
        audio += " Dialogue: " + "; ".join(parts) + "."
    lines.append(audio + " No subtitles or on-screen text.")
    lines.append(f"Style: {sp.style_bible}")
    prompt = "\n".join(lines)

    hits = forbidden_hits(prompt)
    if hits:
        print(f"[screenplay] shot {shot.index}: scrubbing forbidden words {hits}", file=sys.stderr)
        prompt = scrub_forbidden(prompt)
    return prompt, ordered


def compose_all(sp: Screenplay, *, force: bool = False) -> None:
    for s in sp.shots:
        if force or not s.seedance_prompt:
            s.seedance_prompt, s.reference_ids = compose_shot_prompt(s, sp)


# ------------------------------------------------------------------ LLM driver


def write_screenplay(
    idea: str,
    *,
    shots: int,
    shot_seconds: int,
    ratio: str,
    max_characters: int = 3,
    style_hint: str | None = None,
    chat: Callable[[list[dict]], str],
) -> Screenplay:
    """One LLM call, one repair round on validation failure, then give up loudly."""
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_prompt(idea, shots=shots, shot_seconds=shot_seconds, ratio=ratio,
                                                max_characters=max_characters, style_hint=style_hint)},
    ]
    last_violations: list[str] = []
    for attempt in (1, 2):
        print(f"[screenplay] asking the LLM (attempt {attempt})", file=sys.stderr)
        reply = chat(messages)
        try:
            sp = parse_screenplay(extract_json(reply))
            last_violations = validate(sp, shots=shots, shot_seconds=shot_seconds, max_characters=max_characters)
        except ValueError as exc:
            last_violations = [f"output was not valid JSON: {exc}"]
            sp = None
        if sp is not None and not last_violations:
            compose_all(sp, force=True)
            return sp
        print("[screenplay] validation failed:\n  - " + "\n  - ".join(last_violations), file=sys.stderr)
        messages += [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "These checks failed:\n- " + "\n- ".join(last_violations)
             + "\nFix them and return the complete corrected JSON only."},
        ]
    raise SystemExit("screenplay still invalid after a repair round:\n  - " + "\n  - ".join(last_violations))


# --------------------------------------------------------------- serialisation


def to_json(sp: Screenplay) -> dict:
    return asdict(sp)


def from_json(data: dict) -> Screenplay:
    return parse_screenplay(data)
