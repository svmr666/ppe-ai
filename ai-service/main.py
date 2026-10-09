"""AI service entrypoint.

Команд: test-camera, watch-camera, test-yolo, run (camera -> YOLO -> rules -> violation).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config.settings import get_settings
from utils.logger import get_logger, setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Industrial AI PPE & Safety Monitoring — AI service")
    sub = parser.add_subparsers(dest="command", required=True)

    test = sub.add_parser("test-camera", help="Бодит камертай RTSP холболт шалгах")
    test.add_argument("--seconds", type=int, default=10, help="Stream уншиж шалгах хугацаа (default 10)")
    test.add_argument("--display", action="store_true", help="Live цонх нээх (q = гарах)")

    yolo = sub.add_parser("test-yolo", help="YOLO model-ийг камер frame (эсвэл --image) дээр шалгах")
    yolo.add_argument("--seconds", type=int, default=15, help="Камер дээр inference ажиллуулах хугацаа")
    yolo.add_argument("--display", action="store_true", help="Live цонх (q = гарах)")
    yolo.add_argument("--image", type=Path, help="Камергүйгээр нэг зураг дээр шалгах")
    yolo.add_argument("--zone", choices=["FACTORY", "OFFICE"], type=str.upper, help="CAMERA_ZONE-г түр дарж шалгах")

    serve = sub.add_parser("serve", help="PRODUCTION: DB-ийн бүх идэвхтэй камерыг ажиллуулах")
    serve.add_argument("--seconds", type=int, default=0, help="0 = Ctrl+C хүртэл")
    serve.add_argument("--display", action="store_true", help="Эхний камерыг live цонхонд харуулах")

    run = sub.add_parser("run", help=".env дахь НЭГ камерыг ажиллуулах (хөгжүүлэлт/шалгалт)")
    run.add_argument("--seconds", type=int, default=0, help="0 = Ctrl+C хүртэл")
    run.add_argument("--display", action="store_true", help="Live цонх (q = гарах)")
    run.add_argument("--zone", choices=["FACTORY", "OFFICE"], type=str.upper, help="CAMERA_ZONE-г түр дарах")

    watch = sub.add_parser("watch-camera", help="Камерын state-ийг харах (reconnect тест)")
    watch.add_argument("--seconds", type=int, default=0, help="0 = Ctrl+C хүртэл")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    setup_logging(settings.log_level, settings.resolved_log_dir)
    get_logger("main").info("Starting AI service (command=%s)", args.command)

    # camera модулийг lazy import: OPENCV env тохиргоо cv2-ээс өмнө хийгдэнэ.
    from camera.camera_test import run_camera_test, run_camera_watch

    if args.command == "test-camera":
        return run_camera_test(settings, seconds=args.seconds, display=args.display)
    if args.command == "watch-camera":
        return run_camera_watch(settings, seconds=args.seconds)
    if args.command == "serve":
        from camera.run_pipeline import run_serve

        return run_serve(settings, seconds=args.seconds, display=args.display)
    if args.command == "run":
        from camera.run_pipeline import run_pipeline

        return run_pipeline(settings, seconds=args.seconds, display=args.display, zone=args.zone)
    if args.command == "test-yolo":
        from inference.yolo_test import run_yolo_camera_test, run_yolo_image_test

        if args.image:
            return run_yolo_image_test(settings, args.image, zone=args.zone)
        return run_yolo_camera_test(settings, seconds=args.seconds, display=args.display, zone=args.zone)
    return 2


if __name__ == "__main__":
    sys.exit(main())
