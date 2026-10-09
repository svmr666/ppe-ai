"""Person-centric PPE analysis.

Frame дээрх `no_helmet` class-ыг сохроор violation болгохгүй. Detection бүрийг тухайн хүнтэй
газар зүйн байршлаар нь холбож, person бүрийн PPE төлөвийг тодорхойлно:

    Person #1  Helmet: YES  Uniform: YES
    Person #2  Helmet: NO   Uniform: YES

Холбох дүрэм
- helmet / no_helmet : төв нь person box-ын ТОЛГОЙН бүсэд (дээд ~45%) орсон байх ёстой.
- work_uniform        : төв нь person box дотор орсон байх ёстой.
- PPE detection нэг л хүнд хамаарна (давхцсан хүмүүсийн хувьд хамгийн ойр хүнийг сонгоно).
- Нэг person-д helmet ба no_helmet хоёул таарвал confidence өндөр нь ялна.

PPE төлөв
- YES     : эерэг class (helmet / work_uniform) илэрсэн
- NO      : сөрөг class (no_helmet / no_work_uniform) илэрсэн, ЭСВЭЛ model-д сөрөг class байхгүй үед
            (config.*_absence_means_no) хангалттай том хүн дээр эерэг class илрээгүй
- UNKNOWN : нотолгоо дутуу (жишээ нь хүн хэт жижиг/алс байна). Rule engine UNKNOWN-ийг violation болгохгүй.

Энэ модуль зөвхөн detection-тэй ажиллана (frame, zone, DB-гүй). Zone дүрэм нь rules/ дотор.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from inference.detection_types import Detection, PPEClass


class PPEState(str, Enum):
    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AnalyzerConfig:
    # Үүнээс намхан person дээр "байхгүй" гэж дүгнэхгүй (алс/жижиг хүн дээр дуулга харагдахгүй).
    min_person_height_px: float = 80.0

    # Model-д сөрөг class (no_helmet / no_work_uniform) байхгүй үед absence-ээс NO гэж дүгнэх эсэх.
    helmet_absence_means_no: bool = False
    uniform_absence_means_no: bool = False

    # Толгойн бүс: person box-ын дээд талаас (height * head_zone_ratio) хүртэл,
    # дээш нь (height * head_zone_top_margin) хүртэл, хажуу тийш (width * horizontal_margin).
    head_zone_ratio: float = 0.45
    head_zone_top_margin: float = 0.15
    horizontal_margin: float = 0.10

    @classmethod
    def from_mapped_classes(cls, mapped: Iterable[str], **overrides) -> "AnalyzerConfig":
        """Model-д ямар class байгаагаас хамаарч absence-based дүгнэлтийг автоматаар сонгоно."""
        mapped = set(mapped)
        return cls(
            helmet_absence_means_no=(
                PPEClass.HELMET.value in mapped and PPEClass.NO_HELMET.value not in mapped
            ),
            uniform_absence_means_no=(
                PPEClass.WORK_UNIFORM.value in mapped and PPEClass.NO_WORK_UNIFORM.value not in mapped
            ),
            **overrides,
        )


@dataclass
class PersonPPE:
    index: int  # frame доторх дугаар (зүүнээс баруун, 1-ээс эхэлнэ). Tracker ID биш.
    person: Detection
    helmet: PPEState
    uniform: PPEState
    helmet_evidence: Optional[Detection] = None  # helmet эсвэл no_helmet detection
    uniform_evidence: Optional[Detection] = None  # work_uniform эсвэл no_work_uniform detection
    track_id: Optional[int] = None  # Phase 8: temporary tracker ID

    @property
    def label(self) -> str:
        return f"Person #{self.index}"


_HELMET_CLASSES = (PPEClass.HELMET.value, PPEClass.NO_HELMET.value)
_UNIFORM_CLASSES = (PPEClass.WORK_UNIFORM.value, PPEClass.NO_WORK_UNIFORM.value)


class PersonPPEAnalyzer:
    def __init__(self, config: Optional[AnalyzerConfig] = None) -> None:
        self.config = config or AnalyzerConfig()

    def analyze(self, detections: list[Detection]) -> list[PersonPPE]:
        persons = sorted((d for d in detections if d.is_a(PPEClass.PERSON)), key=lambda d: d.x1)
        if not persons:
            return []

        helmet_hits: list[list[Detection]] = [[] for _ in persons]
        uniform_hits: list[list[Detection]] = [[] for _ in persons]

        # Өндөр confidence-тэй detection эхлээд сонгогдоно
        for det in sorted(detections, key=lambda d: -d.confidence):
            if det.class_name in _HELMET_CLASSES:
                owner = self._owner_for_helmet(det, persons)
                if owner is not None:
                    helmet_hits[owner].append(det)
            elif det.class_name in _UNIFORM_CLASSES:
                owner = self._owner_for_uniform(det, persons)
                if owner is not None:
                    uniform_hits[owner].append(det)

        results: list[PersonPPE] = []
        for i, person in enumerate(persons):
            helmet, helmet_ev = self._resolve(
                person, helmet_hits[i], PPEClass.HELMET, PPEClass.NO_HELMET, self.config.helmet_absence_means_no
            )
            uniform, uniform_ev = self._resolve(
                person, uniform_hits[i], PPEClass.WORK_UNIFORM, PPEClass.NO_WORK_UNIFORM,
                self.config.uniform_absence_means_no,
            )
            results.append(PersonPPE(i + 1, person, helmet, uniform, helmet_ev, uniform_ev))
        return results

    # ------------------------------------------------------------------
    def _resolve(
        self,
        person: Detection,
        hits: list[Detection],
        positive: PPEClass,
        negative: PPEClass,
        absence_means_no: bool,
    ) -> tuple[PPEState, Optional[Detection]]:
        pos = max((d for d in hits if d.is_a(positive)), key=lambda d: d.confidence, default=None)
        neg = max((d for d in hits if d.is_a(negative)), key=lambda d: d.confidence, default=None)

        if pos and neg:  # зөрчилдөөн: confidence өндөр нь ялна
            return (PPEState.YES, pos) if pos.confidence >= neg.confidence else (PPEState.NO, neg)
        if pos:
            return PPEState.YES, pos
        if neg:
            return PPEState.NO, neg

        if absence_means_no and person.height >= self.config.min_person_height_px:
            return PPEState.NO, None
        return PPEState.UNKNOWN, None

    def _owner_for_helmet(self, det: Detection, persons: list[Detection]) -> Optional[int]:
        cfg = self.config
        cx, cy = det.center
        best: Optional[tuple[float, int]] = None
        for i, p in enumerate(persons):
            if not (
                p.x1 - cfg.horizontal_margin * p.width <= cx <= p.x2 + cfg.horizontal_margin * p.width
                and p.y1 - cfg.head_zone_top_margin * p.height <= cy <= p.y1 + cfg.head_zone_ratio * p.height
            ):
                continue
            # Хүний толгойн төвд (дээд-голд) хамгийн ойр нь
            head_x, head_y = (p.x1 + p.x2) / 2, p.y1 + 0.1 * p.height
            dist = (((cx - head_x) / max(p.width, 1.0)) ** 2 + ((cy - head_y) / max(p.height, 1.0)) ** 2) ** 0.5
            if best is None or dist < best[0]:
                best = (dist, i)
        return best[1] if best else None

    def _owner_for_uniform(self, det: Detection, persons: list[Detection]) -> Optional[int]:
        cx, cy = det.center
        best: Optional[tuple[float, int]] = None
        for i, p in enumerate(persons):
            if not (p.x1 <= cx <= p.x2 and p.y1 <= cy <= p.y2):
                continue
            # Хүний их биеийн төвд (голд) хамгийн ойр нь
            torso_x, torso_y = (p.x1 + p.x2) / 2, p.y1 + 0.5 * p.height
            dist = (((cx - torso_x) / max(p.width, 1.0)) ** 2 + ((cy - torso_y) / max(p.height, 1.0)) ** 2) ** 0.5
            if best is None or dist < best[0]:
                best = (dist, i)
        return best[1] if best else None
