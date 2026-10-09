"""Zone бүрийн PPE шаардлага. YOLO / камертай ямар ч холбоогүй цэвэр дүрэм.

    FACTORY : Helmet = REQUIRED, Work Uniform = REQUIRED
    OFFICE  : Helmet = OPTIONAL, Work Uniform = REQUIRED

Шинэ zone нэмэх бол ZoneType болон ZONE_REQUIREMENTS-д л мөр нэмнэ.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ZoneType(str, Enum):
    FACTORY = "FACTORY"
    OFFICE = "OFFICE"


class Requirement(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


@dataclass(frozen=True)
class ZoneRequirements:
    helmet: Requirement
    uniform: Requirement


ZONE_REQUIREMENTS: dict[ZoneType, ZoneRequirements] = {
    ZoneType.FACTORY: ZoneRequirements(helmet=Requirement.REQUIRED, uniform=Requirement.REQUIRED),
    ZoneType.OFFICE: ZoneRequirements(helmet=Requirement.OPTIONAL, uniform=Requirement.REQUIRED),
}


def parse_zone(value: "ZoneType | str") -> ZoneType:
    if isinstance(value, ZoneType):
        return value
    try:
        return ZoneType(str(value).strip().upper())
    except ValueError:
        valid = ", ".join(z.value for z in ZoneType)
        raise ValueError(f"Unknown zone '{value}'. Valid zones: {valid}") from None


def get_requirements(zone: "ZoneType | str") -> ZoneRequirements:
    return ZONE_REQUIREMENTS[parse_zone(zone)]
