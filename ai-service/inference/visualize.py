"""Detection / person-PPE төлөвийг frame дээр зурах (debug window, test, snapshot-д дахин ашиглана)."""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from inference.detection_types import Detection, PPEClass
from inference.person_ppe import PersonPPE, PPEState

# BGR
_COLORS = {
    PPEClass.PERSON.value: (255, 160, 0),
    PPEClass.HELMET.value: (0, 200, 0),
    PPEClass.WORK_UNIFORM.value: (0, 200, 0),
    PPEClass.NO_HELMET.value: (0, 0, 255),
    PPEClass.NO_WORK_UNIFORM.value: (0, 0, 255),
}


def draw_detections(
    frame: np.ndarray, detections: list[Detection], people: Optional[list[PersonPPE]] = None
) -> np.ndarray:
    out = frame.copy()
    scale = max(0.8, out.shape[1] / 1280)  # том frame дээр шошго мөн томорно (preview-д жижигрэхээс сэргийлнэ)
    box_th = max(2, round(3 * scale))
    font = 0.75 * scale
    font_th = max(2, round(2 * scale))
    by_person = {id(p.person): p for p in (people or [])}
    for det in detections:
        color = _COLORS.get(det.class_name, (200, 200, 200))
        p1, p2 = (int(det.x1), int(det.y1)), (int(det.x2), int(det.y2))
        cv2.rectangle(out, p1, p2, color, box_th)
        label = f"{det.class_name} {det.confidence:.2f}"
        person = by_person.get(id(det))
        if person:
            ident = f"T{person.track_id}" if person.track_id is not None else person.label
            label = f"{ident} H:{person.helmet.value} U:{person.uniform.value}"
            if PPEState.NO in (person.helmet, person.uniform):
                color = (0, 0, 255)
                cv2.rectangle(out, p1, p2, color, box_th + 1)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font, font_th)
        pad = max(4, round(5 * scale))
        y = max(int(det.y1), th + 2 * pad)
        cv2.rectangle(out, (p1[0], y - th - 2 * pad), (p1[0] + tw + 2 * pad, y), color, -1)
        cv2.putText(out, label, (p1[0] + pad, y - pad), cv2.FONT_HERSHEY_SIMPLEX, font, (255, 255, 255), font_th, cv2.LINE_AA)
    return out
