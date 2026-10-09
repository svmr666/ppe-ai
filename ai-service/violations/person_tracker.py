"""Хөнгөн IoU-based person tracker (камер бүрт нэг instance).

Frame хооронд нэг хүнд тогтвортой *түр* ID өгнө. Violation cooldown / баталгаажуулалт үүгээр
"Person A" ба "Person B"-г ялгана. Энэ нь хүнийг таних (re-identification) систем биш:
хүн frame-ээс max_age_seconds-ээс удаан хасагдвал буцаж ирэхэд шинэ ID авна.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from inference.detection_types import Detection


def iou(a: Detection, b: Detection) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Track:
    track_id: int
    box: Detection
    last_seen: float


class PersonTracker:
    def __init__(self, iou_threshold: float = 0.25, max_age_seconds: float = 3.0) -> None:
        self.iou_threshold = iou_threshold
        self.max_age_seconds = max_age_seconds
        self._tracks: list[_Track] = []
        self._next_id = 1

    @property
    def active_tracks(self) -> int:
        return len(self._tracks)

    def update(self, persons: Sequence[Detection], now: float) -> list[int]:
        """Person detection бүрт track ID онооно. Буцаах жагсаалт нь `persons`-той ижил дарааллаар."""
        self._tracks = [t for t in self._tracks if now - t.last_seen <= self.max_age_seconds]

        pairs = sorted(
            (
                (iou(track.box, det), ti, di)
                for ti, track in enumerate(self._tracks)
                for di, det in enumerate(persons)
            ),
            reverse=True,
        )

        assigned: dict[int, int] = {}  # detection index -> track index
        used_tracks: set[int] = set()
        for score, ti, di in pairs:
            if score < self.iou_threshold:
                break
            if ti in used_tracks or di in assigned:
                continue
            assigned[di] = ti
            used_tracks.add(ti)

        ids: list[int] = []
        for di, det in enumerate(persons):
            if di in assigned:
                track = self._tracks[assigned[di]]
                track.box, track.last_seen = det, now
            else:
                track = _Track(self._next_id, det, now)
                self._next_id += 1
                self._tracks.append(track)
            ids.append(track.track_id)
        return ids
