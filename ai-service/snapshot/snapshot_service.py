"""Violation болсон яг тухайн frame-ээс snapshot JPEG үүсгэнэ (бүх текст Монголоор, Кирилл фонттой).

Snapshot дээр:
  - Зөрчилтэй хүний улаан хайрцаг + дээр нь улаан шошго: «#1 Каскгүй · Ажлын хувцасгүй»
  - Мэдээллийн самбар: зөрчлийн төрөл, Камер, Бүс, Итгэлцэл, Цаг (UTC+8)
Кирилл дэмждэг системийн фонт (Arial / DejaVu / Liberation ...) хайна; олдохгүй бол ASCII-д шилжинэ.
Эх frame-ийг өөрчлөхгүй (copy дээр зурна).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger
from violations.violation_service import ViolationEvent

logger = get_logger("snapshot")

RED = (229, 32, 46)
WHITE = (255, 255, 255)

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
VIOLATION_TITLE = {
    "NO_HELMET": "КАСКГҮЙ",
    "NO_WORK_UNIFORM": "АЖЛЫН ХУВЦАСГҮЙ",
    "OTHER_SAFETY_VIOLATION": "АЮУЛГҮЙ БАЙДЛЫН ЗӨРЧИЛ",
}
ZONE_LABEL = {"FACTORY": "Үйлдвэр", "OFFICE": "Оффис"}


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    data: bytes  # JPEG
    width: int
    height: int
    content_type: str = "image/jpeg"

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data)
        return path


@lru_cache(maxsize=16)
def _font(size: int) -> tuple[ImageFont.ImageFont, bool]:
    """(font, кирилл дэмжих эсэх)"""
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size), True
            except OSError:
                continue
    logger.warning("Кирилл дэмждэг фонт олдсонгүй — snapshot-ийн текст ASCII болно")
    return ImageFont.load_default(size), False


def cyrillic_supported() -> bool:
    return _font(20)[1]


def ppe_label(helmet: str, uniform: str) -> str:
    """«Каскгүй · Ажлын хувцасгүй» (dashboard-ийн давхаргатай ижил текст)."""
    parts = []
    if helmet == "NO":
        parts.append("Каскгүй")
    elif helmet == "YES":
        parts.append("Каскатай")
    if uniform == "NO":
        parts.append("Ажлын хувцасгүй")
    elif uniform == "YES":
        parts.append("Ажлын хувцастай")
    return " · ".join(parts)


def local_filename(event: ViolationEvent) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", event.camera_name).strip("-") or "camera"
    return f"{event.detected_at:%Y%m%d_%H%M%S}_{slug}_{event.violation_type.value}_{event.person_id}.jpg"


class SnapshotService:
    def __init__(self, jpeg_quality: int = 85, utc_offset_hours: float = 8.0) -> None:
        self.jpeg_quality = jpeg_quality
        self.utc_offset_hours = utc_offset_hours

    def create(self, event: ViolationEvent, frame: np.ndarray) -> Snapshot:
        if frame is None or frame.ndim != 3:
            raise SnapshotError("Invalid frame (expected BGR image)")

        height, width = frame.shape[:2]
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image, "RGBA")
        unit = max(0.5, width / 1280)  # жижиг зураг дээр самбар зургийг дарахгүй байхаар масштабла

        self._draw_person(draw, event, width, height, unit)
        self._draw_panel(draw, event, unit)

        out = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        ok, buffer = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise SnapshotError("JPEG encoding failed")
        logger.info(
            "Snapshot created | camera=%s type=%s person=%s size=%dKB",
            event.camera_name, event.violation_type.value, event.person_id, len(buffer) // 1024,
        )
        return Snapshot(buffer.tobytes(), width, height)

    # ------------------------------------------------------------------
    @staticmethod
    def _t(text: str, cyr: bool) -> str:
        return text if cyr else text.encode("ascii", "replace").decode("ascii")

    def _draw_person(self, draw: ImageDraw.ImageDraw, event: ViolationEvent, w: int, h: int, unit: float) -> None:
        box = event.person_box
        x1, y1 = min(max(0, int(box.x1)), w - 2), min(max(0, int(box.y1)), h - 2)
        x2, y2 = max(x1 + 1, min(w - 1, int(box.x2))), max(y1 + 1, min(h - 1, int(box.y2)))
        line = max(2, round(2 * unit))  # нарийн шугам: нүүр, каскийг халхлахгүй
        draw.rectangle([x1, y1, x2, y2], outline=RED, width=line)
        self._draw_helmet(draw, event, w, h, line)

        font, cyr = _font(max(14, round(18 * unit)))
        detail = ppe_label(event.helmet_state, event.uniform_state) or VIOLATION_TITLE.get(event.violation_type.value, "")
        text = self._t(f"#{event.track_id} {detail}", cyr)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        pad = max(4, round(6 * unit))
        chip_w, chip_h = (right - left) + 2 * pad, (bottom - top) + 2 * pad
        cx = min(x1, max(0, w - chip_w))
        cy = y1 - chip_h if y1 - chip_h >= 0 else y1
        draw.rectangle([cx, cy, cx + chip_w, cy + chip_h], fill=RED)
        draw.text((cx + pad - left, cy + pad - top), text, font=font, fill=WHITE)

    @staticmethod
    def _draw_helmet(draw: ImageDraw.ImageDraw, event: ViolationEvent, w: int, h: int, line: int) -> None:
        """Танигдсан каскийг (ногоон) эсвэл каскгүй толгойг (улаан) нарийн хүрээгээр тэмдэглэнэ."""
        hb = event.helmet_box
        if hb is None:
            return
        x1, y1 = min(max(0, int(hb.x1)), w - 2), min(max(0, int(hb.y1)), h - 2)
        x2, y2 = max(x1 + 1, min(w - 1, int(hb.x2))), max(y1 + 1, min(h - 1, int(hb.y2)))
        color = (31, 178, 90) if hb.class_name == "helmet" else RED
        draw.rectangle([x1, y1, x2, y2], outline=color, width=line)

    def _draw_panel(self, draw: ImageDraw.ImageDraw, event: ViolationEvent, unit: float) -> None:
        title_font, cyr = _font(max(13, round(22 * unit)))
        line_font, _ = _font(max(10, round(14 * unit)))
        local = event.detected_at + timedelta(hours=self.utc_offset_hours)
        sign = "+" if self.utc_offset_hours >= 0 else "-"
        offset = f"UTC{sign}{abs(self.utc_offset_hours):g}"
        confidence = f"{event.confidence:.0%}" + (" (дүгнэсэн)" if event.inferred else "")
        lines = [
            self._t(f"Камер: {event.camera_name}", cyr),
            self._t(f"Бүс: {ZONE_LABEL.get(event.zone.value, event.zone.value)}", cyr),
            self._t(f"Итгэлцэл: {confidence}", cyr),
            f"{local:%Y-%m-%d %H:%M:%S} {offset}",
        ]
        title = self._t(VIOLATION_TITLE.get(event.violation_type.value, event.violation_type.value), cyr)

        pad, gap = max(5, round(9 * unit)), max(2, round(4 * unit))
        tb = draw.textbbox((0, 0), title, font=title_font)
        lbs = [draw.textbbox((0, 0), t, font=line_font) for t in lines]
        panel_w = max([tb[2] - tb[0]] + [b[2] - b[0] for b in lbs]) + 2 * pad + max(3, round(5 * unit))
        panel_h = 2 * pad + (tb[3] - tb[1]) + sum((b[3] - b[1]) + gap for b in lbs)
        x0 = y0 = max(4, round(8 * unit))
        bar = max(3, round(5 * unit))
        draw.rectangle([x0, y0, x0 + panel_w, y0 + panel_h], fill=(0, 0, 0, 140))
        draw.rectangle([x0, y0, x0 + bar, y0 + panel_h], fill=RED)

        x = x0 + bar + pad
        y = y0 + pad
        draw.text((x - tb[0], y - tb[1]), title, font=title_font, fill=RED)
        y += (tb[3] - tb[1]) + gap
        for text, b in zip(lines, lbs):
            y += gap
            draw.text((x - b[0], y - b[1]), text, font=line_font, fill=WHITE)
            y += b[3] - b[1]
