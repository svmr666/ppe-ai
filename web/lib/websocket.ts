import { api } from "@/lib/api";
import type { RealtimeEvent } from "@/types";

export const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws";

export interface RealtimeClient {
  close: () => void;
}

/** Dashboard өөр домэйн дээр байхад cookie handshake-д очдоггүй тул 60 секундын ticket-ийг query-д залгана. */
async function socketUrl(): Promise<string> {
  try {
    const { ticket, ws_url } = await api.wsTicket();
    const base = ws_url ?? WS_URL; // quick tunnel үед backend өөрийн одоогийн хаягийг хэлнэ
    return `${base}${base.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`;
  } catch {
    return WS_URL; // ticket авч чадаагүй бол cookie-д найдана (local dev)
  }
}

/**
 * Auto-reconnect (exponential backoff) WebSocket. Нэвтрэлт нь httpOnly cookie эсвэл reconnect бүрд шинэ ticket.
 */
export function connectRealtime(
  onEvent: (event: RealtimeEvent) => void,
  onStatus: (connected: boolean) => void,
): RealtimeClient {
  let socket: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined;

  const open = async () => {
    const url = await socketUrl();
    if (closed) return;
    socket = new WebSocket(url);
    const current = socket;
    socket.onopen = () => {
      if (closed) return current.close();
      attempt = 0;
      onStatus(true);
      heartbeat = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), 25_000);
    };
    socket.onmessage = (message) => {
      if (message.data === "pong") return;
      try {
        onEvent(JSON.parse(message.data) as RealtimeEvent);
      } catch {
        /* ignore malformed */
      }
    };
    socket.onclose = (event) => {
      clearInterval(heartbeat);
      if (closed) return; // хуучин (цэвэрлэгдсэн) холболтын дохио шинэ холболтын төлөвийг дарж болохгүй
      onStatus(false);
      console.warn(`[realtime] WebSocket салсан (code=${event.code}) — ${Math.min(1000 * 2 ** attempt, 15_000) / 1000} сек дараа дахин оролдоно: ${WS_URL}`);
      const delay = Math.min(1000 * 2 ** attempt++, 15_000);
      timer = setTimeout(open, delay);
    };
    socket.onerror = () => current.close();
  };

  void open();
  return {
    close: () => {
      closed = true;
      clearTimeout(timer);
      clearInterval(heartbeat);
      socket?.close();
    },
  };
}
