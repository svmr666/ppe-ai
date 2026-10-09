"""Violation engine: rule үр дүнг баталгаажуулж, cooldown-оор давхардлыг хасаж, ViolationEvent үүсгэнэ.

Камер бүрт нэг instance. Төлөвийг (track_id, violation_type) түлхүүрээр хадгална:

  1. Баталгаажуулалт: сүүлийн `confirm_window` frame-ийн дор хаяж `confirm_min_hits`-д зөрчил
     илэрсэн байх ёстой (нэг frame-ийн алдаа / flicker-ийг үл тооно).
  2. Cooldown: нэг хүн (track) нэг төрлийн зөрчлөөр `cooldown_seconds` дотор дахин event үүсгэхгүй.
         10:00:00 -> violation | 10:00:02, :04, :08 -> ignore | 10:00:12 -> шинэ violation
     Өөр хүн (өөр track) өөрийн cooldown-той тул хамаарахгүй.

Энэ модуль DB / storage-д хандахгүй. Үүссэн event-ийг дараагийн phase-үүд (snapshot, storage, DB, WebSocket) авна.
"""

from __future__ import annotations

from collections import deque
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from inference.detection_types import Detection
from rules.violation_rules import PersonEvaluation, RuleViolation, ViolationType
from rules.zone_rules import ZoneType
from utils.logger import get_logger

logger = get_logger("violations.engine")

_STALE_AFTER_SECONDS = 60.0


@dataclass(frozen=True)
class ViolationEvent:
    camera_name: str
    camera_id: Optional[str]
    zone: ZoneType
    violation_type: ViolationType
    track_id: int
    confidence: float
    inferred: bool  # true = эерэг PPE илрээгүйгээс дүгнэсэн
    detected_at: datetime  # UTC
    person_box: Detection
    evidence_box: Optional[Detection]
    # DB-ийн violations.id болон Storage path-д ашиглагдана (event үүсэх мөчид тогтоно)
    violation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Snapshot-ийн шошгонд («#1 Каскгүй · Ажлын хувцасгүй») хэрэгтэй: тухайн хүний PPE төлөв
    helmet_state: str = "UNKNOWN"
    uniform_state: str = "UNKNOWN"
    helmet_box: Optional[Detection] = None  # танигдсан каск (helmet) эсвэл каскгүй толгой (no_helmet)

    @property
    def person_id(self) -> str:
        return f"T{self.track_id}"


class ViolationEngine:
    def __init__(
        self,
        camera_name: str,
        cooldown_seconds: float,
        confirm_window: int = 5,
        confirm_min_hits: int = 3,
        camera_id: Optional[str] = None,
    ) -> None:
        if confirm_window < 1 or not 1 <= confirm_min_hits <= confirm_window:
            raise ValueError("Require 1 <= confirm_min_hits <= confirm_window")
        self.camera_name = camera_name
        self.camera_id = camera_id
        self.cooldown_seconds = cooldown_seconds
        self.confirm_window = confirm_window
        self.confirm_min_hits = confirm_min_hits

        self._history: dict[int, deque[dict[ViolationType, RuleViolation]]] = {}
        self._last_seen: dict[int, float] = {}
        self._last_emitted: dict[tuple[int, ViolationType], float] = {}

    def process(self, evaluations: list[PersonEvaluation], now: float) -> list[ViolationEvent]:
        events: list[ViolationEvent] = []

        for ev in evaluations:
            track_id = ev.person.track_id
            if track_id is None:  # tracker-гүйгээр cooldown хийх боломжгүй
                continue

            history = self._history.setdefault(track_id, deque(maxlen=self.confirm_window))
            history.append({v.type: v for v in ev.violations})
            self._last_seen[track_id] = now

            for vtype in {t for frame in history for t in frame}:
                hits = [frame[vtype] for frame in history if vtype in frame]
                if len(hits) < self.confirm_min_hits:
                    continue

                last = self._last_emitted.get((track_id, vtype))
                if last is not None and now - last < self.cooldown_seconds:
                    continue

                self._last_emitted[(track_id, vtype)] = now
                latest = hits[-1]
                events.append(
                    ViolationEvent(
                        camera_name=self.camera_name,
                        camera_id=self.camera_id,
                        zone=ev.zone,
                        violation_type=vtype,
                        track_id=track_id,
                        confidence=sum(h.confidence for h in hits) / len(hits),
                        inferred=all(h.inferred for h in hits),
                        detected_at=datetime.fromtimestamp(now, tz=timezone.utc),
                        person_box=ev.person.person,
                        evidence_box=latest.evidence,
                        helmet_state=ev.person.helmet.value,
                        uniform_state=ev.person.uniform.value,
                        helmet_box=ev.person.helmet_evidence,
                    )
                )
                logger.warning(
                    "%s detected | camera=%s zone=%s person=T%d confidence=%.2f%s",
                    vtype.value, self.camera_name, ev.zone.value, track_id,
                    events[-1].confidence, " (inferred)" if events[-1].inferred else "",
                )

        self._purge(now)
        return events

    def _purge(self, now: float) -> None:
        stale = [t for t, seen in self._last_seen.items() if now - seen > _STALE_AFTER_SECONDS]
        for track_id in stale:
            self._history.pop(track_id, None)
            self._last_seen.pop(track_id, None)
        if stale:
            gone = set(stale)
            self._last_emitted = {k: v for k, v in self._last_emitted.items() if k[0] not in gone}
