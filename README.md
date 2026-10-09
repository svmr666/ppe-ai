# Үйлдвэрийн AI PPE & Аюулгүй ажиллагааны хяналтын систем

Бодит Dahua IP камераас ажилчдын дуулга болон ажлын хувцсыг AI-аар бодит цагт шалгаж, зөрчил илэрвэл
snapshot авч, dashboard дээр шууд мэдэгддэг систем.

```
DAHUA IP CAMERA ─RTSP─► MediaMTX ─WebRTC─► dashboard (шууд видео, 25-30 fps)
        │ RTSP
        ▼
                      ai-service ──► Supabase Storage (private) + PostgreSQL
                         │                         ▲
                         │ дотоод дуудлага          │
                         ▼                         │
                      backend (FastAPI) ───────────┘
                         │ REST + WebSocket (httpOnly cookie)
                         ▼
                      web (Next.js dashboard)
```

## 1. Архитектур

| Хавтас | Үүрэг |
|---|---|
| `ai-service/` | RTSP камер → YOLO → person+PPE → zone дүрэм → violation → snapshot → Storage → DB. Олон камер (`serve`) |
| `backend/` | FastAPI: REST, WebSocket, нэвтрэлт/эрх, signed URL, камер шалгалт |
| `web/` | Next.js + TypeScript + Tailwind + shadcn/ui dashboard (Монгол хэл) |
| `infra/mediamtx.yml` | MediaMTX тохиргоо (WebRTC, нэвтрэлт backend-ээр) |
| `database/schema.sql` | PostgreSQL schema + private Storage bucket |

Pipeline: `камер → OpenCV frame → YOLO → person+PPE холбох → tracker → zone дүрэм → баталгаажуулалт + cooldown →
snapshot → Supabase Storage → PostgreSQL → backend → WebSocket → dashboard`

## 2. Шаардлага
- Python **3.10+** (3.12 зөвлөнө; macOS-ийн system `python3` 3.9 тул хэрэглэхгүй)
- Node.js 20+
- Supabase project, Dahua IP камер (RTSP идэвхтэй, нэг сүлжээнд)
- Docker (заавал биш)

## 3. Python setup
```bash
cd ai-service && python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
cd ../backend  && python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

## 4. Node setup
```bash
cd web && npm install && cp .env.local.example .env.local
```

## 5. Supabase setup
1. https://supabase.com дээр project үүсгэнэ.
2. **SQL Editor** → `database/schema.sql`-ийг бүтнээр нь Run хийнэ (дахин ажиллуулахад аюулгүй). Энэ нь `zones`, `cameras`,
   `violations`, `users`, index-үүд, `FACTORY`/`OFFICE` zone, **private** `violation-snapshots` bucket-ийг үүсгэнэ.
3. **Project Settings → API**: `Project URL` ба `service_role` key. **Database → Connection string**: `DATABASE_URL`.

## 6. Environment variables
```bash
cp ai-service/.env.example ai-service/.env && cp backend/.env.example backend/.env && chmod 600 ai-service/.env backend/.env
```
- `DATABASE_URL`: нууц үгэнд `? @ $ & : /` байвал **URL-encode** хийнэ (`?` → `%3F`).
- `SUPABASE_URL` = `https://<ref>.supabase.co` (DB connection string БИШ).
- Түлхүүр үүсгэх:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # CAMERA_SECRET_KEY
  python -c "import secrets; print(secrets.token_urlsafe(48))"                                # JWT_SECRET, INTERNAL_API_KEY
  ```
- `CAMERA_SECRET_KEY` ба `INTERNAL_API_KEY` нь `backend/.env` ба `ai-service/.env`-д **ижил** байна.
- Frontend (`web/.env.local`) зөвхөн `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`. Нууц утга хэзээ ч орохгүй.

## 7. Dahua камерын тохиргоо
Камерын web UI → **Setting → Network → Port**: RTSP `554` идэвхтэй эсэх. Зөвхөн энэ системд зориулсан хэрэглэгч үүсгэвэл сайн.

## 8. RTSP тохиргоо
URL код дотор бичигдэхгүй, тохиргооноос үүснэ (нууц үгийн тусгай тэмдэгтийг автоматаар encode хийнэ):
`rtsp://USER:PASS@IP:554/cam/realmonitor?channel=1&subtype=0`
- `channel` — шууд IP камер бол `1`; `subtype` — `0` үндсэн, `1` туслах урсгал (AI-д ихэвчлэн туслах нь хангалттай, хөнгөн).
- Камер dashboard-оос (**Камерууд → Камер нэмэх**) нэмэгдэнэ; нууц үг **Fernet-ээр шифрлэгдэж** хадгалагдана, API-д буцахгүй.

## 9. YOLO model
```bash
cp /таны/model.pt ai-service/models/initial_model.pt     # эсвэл .onnx
```
`ai-service/.env`: `MODEL_PATH=./models/initial_model.pt`. Дараа нь `best.pt` болгоход зөвхөн энэ мөрийг солино.
Model-ийн class нэр системийнхтэй (`person, helmet, no_helmet, work_uniform, no_work_uniform`) таарахгүй бол:
`CLASS_MAPPING=head:no_helmet,dmp_uniform:work_uniform,dmp_winter_jacket:work_uniform`.
Model-д `no_work_uniform` байхгүй бол хангалттай өндөр (≥80px) хүн дээр `work_uniform` олдохгүй үед `NO_WORK_UNIFORM` гэж үзнэ (`inferred`).

## 10. Database migration
`database/schema.sql`-ийг Supabase SQL Editor дээр Run (5-р хэсэг). Хуучин хувилбараас шинэчлэхэд idempotent.

## 11–13. Ажиллуулах
```bash
# Нэг удаа: ADMIN үүсгэнэ (нууц үгийг terminal нуугдмалаар асууна)
cd backend && .venv/bin/python -m scripts.create_admin you@company.com "Таны нэр"

# 1) WORKER — MediaMTX + backend (:8001) + AI service-ийг нэг терминалд (⌃C = бүгд зогсоно)
python3 worker.py

# 2) WEB — dashboard (http://localhost:3000; production: npm run build && npm start)
cd web && npm run dev
```
VS Code-д: **⌘⇧B** (Бүгдийг асаах) = worker + web хоёр терминал. Тусад нь ажиллуулах бол: `mediamtx infra/mediamtx.yml`,
`cd backend && .venv/bin/python -m uvicorn app.main:app --port 8001`, `cd ai-service && .venv/bin/python main.py serve`.
Dashboard → **Камерууд → Камер нэмэх → Холболт шалгах → Хадгалах**. AI service 10 сек тутам DB-г уншиж камерыг автоматаар
эхлүүлнэ/зогсооно/дахин эхлүүлнэ (**Холбох / Салгах** товч). Эхний шалгалтад `.env`-ийн нэг камераар `python main.py run`.

Эрх: `ADMIN` (бүгд, камер/хэрэглэгч удирдах), `HUB_EMPLOYEE` (зөрчил хүлээн авах/шийдвэрлэх).

## Vercel + аль ч Mac дээр ажиллуулах

Web нь Vercel дээр, backend + ai-service + MediaMTX нь камертай нэг сүлжээнд байгаа Mac дээр ажиллаж, Cloudflare tunnel-ээр холбогдоно. Нэг удаад зөвхөн нэг Mac асаана. Vercel-ийн `/api/*` нь backend руу proxy хийгдэх тул login cookie first-party.

- **Домэйнгүй (анхдагч)**: worker quick tunnel нээж, хаягаа Supabase Storage-ийн public `runtime-config/endpoints.json`-д бичнэ. Vercel: `BACKEND_REGISTRY_URL=https://<ref>.supabase.co/storage/v1/object/public/runtime-config/endpoints.json`. Mac солих/дахин асаахад redeploy хэрэггүй.
- **Өөрийн домэйнтэй**: Cloudflare named tunnel (`api.<домэйн>` -> `localhost:8001`, `video.<домэйн>` -> `localhost:8889`), `backend/.env`-д `CLOUDFLARE_TUNNEL_TOKEN`, `MEDIAMTX_PUBLIC_URL=https://video.<домэйн>`. Vercel: `BACKEND_ORIGIN=https://api.<домэйн>`, `NEXT_PUBLIC_WS_URL=wss://api.<домэйн>/ws`.
- **backend/.env**: `APP_ENV=production`, `COOKIE_SECURE=true`, `CORS_ORIGINS=https://<vercel-домэйн>`. Нууц утга Vercel-д ордоггүй.

Шинэ Mac дээр:

1. `git clone https://github.com/svmr666/ppe-ai.git && cd ppe-ai`
2. `backend/.env`, `ai-service/.env`, YOLO model-оо хуучин Mac-аас хуулна (git-д ордоггүй).
3. `start-worker.command`-г давхар товшино — анх удаа `scripts/setup-mac.sh` (uv + Python 3.12, MediaMTX, cloudflared -> `./bin`, venv) автоматаар ажиллана.

## Моделийн сургалт
Dashboard → **Сургалт** (ADMIN): камераас зураг цуглуулах → браузер дотор шошглох → сургах. Дэлгэрэнгүй: [training/README.md](training/README.md).

## 14. Камер шалгах
```bash
python main.py test-camera            # холбогдож, 10 сек stream уншиж, ai-service/temp/test_frame.jpg хадгална
python main.py test-camera --display  # live цонх (q)
python main.py watch-camera           # state-ийг харна; сүлжээний утсаа салгаж reconnect шалгана
```
Хүлээгдэх: `Camera connected successfully · Resolution: 1920x1080 · RTSP stream active · FPS: ...`.
Салгавал `ONLINE → ERROR → RECONNECTING → ONLINE` (exponential backoff).

## 15. AI шалгах
```bash
python main.py test-yolo                       # камерын frame дээр 15 сек
python main.py test-yolo --image photo.jpg     # зураг дээр
python main.py test-yolo --zone OFFICE
```
Person бүрийн `Helmet / Uniform: YES|NO|UNKNOWN` ба zone-ийн үр дүн гарна.

## 16. Violation шалгах
`python main.py run` (эсвэл `serve`) → хүн дуулгагүй зогсоход:
`⚠️ VIOLATION NO_HELMET | camera=... zone=FACTORY person=T1 confidence=91%` → dashboard дээр **⚠️ PPE ЗӨРЧИЛ** мэдэгдэл.
- **Баталгаажуулалт:** сүүлийн 5 frame-ийн ≥3-т илрэх (`VIOLATION_CONFIRM_*`).
- **Cooldown:** нэг хүн + нэг зөрчил `VIOLATION_COOLDOWN_SECONDS` (10 сек) дотор давтагдахгүй; өөр хүн тусдаа.
- Zone: FACTORY — дуулга+хувцас заавал; OFFICE — хувцас заавал, дуулга заавал биш. `UNKNOWN` хэзээ ч зөрчил болгохгүй.

## 17. Snapshot шалгах
Зөрчил бүрт улаан хайрцаг + шошго «#1 Каскгүй · Ажлын хувцасгүй» ба мэдээллийн самбар (төрөл, камер, бүс, итгэлцэл, цаг UTC+8, бүгд Монголоор)-тай JPEG үүсч `violations/YYYY/MM/DD/<camera-id>/<violation-id>.jpg`
замаар **private** Storage-д орно; DB-д зөвхөн `storage_path`. Dashboard-д backend богино хугацаатай (1 цаг) signed URL өгнө.
`SNAPSHOT_SAVE_LOCAL=true` үед `ai-service/snapshots/`-д локал хуулбар.

## 18. Алдаа заслын заавар
| Шинж тэмдэг | Шийдэл |
|---|---|
| `TCP холболт хугацаа хэтэрлээ` | IP буруу / камер унтраалттай / өөр сүлжээ. `ping IP` |
| `Connection refused` | RTSP порт буруу эсвэл камер дээр RTSP хаалттай |
| `Порт нээлттэй боловч RTSP stream нээгдсэнгүй` | Хэрэглэгч/нууц үг, channel/subtype буруу, account түгжигдсэн |
| `зураг (frame) ирсэнгүй` | Codec (H.265) дэмжигдэхгүй — камерт H.264 сонго эсвэл `subtype=1` |
| `AI_MODEL_ERROR` | `MODEL_PATH` буруу / файл алга |
| `DATABASE_ERROR` | `DATABASE_URL` (нууц үгийг URL-encode хийсэн үү), Supabase pause болсон эсэх. Алдагдсан violation → `ai-service/snapshots/failed-db/*.json` |
| `STORAGE_UPLOAD_ERROR` | `SUPABASE_*` түлхүүр, bucket. Зураг → `snapshots/failed-upload/` |
| `WEBSOCKET_ERROR` (ai-service log) | backend ажиллаж байгаа эсэх, `INTERNAL_API_KEY` хоёр талд ижил эсэх |
| Dashboard "AI ТӨЛӨВ: САЛСАН" | `python main.py serve` ажиллаж байгаа эсэх |
| Нэвтрэхэд 429 | 5 буруу оролдлогын дараа 15 минут түгжинэ |
| Dashboard камер "Зураг хүлээж байна" | Камер `Холбох` болсон, `serve` ажиллаж байгаа эсэх |

## 19. Production deployment
- **HTTPS заавал**; `backend/.env`: `COOKIE_SECURE=true`, `APP_ENV=production` (/docs унтарна), `CORS_ORIGINS=https://dashboard.your.company`.
- Frontend ба backend нэг site-ийн дор (жишээ `app.` / `api.` дэд домэйн) байвал cookie (`SameSite=Lax`) ажиллана.
  `NEXT_PUBLIC_API_URL=https://api...`, `NEXT_PUBLIC_WS_URL=wss://api.../ws`.
- `docker compose up --build` (⚠ энэ машинд Docker суугаагүй тул `docker-compose.yml` туршигдаагүй).
- ai-service-д **GPU** (`INFERENCE_DEVICE=cuda:0`) зөвлөнө: олон камер нэг model-ийг дарааллаар хуваалцдаг тул CPU дээр камер бүрийн FPS буурна.
- Нууцууд: `.env` файлууд git-д/image-д орохгүй; rotate хийх бол `service_role`, DB нууц үг, `JWT_SECRET`, `INTERNAL_API_KEY`.
- Олон instance backend ажиллуулах бол login rate-limit, frame/heartbeat санах ойн хадгалалт, WebSocket broadcast-ыг Redis зэрэг рүү шилжүүлнэ.

## Custom YOLO model (Phase 17)
Roboflow dataset-д `person, helmet, no_helmet, work_uniform, no_work_uniform` class бэлдэнэ.
```bash
pip install roboflow && export ROBOFLOW_API_KEY=...
python -m tools.train_custom_model --workspace WS --project PROJ --version 1 --epochs 100   # → models/best.pt
```
Дараа нь `.env`: `MODEL_PATH=./models/best.pt` (`CLASS_MAPPING`-ийг хоослоно). Өөр код өөрчлөгдөхгүй.

## Нууцлал (хураангуй)
- Камерын нууц үг DB-д Fernet-ээр шифрлэгдэнэ; API/log/WebSocket-д хэзээ ч гарахгүй (RTSP/DB URL-ийн нууц үг `***`).
- Хэрэглэгчийн нууц үг bcrypt; JWT httpOnly cookie; CSRF (Origin шалгалт + SameSite); login brute-force хамгаалалт; WebSocket нэвтрэлт + Origin шалгалт.
- Storage bucket **private**; зураг зөвхөн нэвтэрсэн хэрэглэгчид signed URL-ээр. `service_role` key зөвхөн ai-service/backend.
- SQL бүгд parameterized; DB руу SSL; ai-service ↔ backend дотоод дуудлага `X-Internal-Key`-тэй.

## Тест
```bash
cd ai-service && python -m pytest                      # unit (камер/AI/дүрэм/snapshot/manager)
cd backend    && python -m pytest                      # unit
cd backend    && RUN_DB_TESTS=1 python -m pytest       # + бодит Supabase integration (өөрийн өгөгдлөө устгана)
cd ai-service && RUN_DB_TESTS=1 python -m pytest tests/test_repository_integration.py
cd web        && npx tsc --noEmit && npm run lint && npm run build
```

## Мэдэгдэж буй хязгаарлалт
- Бодит Dahua камер дээр RTSP нээх хэсэг (`test-camera`) **таны камер дээр туршигдаагүй** (mp4 dev adapter-аар pipeline шалгасан).
- Шууд видео MediaMTX-ээр (WebRTC) явдаг тул `mediamtx infra/mediamtx.yml` заавал ажиллаж байх ёстой. H.265 камерын видео browser-д WebRTC-ээр тоглогдохгүй — камерт H.264 сонгоно. LAN/production дээр `infra/mediamtx.yml` дахь хаяг, `webrtcAdditionalHosts`, `MEDIAMTX_PUBLIC_URL`-ийг тохируулна.
- AI хайрцаг нь видеоны дээр canvas-аар давхарлагддаг (WebSocket-ээр ирдэг, видеотой ~0.3 сек зөрж болно).
- Tracker нь хүнийг таньдаггүй: 3 сек харагдахгүй байгаад буцаж ирвэл шинэ ID авна.
- Snapshot-ийн Монгол текст Кирилл дэмждэг системийн фонт (Arial/DejaVu/Liberation) шаардана; олдохгүй бол ASCII болно.
- Docker файлууд туршигдаагүй. Custom model сургалтын script ажиллуулаагүй (dataset/GPU шаардана).
# ppe-ai
