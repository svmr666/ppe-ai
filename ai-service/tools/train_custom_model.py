"""Phase 17: Roboflow dataset -> YOLO сургалт -> models/best.pt

Dataset-д дараах 5 class байх ёстой (нэр нь яг ийм, эсвэл .env-ийн CLASS_MAPPING-ээр хөрвүүлнэ):
    person, helmet, no_helmet, work_uniform, no_work_uniform

Ажиллуулах (GPU-тэй машин зөвлөнө; CPU дээр маш удаан):
    pip install roboflow
    export ROBOFLOW_API_KEY=...          # нууц түлхүүр — код/git-д оруулахгүй
    python -m tools.train_custom_model --workspace WS --project PROJ --version 1 --epochs 100

Дууссаны дараа .env дээр:  MODEL_PATH=./models/best.pt   (бусад код өөрчлөгдөхгүй)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_CLASSES = {"person", "helmet", "no_helmet", "work_uniform", "no_work_uniform"}


def normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--base-model", default="yolo11s.pt", help="Pretrained эхлэл model (ultralytics автоматаар татна)")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="", help="'' = auto, 0 = эхний GPU, mps, cpu")
    args = parser.parse_args()

    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print("ROBOFLOW_API_KEY орчны хувьсагч тохируулаагүй байна.")
        return 2

    try:
        from roboflow import Roboflow
    except ImportError:
        print("`pip install roboflow` хийнэ үү.")
        return 2
    from ultralytics import YOLO

    dataset = Roboflow(api_key=api_key).workspace(args.workspace).project(args.project).version(args.version).download("yolov8")
    data_yaml = Path(dataset.location) / "data.yaml"

    import yaml

    names = yaml.safe_load(data_yaml.read_text())["names"]
    names = list(names.values()) if isinstance(names, dict) else list(names)
    missing = REQUIRED_CLASSES - {normalize(n) for n in names}
    print(f"Dataset classes: {names}")
    if missing:
        print(f"ЭРЭМБЭ: dataset-д дараах class байхгүй: {sorted(missing)} — .env-ийн CLASS_MAPPING-ээр хөрвүүлэх эсвэл dataset-ээ засна уу.")

    model = YOLO(args.base_model)
    model.train(
        data=str(data_yaml), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
        device=args.device or None, project=str(SERVICE_ROOT / "runs"), name="ppe", exist_ok=True,
    )

    best = SERVICE_ROOT / "runs" / "ppe" / "weights" / "best.pt"
    if not best.is_file():
        print(f"best.pt олдсонгүй: {best}")
        return 1
    target = SERVICE_ROOT / "models" / "best.pt"
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(best, target)
    print(f"\nБэлэн: {target}\n.env дээр MODEL_PATH=./models/best.pt болгоно уу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
