#!/usr/bin/env python3
"""Forge device-simulator — a laptop stand-in for the iPhone/Quest device.

Mimics the real DeviceSource over ``/v2/live`` so a human can actually talk to
Gemini Live *through the orchestrator* (the orchestrator is the intermediary
that forwards to Gemini Live and relays Live's TTS audio + transcripts back).

What it does:

  * captures the **webcam** → downscaled **JPEG** frames at a few FPS,
  * captures the **mic** → **PCM, 16 kHz mono** (Live's input rate),
  * streams BOTH over one ``/v2/live`` WebSocket using the framing the
    orchestrator parses — a 1-byte type prefix per binary frame:
        0x01 = PCM audio chunk      (payload = raw little-endian PCM 16 kHz mono)
        0x02 = JPEG video frame     (payload = the JPEG bytes)
    The bytes after the prefix are sent verbatim (no transcode), exactly as the
    server expects (see orchestrator/main.py `_parse_live_frame` +
    orchestrator/live/bridge.py `MediaKind`).
  * **plays** the TTS audio the server sends back (bare binary frames, no
    prefix) on the speaker at **24 kHz** (Live's output rate), and
  * prints transcripts (text WS frames) as they arrive.

Gemini Live takes PCM + JPEG (NOT H.264), which is exactly what this emits.

Run (after `pip install -r clients/requirements.txt`):

    python clients/live_device_sim.py --url ws://localhost:8080/v2/live

Ctrl-C to stop cleanly. See clients/README.md for the full walkthrough.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time
import uuid

# ── WS framing (must match orchestrator/main.py _parse_live_frame) ───────────
PREFIX_AUDIO = 0x01  # PCM audio, 16 kHz mono little-endian
PREFIX_VIDEO = 0x02  # JPEG frame (image/jpeg)

# ── Media settings (Live session rates; 00 §4.1) ─────────────────────────────
MIC_RATE = 16_000          # input PCM sample rate Live expects
SPEAKER_RATE = 24_000      # output PCM sample rate Live emits
AUDIO_BLOCK = 1_600        # samples per mic block (~100 ms at 16 kHz)
VIDEO_FPS = 2              # frames/sec to send (a few FPS keeps bandwidth sane)
VIDEO_MAX_EDGE = 512       # downscale the long edge before JPEG-encoding
JPEG_QUALITY = 70


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="live_device_sim",
        description="Laptop device-simulator: stream webcam JPEG + mic PCM to "
        "Forge /v2/live and play back Gemini Live TTS.",
    )
    p.add_argument(
        "--url",
        default="ws://localhost:8080/v2/live",
        help="orchestrator /v2/live WebSocket URL (default: %(default)s)",
    )
    p.add_argument(
        "--session",
        default=None,
        help="sessionId query param (default: a random one)",
    )
    p.add_argument(
        "--camera", type=int, default=0, help="OpenCV camera index (default: 0)"
    )
    p.add_argument(
        "--fps", type=float, default=VIDEO_FPS, help="video FPS (default: %(default)s)"
    )
    p.add_argument(
        "--no-video", action="store_true", help="audio only (skip the webcam)"
    )
    p.add_argument(
        "--no-audio", action="store_true", help="video only (skip the mic/speaker)"
    )
    p.add_argument(
        "--token",
        default=None,
        help="dev auth token (sent as a WS subprotocol; only if the server "
        "sets ALLOWED_DEV_TOKENS)",
    )
    return p.parse_args(argv)


def _build_url(args: argparse.Namespace) -> str:
    session = args.session or f"sim-{uuid.uuid4().hex[:12]}"
    sep = "&" if "?" in args.url else "?"
    return f"{args.url}{sep}sessionId={session}", session


# ── Capture coroutines (lazy heavy imports so --help needs no native deps) ────
async def _video_sender(ws, args, stop: asyncio.Event) -> None:
    """Grab webcam frames, downscale, JPEG-encode, send as 0x02-prefixed frames."""
    import cv2  # noqa: PLC0415 — heavy dep, imported only when video is on

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"[sim] WARN: could not open camera {args.camera}; video disabled",
              file=sys.stderr)
        return
    period = 1.0 / max(args.fps, 0.1)
    sent = 0
    try:
        while not stop.is_set():
            t0 = time.monotonic()
            ok, frame = await asyncio.to_thread(cap.read)
            if not ok:
                await asyncio.sleep(period)
                continue
            # downscale the long edge to VIDEO_MAX_EDGE, preserving aspect
            h, w = frame.shape[:2]
            scale = VIDEO_MAX_EDGE / float(max(h, w))
            if scale < 1.0:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            )
            if ok:
                await ws.send(bytes([PREFIX_VIDEO]) + buf.tobytes())
                sent += 1
                if sent % 10 == 0:
                    print(f"[sim] sent {sent} JPEG frames")
            dt = time.monotonic() - t0
            await asyncio.sleep(max(0.0, period - dt))
    finally:
        cap.release()


async def _audio_sender(ws, stop: asyncio.Event) -> None:
    """Capture mic PCM (16 kHz mono int16), send as 0x01-prefixed frames."""
    import sounddevice as sd  # noqa: PLC0415

    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()

    def _cb(indata, _frames, _time, status):  # called on a PortAudio thread
        if status:
            print(f"[sim] mic status: {status}", file=sys.stderr)
        # indata is int16 mono; ship raw little-endian PCM bytes
        loop.call_soon_threadsafe(_enqueue, bytes(indata))

    def _enqueue(chunk: bytes) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(chunk)

    with sd.InputStream(
        samplerate=MIC_RATE, channels=1, dtype="int16",
        blocksize=AUDIO_BLOCK, callback=_cb,
    ):
        print(f"[sim] mic open @ {MIC_RATE} Hz mono")
        while not stop.is_set():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            await ws.send(bytes([PREFIX_AUDIO]) + chunk)


async def _receiver(ws, args, stop: asyncio.Event) -> None:
    """Receive server frames: bare binary = TTS PCM (play it); text = transcript."""
    player = None
    if not args.no_audio:
        import sounddevice as sd  # noqa: PLC0415

        player = sd.RawOutputStream(
            samplerate=SPEAKER_RATE, channels=1, dtype="int16"
        )
        player.start()
        print(f"[sim] speaker open @ {SPEAKER_RATE} Hz mono")
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                # TTS audio from Gemini Live, relayed by the orchestrator
                if player is not None:
                    player.write(bytes(msg))
            else:
                # text frame — transcript / control
                print(f"[transcript] {msg}")
    finally:
        stop.set()
        if player is not None:
            with contextlib.suppress(Exception):
                player.stop()
                player.close()


async def run(args: argparse.Namespace) -> int:
    import websockets  # noqa: PLC0415

    url, session = _build_url(args)
    subprotocols = [args.token] if args.token else None
    print(f"[sim] connecting to {url}")
    print(f"[sim] sessionId={session}  video={'off' if args.no_video else 'on'}  "
          f"audio={'off' if args.no_audio else 'on'}")

    stop = asyncio.Event()
    # Ctrl-C → set the stop event so tasks unwind cleanly.
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):  # add_signal_handler not on Win
        loop.add_signal_handler(signal.SIGINT, stop.set)
        loop.add_signal_handler(signal.SIGTERM, stop.set)

    try:
        async with websockets.connect(url, subprotocols=subprotocols) as ws:
            print("[sim] connected. Talk + show the camera something. Ctrl-C to quit.")
            tasks: list[asyncio.Task] = [
                asyncio.create_task(_receiver(ws, args, stop)),
            ]
            if not args.no_video:
                tasks.append(asyncio.create_task(_video_sender(ws, args, stop)))
            if not args.no_audio:
                tasks.append(asyncio.create_task(_audio_sender(ws, stop)))

            await stop.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except (ConnectionRefusedError, OSError) as e:
        print(f"[sim] connection failed: {e}", file=sys.stderr)
        return 1
    print("[sim] bye.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n[sim] interrupted; bye.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
