#!/usr/bin/env python3
"""MediaMTX + backend + AI service-ийг НЭГ терминалд ажиллуулна. ⌃C дарахад бүгд зэрэг зогсоно.

    python3 worker.py

Dashboard (web) тусад нь:  cd web && npm run dev
"""

from __future__ import annotations

import os
import re
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
LOCAL_BIN = ROOT / "bin"  # scripts/setup-mac.sh энд mediamtx, cloudflared татна
BACKEND_PORT = 8001
BACKEND_READY_TIMEOUT = 40
TUNNEL_READY_TIMEOUT = 40
STOP_TIMEOUT = 8


def say(message: str) -> None:
    print(f"[worker] {message}", flush=True)


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class UrlWatch(threading.Event):
    """cloudflared quick tunnel-ийн log-оос анхны https://*.trycloudflare.com хаягийг барина."""

    PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def __init__(self) -> None:
        super().__init__()
        self.url = ""

    def feed(self, line: str) -> None:
        match = None if self.is_set() else self.PATTERN.search(line)
        if match:
            self.url = match.group(0)
            self.set()


def pump(name: str, stream, watch: UrlWatch | None = None) -> None:
    for line in iter(stream.readline, ""):
        print(f"[{name}] {line.rstrip()}", flush=True)
        if watch is not None:
            watch.feed(line)


def find_binary(name: str) -> str | None:
    local = LOCAL_BIN / name
    return str(local) if local.is_file() and os.access(local, os.X_OK) else shutil.which(name)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def spawn(name: str, command: list[str], cwd: Path, extra_env: dict[str, str] | None = None,
          watch: UrlWatch | None = None) -> subprocess.Popen:
    # start_new_session: терминалын ⌃C шууд child-д очихгүй, бид зөв дарааллаар (AI -> backend -> mediamtx) зогсооно
    proc = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **(extra_env or {})}, start_new_session=True,
    )
    threading.Thread(target=pump, args=(name, proc.stdout, watch), daemon=True).start()
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
    mediamtx = find_binary("mediamtx")
    backend_py = ROOT / "backend/.venv/bin/python"
    ai_py = ROOT / "ai-service/.venv/bin/python"
    backend_env = read_env(ROOT / "backend/.env")
    # named: өөрийн домэйн (тогтмол хаяг) | quick: домэйнгүй, хаяг Supabase-д нийтлэгдэнэ | off: зөвхөн local
    tunnel_token = backend_env.get("CLOUDFLARE_TUNNEL_TOKEN", "")
    tunnel_mode = "named" if tunnel_token else ("off" if backend_env.get("CLOUDFLARE_TUNNEL", "").lower() == "off" else "quick")
    cloudflared = find_binary("cloudflared") if tunnel_mode != "off" else None
    # Dashboard (Vercel) домэйнээс WebRTC видео үзэхийг зөвшөөрнө — backend-ийн CORS_ORIGINS-тэй ижил
    origins = ",".join(o.strip().rstrip("/") for o in backend_env.get("CORS_ORIGINS", "").split(",") if o.strip())
    missing = [msg for ok, msg in (
        (mediamtx, "mediamtx олдсонгүй (./scripts/setup-mac.sh ажиллуулна уу)"),
        (cloudflared or tunnel_mode == "off", "cloudflared олдсонгүй (./scripts/setup-mac.sh ажиллуулна уу)"),
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
        backend_extra: dict[str, str] = {}
        public_urls: tuple[str, str] | None = None
        if tunnel_mode == "quick":
            watches = {"tunnel-api": (UrlWatch(), BACKEND_PORT), "tunnel-video": (UrlWatch(), 8889)}
            for name, (watch, port) in watches.items():
                procs[name] = spawn(name, [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"], ROOT, watch=watch)
            say("Cloudflare tunnel-ийн хаягийг хүлээж байна…")
            deadline = time.time() + TUNNEL_READY_TIMEOUT
            if not all(w.wait(max(0.0, deadline - time.time())) for w, _ in watches.values()):
                say("АЛДАА: Cloudflare tunnel хаяг авч чадсангүй (интернэт холболтоо шалгана уу)")
                return 1
            api_url, video_url = watches["tunnel-api"][0].url, watches["tunnel-video"][0].url
            public_urls = (api_url, video_url)
            backend_extra = {"MEDIAMTX_PUBLIC_URL": video_url, "PUBLIC_WS_URL": api_url.replace("https://", "wss://", 1) + "/ws"}

        procs["mediamtx"] = spawn("mediamtx", [mediamtx, "infra/mediamtx.yml"], ROOT,
                                  {"MTX_WEBRTCALLOWORIGINS": origins} if origins else None)
        procs["backend"] = spawn("backend", [str(backend_py), "-m", "uvicorn", "app.main:app", "--port", str(BACKEND_PORT)],
                                 ROOT / "backend", backend_extra)
        say("backend-ийг хүлээж байна…")
        if not backend_ready(procs["backend"]):
            say("АЛДАА: backend ассангүй (дээрх [backend] мөрүүдийг харна уу)")
            return 1
        if public_urls:
            # Vercel энэ Mac-ийг Supabase Storage дахь endpoints.json-оос олно
            published = subprocess.run([str(backend_py), "-m", "scripts.publish_endpoints", *public_urls], cwd=ROOT / "backend")
            if published.returncode != 0:
                say("АНХААР: tunnel хаягийг Supabase-д нийтэлж чадсангүй — Vercel энэ Mac руу холбогдохгүй")
            say(f"нийтийн хаяг: API {public_urls[0]} | видео {public_urls[1]}")
        procs["ai-service"] = spawn("ai-service", [str(ai_py), "main.py", "serve"], ROOT / "ai-service")
        if tunnel_mode == "named":
            # Token-г argv-д биш env-ээр дамжуулна (ps-д харагдахгүй)
            procs["tunnel"] = spawn("tunnel", [cloudflared, "tunnel", "--no-autoupdate", "run"], ROOT, {"TUNNEL_TOKEN": tunnel_token})
        say("бэлэн — MediaMTX, backend, AI service" + ("" if tunnel_mode == "off" else ", Cloudflare tunnel") + " ажиллаж байна. Зогсоох: ⌃C")

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
        for name in ("tunnel", "tunnel-api", "tunnel-video", "ai-service", "backend", "mediamtx"):  # tunnel -> AI -> backend -> mediamtx
            proc = procs.get(name)
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    sys.exit(main())
