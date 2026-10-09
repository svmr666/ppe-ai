"""Person-ийн PPE төлөв + zone шаардлага => violation.

    evaluate_person(person, zone)  -> PersonEvaluation
    evaluate_people(people, zone)  -> list[PersonEvaluation]

Дүрэм:
- Шаардлага REQUIRED БА төлөв нь NO үед л violation үүснэ.
- UNKNOWN (нотолгоо дутуу) нь хэзээ ч violation болгохгүй.
- OPTIONAL шаардлагыг зөрчлөө гэж үзэхгүй (жишээ нь OFFICE дээр дуулга).

`ViolationType` нь DB-ийн enum-тай ижил. "NONE" нь тусдаа утга биш — PersonEvaluation.violations хоосон байхыг заана
(`PersonEvaluation.summary` нь "NONE" гэж харуулна).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from inference.detection_types import Detection
from inference.person_ppe import PersonPPE, PPEState
from rules.zone_rules import Requirement, ZoneType, get_requirements, parse_zone


class ViolationType(str, Enum):
    NO_HELMET = "NO_HELMET"
    NO_WORK_UNIFORM = "NO_WORK_UNIFORM"
    OTHER_SAFETY_VIOLATION = "OTHER_SAFETY_VIOLATION"


@dataclass(frozen=True)
class RuleViolation:
    type: ViolationType
    confidence: float
    inferred: bool  # true = эерэг class илрээгүйгээс дүгнэсэн (model-д сөрөг class байхгүй)
    evidence: Optional[Detection] = None


@dataclass
class PersonEvaluation:
    person: PersonPPE
    zone: ZoneType
    violations: list[RuleViolation] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return not self.violations

    @property
    def summary(self) -> str:
        return ", ".join(v.type.value for v in self.violations) or "NONE"


def _violation(
    state: PPEState,
    requirement: Requirement,
    vtype: ViolationType,
    evidence: Optional[Detection],
    person: PersonPPE,
) -> Optional[RuleViolation]:
    if requirement is not Requirement.REQUIRED or state is not PPEState.NO:
        return None
    # Сөрөг class илэрсэн бол түүний confidence; absence-аас дүгнэсэн бол person-ийн confidence.
    confidence = evidence.confidence if evidence else person.person.confidence
    return RuleViolation(vtype, confidence, inferred=evidence is None, evidence=evidence)


def evaluate_person(person: PersonPPE, zone: "ZoneType | str") -> PersonEvaluation:
    zone = parse_zone(zone)
    required = get_requirements(zone)

    candidates = (
        _violation(person.helmet, required.helmet, ViolationType.NO_HELMET, person.helmet_evidence, person),
        _violation(
            person.uniform, required.uniform, ViolationType.NO_WORK_UNIFORM, person.uniform_evidence, person
        ),
    )
    return PersonEvaluation(person, zone, [v for v in candidates if v is not None])


def evaluate_people(people: list[PersonPPE], zone: "ZoneType | str") -> list[PersonEvaluation]:
    zone = parse_zone(zone)
    return [evaluate_person(p, zone) for p in people]
