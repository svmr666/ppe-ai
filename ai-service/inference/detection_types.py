"""YOLO-оос гарах үр дүнгийн нэгдсэн төрлүүд. Rule engine зөвхөн эдгээр canonical class-тай ажиллана."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PPEClass(str, Enum):
    """Системийн canonical class нэрс. Model-ийн class нэр өөр бол CLASS_MAPPING-ээр хөрвүүлнэ."""

    PERSON = "person"
    HELMET = "helmet"
    NO_HELMET = "no_helmet"
    WORK_UNIFORM = "work_uniform"
    NO_WORK_UNIFORM = "no_work_uniform"


@dataclass(frozen=True)
class Detection:
    class_name: str  # canonical (PPEClass.value)
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    def is_a(self, cls: PPEClass) -> bool:
        return self.class_name == cls.value
