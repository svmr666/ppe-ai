import { useSyncExternalStore } from "react";

/** Зөрчлийн дуут дохио: Web Audio-оор үүсгэнэ (файл татахгүй). Унтраалтыг localStorage-д хадгална. */
const KEY = "alert-sound";
const MIN_GAP_MS = 3000; // олон зөрчил дараалан ирэхэд дуу давтагдахгүй
const listeners = new Set<() => void>();
let ctx: AudioContext | null = null;
let lastPlayed = 0;

export function soundEnabled(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setSoundEnabled(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* хадгалах боломжгүй бол энэ сессэд л үйлчилнэ */
  }
  listeners.forEach((l) => l());
}

export function useSoundEnabled(): boolean {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; },
    soundEnabled,
    () => true,
  );
}

function context(): AudioContext | null {
  if (!ctx) {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
  }
  return ctx;
}

/** Browser хэрэглэгчийн үйлдлийн дараа л дуу гаргахыг зөвшөөрдөг: эхний дарралт/товчлуурын дараа идэвхжүүлнэ. */
export function unlockAudio(): void {
  const c = context();
  if (c && c.state === "suspended") void c.resume();
}

/** Хоёр өнгөт дохио. force=true бол унтраалт/давтамжийг үл хамаарна (шалгах товч). */
export function playAlert(force = false): void {
  if (!force && (!soundEnabled() || Date.now() - lastPlayed < MIN_GAP_MS)) return;
  const c = context();
  if (!c) return;
  if (c.state === "suspended") void c.resume();
  if (c.state !== "running" && !force) return; // хараахан зөвшөөрөгдөөгүй
  lastPlayed = Date.now();
  const start = c.currentTime + 0.02;
  [880, 660, 880, 660].forEach((freq, i) => {
    const osc = c.createOscillator();
    const gain = c.createGain();
    const t = start + i * 0.2;
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    osc.connect(gain).connect(c.destination);
    osc.start(t);
    osc.stop(t + 0.2);
  });
}
