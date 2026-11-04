"""Reels: a short vertical video built from the day's post, with a Hindi voiceover.

Each spoken line gets its own slide, so the text on screen follows the voice.
Same accuracy rules as captions: the LLM writes only the devotional lines; any
timing is built by code from Drik/kalagana data and spoken in Hindi words.
"""
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel

from postgen import names
from postgen.captions import SHARED_RULES, CaptionError
from postgen.config import load_yaml
from postgen.models import DayFacts, round_min

HINDI_NUM = ["शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दस",
             "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस", "बीस",
             "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस", "तीस",
             "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस", "चालीस",
             "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
             "पचास", "इक्यावन", "बावन", "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ"]


def spoken_time(t: datetime, with_part: bool = True) -> str:
    """7:53 AM → 'सुबह सात बजकर तिरपन मिनट'; 6:00 PM → 'शाम छह बजे' (nearest minute)."""
    t = round_min(t)
    part = names.time_hi(t).split()[0]
    h12 = t.hour % 12 or 12
    clock = f"{HINDI_NUM[h12]} बजे" if t.minute == 0 else f"{HINDI_NUM[h12]} बजकर {HINDI_NUM[t.minute]} मिनट"
    return f"{part} {clock}" if with_part else clock


def spoken_span(a: datetime, b: datetime) -> str:
    same_part = names.time_hi(a).split()[0] == names.time_hi(b).split()[0]
    return f"{spoken_time(a)} से {spoken_time(b, with_part=not same_part)} तक"


@dataclass
class Line:
    say: str          # spoken (Devanagari)
    show: str         # on screen (Devanagari, short)
    source: str       # "llm" | "code"


def timing_line(day: DayFacts, kind: str) -> Optional[Line]:
    """The one timing worth saying out loud, built by code (never by the LLM)."""
    if kind == "panchang":
        rk = day.rahu_kaal
        return Line(f"आज राहु काल {spoken_span(rk.start, rk.end)} रहेगा, इस समय कोई शुभ काम शुरू न करें।",
                    f"राहु काल: {names.span_hi(rk.start, rk.end)}", "code")
    if day.timings:
        # Prefer the puja muhurat when a day has several curated timings.
        label, value = next((t for t in day.timings if "Muhurat" in t[0]), day.timings[0])
        parts = [datetime.strptime(v.strip(), "%I:%M %p") for v in value.split("–")]
        label_hi = names.TIMING_LABEL_HI.get(label, label)
        said = spoken_span(*parts) if len(parts) == 2 else spoken_time(parts[0])
        return Line(f"{label_hi} {said} है।", f"{label_hi}: {names.timing_value_hi(value)}", "code")
    return None


# ---------------- script (LLM) ----------------

REEL_PROMPT = SHARED_RULES + """
You are writing the SPOKEN script for a 15–25 second Instagram reel, in simple spoken Hindi (Devanagari), like a calm, respectful elder talking to the viewer. Facts describe the day of the post.

Return 3 lines, in order:
1. hook: makes a scrolling viewer stop. Specific to the day; a benefit or a gentle question. Max 12 words. (e.g. "आज रमा एकादशी है — बस ये एक काम ज़रूर करें।" is too pushy; prefer "आज रमा एकादशी है, श्री हरि की कृपा पाने का सुंदर दिन।")
2. meaning: one line on why the day matters (tradition and belief, "माना जाता है").
3. upay: exactly the upay given in the facts (same remedy), as one spoken instruction.

For each line give:
- say: what the voice says. Natural spoken Hindi, short sentences, no English words except names.
- show: 2–6 word on-screen version of the same line (max 34 characters), Devanagari.

Never mention a time, date, number of minutes, "follow", the app, or a link: the app adds the timing line and the closing line itself."""


class ReelLine(BaseModel):
    say: str
    show: str


class ReelScript(BaseModel):
    hook: ReelLine
    meaning: ReelLine
    upay: ReelLine


@dataclass
class Reel:
    lines: list[Line]
    video: Optional[Path] = None
    cover: Optional[Path] = None
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)


class ReelWriter:
    def __init__(self, base):
        """base: a CaptionWriter (reuses its Claude client, guardrails and settings)."""
        self.base = base
        self.cfg = load_yaml("settings")["reels"]
        self.cta = load_yaml("brand")["reel_cta"]

    def facts(self, day: DayFacts, cap: dict) -> dict:
        upay = cap.get("upay_hi") or (cap.get("llm") or {}).get("upay") or ""
        f = {"weekday": names.WEEKDAY_HI[day.weekday],
             "tithis": [names.tithi_hi(t.name) for t in day.tithis],
             "observances": day.festivals,
             "upay_to_use": upay}
        if cap["kind"] == "panchang":
            deity = load_yaml("occasions")["weekdays"][day.weekday]["deity_hi"]
            f.update(occasion="आज का पंचांग (ordinary day)", weekday_deity=deity,
                     note="No festival today: build the hook and meaning around the weekday deity.")
            if not upay:
                f["upay_to_use"] = f"{deity} का स्मरण करते हुए एक दीपक जलाएँ"
        else:
            f.update(occasion=cap["image_headline"])
        return f

    def problems(self, s: dict, day: DayFacts, cap: dict) -> list[str]:
        flat = {f"{k} {p}": s[k][p] for k in ("hook", "meaning", "upay") for p in ("say", "show")}
        allowed = " ".join([cap.get("occasion", ""), *day.festivals, *(t.name for t in day.tithis)])
        probs = self.base.guardrail_problems(flat, allowed, [])
        for k, v in flat.items():
            if not re.search(r"[ऀ-ॿ]", v):
                probs.append(f"{k} must be in Devanagari")
            if re.search(r"\d", v):
                probs.append(f"{k}: no digits — the app adds all numbers and timings")
            if re.search(r"फॉलो|follow|ऐप|app|लिंक|link", v, re.IGNORECASE):
                probs.append(f"{k}: don't mention follow/app/link — the app adds the closing line")
        for k in ("hook", "meaning", "upay"):
            if len(s[k]["show"]) > 40:
                probs.append(f"{k} show is {len(s[k]['show'])} characters; max 34")
        total = sum(len(s[k]["say"]) for k in ("hook", "meaning", "upay"))
        budget = self.cfg["max_script_chars"] - 140          # room for the timing + closing lines
        if total > budget:
            probs.append(f"the three lines total {total} characters; keep them under {budget}")
        return probs

    def script(self, day: DayFacts, cap: dict) -> tuple[list[Line], dict]:
        facts = self.facts(day, cap)
        feedback, usage = [], {"input_tokens": 0, "output_tokens": 0}
        for _ in range(self.base.settings.get("max_attempts", 3)):
            data, u = self.base._call(ReelScript, facts, feedback, system=REEL_PROMPT)
            usage = {k: usage[k] + u[k] for k in usage}
            feedback = self.problems(data, day, cap)
            if not feedback:
                kind = "panchang" if cap["kind"] == "panchang" else "occasion"
                lines = [Line(data[k]["say"], data[k]["show"], "llm") for k in ("hook", "meaning", "upay")]
                t = timing_line(day, kind)
                if t:
                    lines.insert(2, t)                       # hook, meaning, timing, upay, CTA
                lines.append(Line(self.cta["say"], self.cta["show"], "code"))
                return lines, usage
        raise CaptionError(f"reel script for {day.date} still invalid: {feedback}")


# ---------------- voice (ElevenLabs) ----------------

def tts(text: str, out: Path, cfg: dict) -> Path:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['voice_id']}?output_format=mp3_44100_128",
        headers={"xi-api-key": key, "Content-Type": "application/json"}, timeout=120,
        json={"text": text, "model_id": cfg["tts_model"], "voice_settings": cfg["voice_settings"]})
    if not r.ok:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    out.write_bytes(r.content)
    return out


# ---------------- video (ffmpeg) ----------------

def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def media_seconds(path: Path) -> float:
    err = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise RuntimeError(f"can't read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def segment(frame: Path, audio: Path, seconds: float, out: Path, fps: int = 30):
    """One slide: slow push-in on the still, fade in/out, its spoken line underneath."""
    n = max(1, round(seconds * fps))
    vf = (f"scale=2160:3840,zoompan=z='min(zoom+0.0007,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={n}:s=1080x1920:fps={fps},fade=t=in:st=0:d=0.25,fade=t=out:st={max(0, seconds - 0.3):.2f}:d=0.3,"
          f"format=yuv420p")
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-i", str(frame), "-i", str(audio),
           "-filter_complex", f"[0:v]{vf}[v];[1:a]apad,atrim=0:{seconds:.3f},aresample=44100[a]",
           "-map", "[v]", "-map", "[a]", "-t", f"{seconds:.3f}", "-r", str(fps),
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(out)]
    subprocess.run(cmd, check=True)


def concat(segments: list[Path], out: Path):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for s in segments:
            f.write(f"file '{s}'\n")
        listing = f.name
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
                    "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
