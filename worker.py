#!/usr/bin/env python3
"""MediaMTX + backend + AI service-ийг НЭГ терминалд ажиллуулна. ⌃C дарахад бүгд зэрэг зогсоно.

    python3 worker.py

Dashboard (web) тусад нь:  cd web && npm run dev
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_PORT = 8001
BACKEND_READY_TIMEOUT = 40
STOP_TIMEOUT = 8


def say(message: str) -> None:
    print(f"[worker] {message}", flush=True)


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pump(name: str, stream) -> None:
    for line in iter(stream.readline, ""):
        print(f"[{name}] {line.rstrip()}", flush=True)


def spawn(name: str, command: list[str], cwd: Path) -> subprocess.Popen:
    # start_new_session: терминалын ⌃C шууд child-д очихгүй, бид зөв дарааллаар (AI -> backend -> mediamtx) зогсооно
    proc = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"}, start_new_session=True,
    )
    threading.Thread(target=pump, args=(name, proc.stdout), daemon=True).start()
    return proc


def backend_ready(proc: subprocess.Popen) -> bool:
    deadline = time.time() + BACKEND_READY_TIMEOUT
    while time.time() < deadline and proc.poll() is None:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{BACKEND_PORT}/health", timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # сервер хариу өгч байна
        except OSError:
            time.sleep(0.5)
    return False


def main() -> int:
    mediamtx = shutil.which("mediamtx")
    backend_py = ROOT / "backend/.venv/bin/python"
    ai_py = ROOT / "ai-service/.venv/bin/python"
    missing = [msg for ok, msg in (
        (mediamtx, "mediamtx олдсонгүй (brew install mediamtx)"),
        (backend_py.exists(), "backend/.venv байхгүй (README-ийн суулгалтыг хийнэ үү)"),
        (ai_py.exists(), "ai-service/.venv байхгүй (README-ийн суулгалтыг хийнэ үү)"),
    ) if not ok]
    busy = [f"порт {p} ({who}) завгүй — өмнөх үйлчилгээгээ зогсооно уу" for p, who in
            ((BACKEND_PORT, "backend"), (8889, "mediamtx"), (9997, "mediamtx"), (8554, "mediamtx")) if port_busy(p)]
    if missing or busy:
        for msg in missing + busy:
            say(f"АЛДАА: {msg}")
        return 1

    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)

    procs: dict[str, subprocess.Popen] = {}
    reported: set[str] = set()
    try:
        procs["mediamtx"] = spawn("mediamtx", [mediamtx, "infra/mediamtx.yml"], ROOT)
        procs["backend"] = spawn("backend", [str(backend_py), "-m", "uvicorn", "app.main:app", "--port", str(BACKEND_PORT)], ROOT / "backend")
        say("backend-ийг хүлээж байна…")
        if not backend_ready(procs["backend"]):
            say("АЛДАА: backend ассангүй (дээрх [backend] мөрүүдийг харна уу)")
            return 1
        procs["ai-service"] = spawn("ai-service", [str(ai_py), "main.py", "serve"], ROOT / "ai-service")
        say("бэлэн — MediaMTX, backend, AI service ажиллаж байна. Зогсоох: ⌃C")

        while True:
            for name, proc in procs.items():
                code = proc.poll()
                if code is not None and name not in reported:
                    reported.add(name)
                    say(f"{name} зогслоо (код {code})")
            if len(reported) == len(procs):
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        say("зогсоож байна…")
        return 0
    finally:
        for name in ("ai-service", "backend", "mediamtx"):  # AI -> backend -> mediamtx
            proc = procs.get(name)
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    sys.exit(main())
