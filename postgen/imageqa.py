"""Automatic review of generated backgrounds (replaces eyeballing each image).

Claude looks at the image and the subject it was meant to show, and rejects
anything with text, faces/figures, malformed objects, wrong symbols, or
iconography that doesn't match. Rejected images never reach the library.
"""
import base64
import io

import anthropic
from PIL import Image
from pydantic import BaseModel

from postgen.config import load_yaml

FALLBACK_BETA = "server-side-fallback-2026-07-01"

QA_PROMPT = """You review AI-generated background images for a Hindu devotional Instagram page (North-Indian audience). The app overlays all text later, so the image must be clean. It is decorative art: composition, props and style are allowed to differ from the brief.

The image was meant to show: {subject}

BLOCKERS (any one → ok=false):
- any text, letters, numbers, pseudo-script, readable glyphs, watermark, logo or signature (decorative patterns, rangoli and carvings without letters are fine);
- any human or deity face, human figure, or human body part;
- a sacred symbol drawn wrong: zodiac wheel with duplicated/missing signs; Shivling without its base; Sudarshan chakra that isn't a spoked disc with a sharp serrated rim (a flower, sun, flame-ball or gear-like medallion is wrong); broken trishul or shankh;
- anything disrespectful: sacred objects broken, in filth or rubbish, or next to inappropriate items (shoes, meat, alcohol). Puja items resting on a clean floor, ground, rock or altar is normal and fine;
- the main subject is badly malformed or melted (obvious AI failure);
- the lower ~40% (text area) is bright or crowded enough that white text would be hard to read.

NOT blockers (list under `notes` only): placement or angle differs from the brief, extra or missing minor props, a second instance of a prop, slight artefacts in the background, mild sparkles/smoke, artistic stylisation.

Set ok=false only if there is at least one blocker."""


class QAVerdict(BaseModel):
    ok: bool
    blockers: list[str]
    notes: list[str]

    @property
    def issues(self) -> list[str]:
        return self.blockers


class ImageQA:
    def __init__(self, client=None):
        cfg = load_yaml("settings")["images"]
        self.model = cfg.get("qa_model", "claude-sonnet-5-5")
        self.client = client or anthropic.Anthropic()

    def check(self, img: Image.Image, subject: str) -> QAVerdict:
        thumb = img.copy()
        thumb.thumbnail((864, 1080))                      # plenty for review, fewer tokens
        buf = io.BytesIO()
        thumb.save(buf, "JPEG", quality=85)
        resp = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=4000,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": QA_PROMPT.format(subject=subject)},
            ]}],
            output_format=QAVerdict,
        )
        if resp.stop_reason != "end_turn" or resp.parsed_output is None:
            # Can't judge → treat as a failure so the image isn't used unchecked.
            return QAVerdict(ok=False, blockers=[f"review unavailable (stop_reason={resp.stop_reason})"], notes=[])
        v = resp.parsed_output
        return QAVerdict(ok=v.ok and not v.blockers, blockers=v.blockers, notes=v.notes)
