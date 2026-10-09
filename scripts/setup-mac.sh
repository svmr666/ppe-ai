#!/bin/zsh
# Аль ч Mac (Apple Silicon / Intel) дээр worker-ийг ажиллуулахад хэрэгтэй бүхнийг суулгана. Homebrew шаардахгүй.
# Давтан ажиллуулахад аюулгүй: байгаа зүйлийг дахин татахгүй.
#   ./scripts/setup-mac.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ROOT/bin"
MEDIAMTX_VERSION="v1.21.2"
PYTHON_VERSION="3.12"
mkdir -p "$BIN"
export PATH="$BIN:$PATH"

say() { print -P "%F{cyan}[setup]%f $1"; }
warn() { print -P "%F{yellow}[setup] АНХААР:%f $1"; }

case "$(uname -m)" in
  arm64) GOARCH=arm64; RUSTARCH=aarch64 ;;
  x86_64) GOARCH=amd64; RUSTARCH=x86_64 ;;
  *) print "Дэмжигдээгүй архитектур: $(uname -m)"; exit 1 ;;
esac

fetch() {  # fetch <url> <tar-ийн доторх зам> <гаралтын нэр>
  local tmp; tmp="$(mktemp -d)"
  curl -fsSL "$1" -o "$tmp/archive.tgz"
  tar -xzf "$tmp/archive.tgz" -C "$tmp"
  mv "$tmp/$2" "$BIN/$3" && chmod +x "$BIN/$3"
  rm -rf "$tmp"
}

if [[ ! -x "$BIN/uv" ]]; then
  say "uv татаж байна…"
  fetch "https://github.com/astral-sh/uv/releases/latest/download/uv-${RUSTARCH}-apple-darwin.tar.gz" "uv-${RUSTARCH}-apple-darwin/uv" uv
fi

if [[ ! -x "$BIN/mediamtx" ]]; then
  say "MediaMTX ${MEDIAMTX_VERSION} татаж байна…"
  fetch "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_darwin_${GOARCH}.tar.gz" mediamtx mediamtx
fi

if [[ ! -x "$BIN/cloudflared" ]]; then
  say "cloudflared татаж байна…"
  fetch "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-${GOARCH}.tgz" cloudflared cloudflared
fi

for service in backend ai-service; do
  dir="$ROOT/$service"
  if [[ ! -x "$dir/.venv/bin/python" ]]; then
    say "$service: Python ${PYTHON_VERSION} venv үүсгэж байна…"
    uv venv --python "$PYTHON_VERSION" "$dir/.venv"
  fi
  say "$service: requirements суулгаж байна…"
  uv pip install --python "$dir/.venv/bin/python" -q -r "$dir/requirements.txt"
  if [[ ! -f "$dir/.env" ]]; then
    cp "$dir/.env.example" "$dir/.env" && chmod 600 "$dir/.env"
    warn "$service/.env шинээр үүслээ — CHANGE_ME / YOUR_* утгуудыг бөглөнө үү (эсвэл хуучин Mac-аас хуулна)."
  fi
done

model_path="$(grep -E '^MODEL_PATH=' "$ROOT/ai-service/.env" | cut -d= -f2- || true)"
if [[ -n "$model_path" && ! -f "$ROOT/ai-service/$model_path" && ! -f "$model_path" ]]; then
  warn "YOLO model олдсонгүй: ai-service/$model_path — model файлаа хуулна уу (git-д ордоггүй)."
fi

if ! grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' "$ROOT/backend/.env"; then
  say "CLOUDFLARE_TUNNEL_TOKEN алга — quick tunnel ашиглана (хаяг нь Supabase-ийн runtime-config/endpoints.json-д нийтлэгдэж, Vercel өөрөө олно)."
fi

say "Бэлэн. Асаах: давхар товшино start-worker.command (эсвэл: python3 worker.py)"
