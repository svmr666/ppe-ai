import type { NextRequest } from "next/server";

// BACKEND_ORIGIN (тогтмол домэйн) тохируулсан бол next.config.ts-ийн rewrite энэ route-аас өмнө ажиллана.
// Үгүй бол worker.py-ийн Supabase-д нийтэлсэн endpoints.json-оос тухайн үед асаалттай Mac-ийн хаягийг уншина.
const REGISTRY_URL = process.env.BACKEND_REGISTRY_URL;
const REGISTRY_TTL_MS = 10_000;

const HOP_BY_HOP = ["connection", "keep-alive", "transfer-encoding", "upgrade", "host", "content-length"];

let cached: { origin: string; at: number } | null = null;

async function backendOrigin(fresh = false): Promise<string | null> {
  const fixed = process.env.BACKEND_ORIGIN?.replace(/\/$/, "");
  if (fixed) return fixed;
  if (!REGISTRY_URL) return null;
  if (!fresh && cached && Date.now() - cached.at < REGISTRY_TTL_MS) return cached.origin;
  try {
    const response = await fetch(`${REGISTRY_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return null;
    const { api_url } = (await response.json()) as { api_url?: string };
    if (!api_url?.startsWith("https://")) return null;
    cached = { origin: api_url.replace(/\/$/, ""), at: Date.now() };
    return cached.origin;
  } catch {
    return null;
  }
}

const offline = () =>
  Response.json({ detail: "Backend (Mac) асаагүй байна. start-worker.command-г ажиллуулна уу." }, { status: 503 });

async function forward(request: NextRequest, origin: string, body: ArrayBuffer | undefined): Promise<Response> {
  const headers = new Headers(request.headers);
  HOP_BY_HOP.forEach((name) => headers.delete(name));
  return fetch(`${origin}${request.nextUrl.pathname}${request.nextUrl.search}`, {
    method: request.method,
    headers,
    body,
    redirect: "manual",
    cache: "no-store",
  });
}

async function proxy(request: NextRequest): Promise<Response> {
  const origin = await backendOrigin();
  if (!origin) return offline();
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();

  // Cloudflare tunnel унтарсан үед 530 буцаана
  const attempt = (target: string) => forward(request, target, body).then((r) => (r.status === 530 ? null : r), () => null);
  let upstream = await attempt(origin);
  if (!upstream) {
    // Mac солигдсон / дахин асаагдсан байж болно — шинэ хаягаар нэг удаа дахин оролдоно
    const next = await backendOrigin(true);
    upstream = next && next !== origin ? await attempt(next) : null;
    if (!upstream) return offline();
  }

  const headers = new Headers(upstream.headers);
  ["content-encoding", "content-length", "transfer-encoding", "connection"].forEach((name) => headers.delete(name));
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as HEAD };
