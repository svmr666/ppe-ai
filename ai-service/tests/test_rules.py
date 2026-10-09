import pytest

from inference.detection_types import Detection
from inference.person_ppe import AnalyzerConfig, PersonPPEAnalyzer
from rules.violation_rules import ViolationType, evaluate_people, evaluate_person
from rules.zone_rules import Requirement, ZoneType, get_requirements, parse_zone


def det(name, x1, y1, x2, y2, conf=0.9):
    return Detection(name, conf, x1, y1, x2, y2)


PERSON = det("person", 100, 100, 300, 500)
HELMET = det("helmet", 150, 100, 250, 170)
HEAD = det("no_helmet", 150, 100, 250, 170, conf=0.91)
UNIFORM = det("work_uniform", 110, 200, 290, 450)
NO_UNIFORM = det("no_work_uniform", 110, 200, 290, 450, conf=0.88)


def run(detections, zone, **cfg):
    (person,) = PersonPPEAnalyzer(AnalyzerConfig(**cfg)).analyze(detections)
    return evaluate_person(person, zone)


def types(evaluation):
    return [v.type for v in evaluation.violations]


# ---- spec §35 тестүүд ----
def test1_helmet_on_uniform_on_factory_no_violation():
    result = run([PERSON, HELMET, UNIFORM], ZoneType.FACTORY)
    assert result.is_compliant and result.summary == "NONE"


def test2_helmet_off_uniform_on_factory_is_no_helmet():
    result = run([PERSON, HEAD, UNIFORM], ZoneType.FACTORY)
    assert types(result) == [ViolationType.NO_HELMET]
    assert result.violations[0].confidence == pytest.approx(0.91)
    assert result.violations[0].inferred is False


def test3_helmet_on_uniform_off_factory_is_no_work_uniform():
    result = run([PERSON, HELMET, NO_UNIFORM], ZoneType.FACTORY)
    assert types(result) == [ViolationType.NO_WORK_UNIFORM]


def test4_helmet_off_uniform_on_office_no_violation():
    result = run([PERSON, HEAD, UNIFORM], ZoneType.OFFICE)
    assert result.is_compliant


# ---- нэмэлт ----
def test_office_still_requires_uniform():
    result = run([PERSON, HEAD, NO_UNIFORM], ZoneType.OFFICE)
    assert types(result) == [ViolationType.NO_WORK_UNIFORM]


def test_factory_both_violations():
    result = run([PERSON, HEAD, NO_UNIFORM], ZoneType.FACTORY)
    assert types(result) == [ViolationType.NO_HELMET, ViolationType.NO_WORK_UNIFORM]
    assert result.summary == "NO_HELMET, NO_WORK_UNIFORM"


def test_unknown_never_creates_violation():
    result = run([PERSON], ZoneType.FACTORY)  # нотолгоо байхгүй => UNKNOWN
    assert result.is_compliant


def test_absence_based_violation_is_marked_inferred():
    result = run([PERSON, HELMET], ZoneType.FACTORY, uniform_absence_means_no=True)
    (violation,) = result.violations
    assert violation.type is ViolationType.NO_WORK_UNIFORM
    assert violation.inferred is True and violation.evidence is None
    assert violation.confidence == pytest.approx(PERSON.confidence)


def test_multiple_people_evaluated_independently():
    b = det("person", 500, 100, 700, 500)
    dets = [PERSON, b, HELMET, det("no_helmet", 550, 100, 650, 170), UNIFORM, det("work_uniform", 510, 200, 690, 450)]
    people = PersonPPEAnalyzer().analyze(dets)
    first, second = evaluate_people(people, "factory")
    assert first.is_compliant
    assert types(second) == [ViolationType.NO_HELMET]


def test_zone_requirements_table():
    assert get_requirements(ZoneType.FACTORY).helmet is Requirement.REQUIRED
    assert get_requirements("office").helmet is Requirement.OPTIONAL
    assert get_requirements("OFFICE").uniform is Requirement.REQUIRED


def test_parse_zone_rejects_unknown():
    assert parse_zone(" factory ") is ZoneType.FACTORY
    with pytest.raises(ValueError, match="Valid zones"):
        parse_zone("WAREHOUSE")
