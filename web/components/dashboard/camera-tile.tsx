"use client";

import { useEffect, useState } from "react";
import { FlipHorizontal2, Power, VideoOff } from "lucide-react";
import { toast } from "sonner";

import { useAuth } from "@/components/providers/auth-provider";

import { DetectionOverlay } from "@/components/dashboard/detection-overlay";
import { CameraStatusBadge } from "@/components/status-badges";
import { api } from "@/lib/api";
import { CAMERA_STATUS_LABEL, cameraTone, zoneLabel } from "@/lib/format";
import { startWhep } from "@/lib/webrtc";
import type { Camera } from "@/types";

const RETRY_MS = 2000;

/**
 * Шууд харагдац: MediaMTX-ээс WebRTC (WHEP)-ээр жинхэнэ видео (25-30 fps) + AI боксын давхарга.
 * Нэвтрэлт: backend-ийн 2 минутын токен. Raw RTSP URL browser-д огт очихгүй.
 */
export function CameraTile({ camera }: { camera: Camera }) {
  const mirror = camera.camera_type === "LOCAL_WEBCAM" && camera.mirror; // IP камер үргэлж анхдагч (эргүүлэхгүй)
  const live = cameraTone(camera.status) !== "idle" && camera.enabled;
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const { can } = useAuth();

  async function toggleMirror() {
    try {
      await api.updateCamera(camera.id, { mirror: !camera.mirror }); // CAMERA_UPDATED event-ээр шууд шинэчлэгдэнэ
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Амжилтгүй боллоо");
    }
  }

  const [powering, setPowering] = useState(false);
  async function togglePower() {
    setPowering(true);
    try {
      if (camera.enabled) await api.disconnectCamera(camera.id);
      else await api.connectCamera(camera.id);
      toast.success(camera.enabled ? "Камерыг унтраалаа" : "Камерыг асааж байна…"); // төлөв CAMERA_UPDATED event-ээр шинэчлэгдэнэ
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Амжилтгүй боллоо");
    } finally {
      setPowering(false);
    }
  }

  useEffect(() => {
    if (!live || !video) return;
    let cancelled = false;
    let pc: RTCPeerConnection | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const retry = () => {
      pc?.close();
      pc = null;
      setPlaying(false);
      if (!cancelled) timer = setTimeout(connect, RETRY_MS);
    };
    const connect = async () => {
      try {
        const access = await api.webrtc(camera.id);
        if (cancelled) return;
        pc = await startWhep(access.whep_url, access.token, (stream) => { video.srcObject = stream; }, retry);
        if (cancelled) pc.close();
      } catch {
        retry(); // MediaMTX-д видео хараахан бэлэн биш / түр тасарсан
      }
    };
    connect();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      pc?.close();
      video.srcObject = null;
    };
  }, [live, video, camera.id]);

  return (
    <div className="group relative overflow-hidden rounded-2xl border bg-black shadow-lg shadow-black/30">
      <div className="relative aspect-video">
        <video
          ref={setVideo}
          autoPlay muted playsInline
          onPlaying={() => setPlaying(true)}
          onEmptied={() => setPlaying(false)}
          className={`size-full bg-black object-contain ${mirror ? "-scale-x-100" : ""}`}
        />
        {playing && camera.ai_enabled && <DetectionOverlay cameraId={camera.id} video={video} mirror={mirror} />}
        {playing && !camera.ai_enabled && (
          <span className="pointer-events-none absolute right-4 top-4 flex items-center gap-2 rounded-full bg-black/60 px-3 py-1 text-xs font-medium text-white backdrop-blur">
            <span className="size-2 rounded-full bg-zinc-500" /> AI салгасан · зөвхөн видео
          </span>
        )}

        {!playing && (
          <div className="absolute inset-0 grid place-items-center bg-gradient-to-br from-zinc-900 to-zinc-950 text-muted-foreground">
            <div className="flex flex-col items-center gap-3 text-sm">
              <VideoOff className="size-10" />
              {camera.enabled ? (live ? "Видео хүлээж байна…" : CAMERA_STATUS_LABEL[camera.status]) : "Камер унтраалттай"}
              {!camera.enabled && can("ADMIN") && (
                <button type="button" disabled={powering} onClick={togglePower}
                  className="mt-1 flex items-center gap-2 rounded-full bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:opacity-60">
                  <Power className="size-4" /> Камер асаах
                </button>
              )}
            </div>
          </div>
        )}

        {playing && (
          <span className="absolute left-4 top-4 flex items-center gap-2 rounded-full bg-black/60 px-3 py-1 text-xs font-semibold tracking-wider text-red-400 backdrop-blur">
            <span className="size-2 animate-pulse rounded-full bg-red-500" /> ШУУД
          </span>
        )}

        {can("ADMIN") && camera.enabled && (
          <button
            type="button" onClick={togglePower} disabled={powering} title="Камерыг унтраах" aria-label="Камерыг унтраах"
            className="absolute right-4 top-14 grid size-9 place-items-center rounded-full bg-black/60 text-white backdrop-blur transition hover:bg-red-600 disabled:opacity-60"
          >
            <Power className="size-4" />
          </button>
        )}

        {can("ADMIN") && camera.camera_type === "LOCAL_WEBCAM" && (
          <button
            type="button" onClick={toggleMirror} title="Толин тусгал (баруун-зүүн эргүүлэх)" aria-label="Камерыг эргүүлэх"
            className="absolute bottom-16 right-4 grid size-10 place-items-center rounded-full bg-black/60 text-white backdrop-blur transition hover:bg-primary hover:text-primary-foreground"
          >
            <FlipHorizontal2 className="size-5" />
          </button>
        )}

        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 bg-gradient-to-t from-black/85 via-black/40 to-transparent px-5 pb-4 pt-12">
          <div className="min-w-0">
            <div className="truncate text-lg font-semibold text-white">{camera.name}</div>
            <div className="text-sm text-white/70">{camera.zone ? zoneLabel(camera.zone) : "Бүсгүй"}</div>
          </div>
          <CameraStatusBadge status={camera.status} />
        </div>
      </div>
    </div>
  );
}
