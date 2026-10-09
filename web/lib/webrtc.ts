/**
 * WHEP (WebRTC-HTTP Egress Protocol) клиент — MediaMTX-ээс камерын видеог авна.
 * Нэвтрэлт: backend-ийн өгсөн богино хугацаатай Bearer токен (тухайн камерын path-д л хүчинтэй).
 */
export async function startWhep(
  whepUrl: string,
  token: string,
  onStream: (stream: MediaStream) => void,
  onClosed: () => void,
): Promise<RTCPeerConnection> {
  const pc = new RTCPeerConnection();
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (event) => onStream(event.streams[0]);
  pc.onconnectionstatechange = () => {
    if (["failed", "closed", "disconnected"].includes(pc.connectionState)) onClosed();
  };

  try {
    await pc.setLocalDescription(await pc.createOffer());
    // ICE candidate-үүдийг цуглуулж дуустал (хамгийн ихдээ 1.5 сек) хүлээнэ
    await new Promise<void>((resolve) => {
      if (pc.iceGatheringState === "complete") return resolve();
      const done = () => pc.iceGatheringState === "complete" && (pc.removeEventListener("icegatheringstatechange", done), resolve());
      pc.addEventListener("icegatheringstatechange", done);
      setTimeout(resolve, 1500);
    });

    const response = await fetch(whepUrl, {
      method: "POST",
      headers: { "Content-Type": "application/sdp", Authorization: `Bearer ${token}` },
      body: pc.localDescription!.sdp,
    });
    if (!response.ok) throw new Error(`WHEP ${response.status}`);
    await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
    return pc;
  } catch (err) {
    pc.close();
    throw err;
  }
}
