#!/usr/bin/env python3
"""Шошглосон зургаар PPE модель сургана → models/ppe-trained-<цаг>.onnx

Ихэвчлэн dashboard → Сургалт → «Сургах» товчоор ажиллана (backend дуудна). Гараар:

    cd training && ../ai-service/.venv/bin/python train.py --epochs 40 --base models/base-n.pt

Нууцлал: ultralytics-ийн анонимоор статистик илгээх (sync) унтраалттай, дутуу сан автоматаар суулгахгүй,
суурь жин локал файл байх ёстой (интернэтээс юу ч татахгүй).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("YOLO_AUTOINSTALL", "False")

from dataset_split import CLASSES, build  # noqa: E402

STATE = ROOT / "runs" / "state.json"


def update_state(**fields) -> None:
    try:
        state = json.loads(STATE.read_text()) if STATE.is_file() else {}
    except ValueError:
        state = {}
    state.update(fields)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    tmp.replace(STATE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    if torch.cuda.is_available():
        return "0"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def coco_person_detector(conf: float = 0.4):
    """Хүнийг шошглохгүй тул COCO суурь модель (models/base-n.pt)-оор олж сургалтын өгөгдөлд нэмнэ."""
    from ultralytics import YOLO

    weights = ROOT / "models" / "base-n.pt"
    if not weights.is_file():
        print("АНХААР: models/base-n.pt олдсонгүй — хүн автоматаар нэмэгдэхгүй", flush=True)
        return None
    model = YOLO(str(weights))

    def detect(image: Path) -> list[tuple[float, float, float, float]]:
        res = model.predict(str(image), classes=[0], conf=conf, verbose=False, device="cpu")[0]
        return [tuple(float(v) for v in xywhn) for xywhn in res.boxes.xywhn.cpu().numpy()] if res.boxes is not None else []

    return detect


def evaluate(model_path: Path, data_yaml: str, device: str) -> dict:
    from ultralytics import YOLO

    m = YOLO(str(model_path)).val(data=data_yaml, split="test", imgsz=640, device=device, plots=False, verbose=False)
    per_class = {}
    for i, cls_idx in enumerate(m.box.ap_class_index):
        per_class[m.names[int(cls_idx)]] = {
            "precision": round(float(m.box.p[i]), 3), "recall": round(float(m.box.r[i]), 3), "map50": round(float(m.box.ap50[i]), 3),
        }
    return {"map50": round(float(m.box.map50), 3), "map50_95": round(float(m.box.map), 3), "per_class": per_class}


def run(args: argparse.Namespace) -> None:
    from ultralytics import YOLO, settings

    settings.update({"sync": False})  # анонимоор ч гэсэн статистик илгээхгүй
    base = Path(args.base)
    if not base.is_file():
        raise SystemExit(f"Суурь жин олдсонгүй: {base}")

    summary = build(ROOT, ROOT / "runs" / "dataset", person_detector=coco_person_detector())
    print(f"Өгөгдөл: {summary['images']} | хайрцаг (train): {summary['boxes']['train']}", flush=True)
    if summary["missing_in_train"]:
        print(f"АНХААР: train-д эдгээр ангилал огт байхгүй (сурахгүй): {', '.join(summary['missing_in_train'])}", flush=True)

    device = pick_device(args.device)
    print(f"Төхөөрөмж: {device} | суурь: {base.name} | epoch: {args.epochs}", flush=True)
    model = YOLO(str(base))
    model.train(
        data=summary["data_yaml"], epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=device,
        project=str(ROOT / "runs"), name="run", exist_ok=True, patience=15, workers=2, plots=False, amp=False, seed=0,
    )

    best = ROOT / "runs" / "run" / "weights" / "best.pt"
    if not best.is_file():
        raise RuntimeError("best.pt үүссэнгүй")
    stamp = time.strftime("%Y%m%d_%H%M")
    (ROOT / "models").mkdir(exist_ok=True)
    kept_pt = ROOT / "models" / f"ppe-trained-{stamp}.pt"
    kept_pt.write_bytes(best.read_bytes())  # дараагийн сургалтад суурь болгож болно

    metrics = evaluate(kept_pt, summary["data_yaml"], device)
    print(f"Test үнэлгээ: mAP50={metrics['map50']} mAP50-95={metrics['map50_95']}", flush=True)

    exported = Path(YOLO(str(kept_pt)).export(format="onnx", imgsz=args.imgsz, simplify=False, dynamic=False))
    onnx = ROOT / "models" / f"ppe-trained-{stamp}.onnx"
    exported.replace(onnx)
    update_state(state="done", finished_at=now(), metrics={**metrics, "images": summary["images"]},
                 model_file=f"training/models/{onnx.name}", message="Сургалт амжилттай дууслаа")
    print(f"Бэлэн: {onnx}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="Суурь жин (.pt), жишээ models/base-n.pt")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="auto", help="auto | cpu | mps | 0")
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except SystemExit as exc:
        update_state(state="failed", finished_at=now(), message=str(exc.code))
        raise
    except Exception as exc:  # алдааг хэрэглэгчид ойлгомжтой харуулна, дэлгэрэнгүй нь логт
        import traceback

        traceback.print_exc()
        update_state(state="failed", finished_at=now(), message=f"{type(exc).__name__}: {str(exc)[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
