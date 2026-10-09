from inference.detection_types import Detection
from inference.person_ppe import AnalyzerConfig, PersonPPEAnalyzer, PPEState


def det(name, x1, y1, x2, y2, conf=0.9):
    return Detection(name, conf, x1, y1, x2, y2)


# Person A: x 100-300, y 100-500 (толгойн бүс y<=280). Person B: x 500-700.
PERSON_A = det("person", 100, 100, 300, 500)
PERSON_B = det("person", 500, 100, 700, 500)
HELMET_A = det("helmet", 150, 100, 250, 170)
HELMET_B = det("helmet", 550, 100, 650, 170)
HEAD_B = det("no_helmet", 550, 100, 650, 170)
UNIFORM_A = det("work_uniform", 110, 200, 290, 450)
UNIFORM_B = det("work_uniform", 510, 200, 690, 450)


def analyze(dets, **cfg):
    return PersonPPEAnalyzer(AnalyzerConfig(**cfg)).analyze(dets)


def test_single_person_fully_equipped():
    (p,) = analyze([PERSON_A, HELMET_A, UNIFORM_A])
    assert (p.helmet, p.uniform) == (PPEState.YES, PPEState.YES)


def test_two_people_one_without_helmet():
    p1, p2 = analyze([PERSON_A, PERSON_B, HELMET_A, HEAD_B, UNIFORM_A, UNIFORM_B])
    assert p1.label == "Person #1" and p2.label == "Person #2"
    assert (p1.helmet, p1.uniform) == (PPEState.YES, PPEState.YES)
    assert (p2.helmet, p2.uniform) == (PPEState.NO, PPEState.YES)
    assert p2.helmet_evidence.class_name == "no_helmet"


def test_persons_are_numbered_left_to_right_regardless_of_input_order():
    p1, p2 = analyze([PERSON_B, PERSON_A])
    assert p1.person is PERSON_A and p2.person is PERSON_B


def test_helmet_goes_to_the_closest_head_when_people_overlap():
    a = det("person", 100, 100, 300, 500)
    b = det("person", 200, 100, 400, 500)  # A-тай давхцсан
    helmet_on_a = det("helmet", 150, 100, 250, 170)  # төв x=200 -> A-ийн толгой
    p1, p2 = analyze([a, b, helmet_on_a])
    assert p1.helmet == PPEState.YES
    assert p2.helmet == PPEState.UNKNOWN


def test_helmet_outside_head_zone_is_not_assigned():
    belt_level = det("helmet", 150, 380, 250, 450)
    (p,) = analyze([PERSON_A, belt_level])
    assert p.helmet == PPEState.UNKNOWN


def test_uniform_outside_person_is_ignored():
    elsewhere = det("work_uniform", 800, 200, 900, 400)
    (p,) = analyze([PERSON_A, elsewhere])
    assert p.uniform == PPEState.UNKNOWN


def test_conflicting_evidence_higher_confidence_wins():
    helmet = det("helmet", 150, 100, 250, 170, conf=0.6)
    head = det("no_helmet", 150, 100, 250, 170, conf=0.8)
    (p,) = analyze([PERSON_A, helmet, head])
    assert p.helmet == PPEState.NO
    stronger_helmet = det("helmet", 150, 100, 250, 170, conf=0.95)
    (p,) = analyze([PERSON_A, stronger_helmet, head])
    assert p.helmet == PPEState.YES


def test_absence_means_no_only_for_large_enough_person():
    small = det("person", 100, 100, 160, 150)  # өндөр 50px
    big, = analyze([PERSON_A], helmet_absence_means_no=True, uniform_absence_means_no=True)
    tiny, = analyze([small], helmet_absence_means_no=True, uniform_absence_means_no=True)
    assert (big.helmet, big.uniform) == (PPEState.NO, PPEState.NO)
    assert (tiny.helmet, tiny.uniform) == (PPEState.UNKNOWN, PPEState.UNKNOWN)


def test_absence_is_unknown_by_default():
    (p,) = analyze([PERSON_A])
    assert (p.helmet, p.uniform) == (PPEState.UNKNOWN, PPEState.UNKNOWN)


def test_explicit_evidence_counts_even_for_small_person():
    small = det("person", 100, 100, 160, 150)
    helmet = det("helmet", 105, 98, 150, 120)
    (p,) = analyze([small, helmet])
    assert p.helmet == PPEState.YES


def test_config_from_user_model_classes():
    # .env mapping: person, helmet, no_helmet (head), work_uniform — no_work_uniform байхгүй
    cfg = AnalyzerConfig.from_mapped_classes({"person", "helmet", "no_helmet", "work_uniform"})
    assert cfg.helmet_absence_means_no is False
    assert cfg.uniform_absence_means_no is True


def test_no_persons_means_no_results():
    assert analyze([HELMET_A, UNIFORM_A]) == []
