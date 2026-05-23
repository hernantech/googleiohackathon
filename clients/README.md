# Forge device-simulator (`live_device_sim.py`)

A laptop stand-in for the iPhone/Quest device so a human can talk to **Gemini
Live through the orchestrator**. It captures the **webcam → JPEG frames** and
the **mic → PCM 16 kHz mono**, streams both over the orchestrator's `/v2/live`
WebSocket, and **plays the TTS audio (24 kHz)** that Gemini Live sends back
(relayed by the orchestrator). Transcripts print to stdout.

The orchestrator is the intermediary: device → `/v2/live` → orchestrator →
Gemini Live, and Live's audio/transcripts → orchestrator → back to the device.
Gemini Live takes **PCM + JPEG** (not H.264), which is what this client emits.

## Wire framing (client → orchestrator)

Each binary WS frame is one media chunk with a **1-byte type prefix**; the rest
is the raw payload, relayed verbatim (no transcode):

| prefix | payload |
| ------ | ------- |
| `0x01` | PCM audio, 16 kHz mono, little-endian int16 |
| `0x02` | a JPEG frame (`image/jpeg`) |

TTS audio comes **back** as bare binary frames (no prefix), 24 kHz PCM int16.
Transcripts come back as text frames. This matches the orchestrator's
`_parse_live_frame` (`orchestrator/main.py`) and `MediaKind`
(`orchestrator/live/bridge.py`).

## Setup

These are **client** deps (webcam + audio native stacks) and are intentionally
NOT part of the orchestrator package. Install them into a venv:

```bash
python3.12 -m venv .venv            # or reuse the orchestrator venv
.venv/bin/pip install -r clients/requirements.txt
```

macOS: the first run will prompt for **camera** and **microphone**
permissions — grant both (System Settings → Privacy & Security). On Linux you
need PortAudio (`libportaudio2`) for `sounddevice`.

## Run

Start the orchestrator first (keyed, so the real Live session opens):

```bash
GEMINI_API_KEY=... .venv/bin/python -m orchestrator.main
```

Then, in another terminal, run the simulator and start talking / showing the
camera something:

```bash
python clients/live_device_sim.py --url ws://localhost:8080/v2/live
```

Press **Ctrl-C** to stop cleanly.

### Options

| flag | default | meaning |
| ---- | ------- | ------- |
| `--url` | `ws://localhost:8080/v2/live` | orchestrator `/v2/live` WS URL |
| `--session` | random `sim-<hex>` | `sessionId` query param |
| `--camera` | `0` | OpenCV camera index |
| `--fps` | `2` | webcam frames/sec sent to Live |
| `--no-video` | off | audio only (skip the webcam) |
| `--no-audio` | off | video only (skip mic + speaker) |
| `--token` | — | dev auth token (only if the server sets `ALLOWED_DEV_TOKENS`) |

Examples:

```bash
# point at a remote host
python clients/live_device_sim.py --url ws://192.168.1.50:8080/v2/live

# audio-only smoke (no camera permission needed)
python clients/live_device_sim.py --no-video
```

## Notes / limits

- If the orchestrator runs **without** `GEMINI_API_KEY`, `/v2/live` accepts and
  **drains** your media but sends nothing back (no TTS, no transcripts) — that's
  the zero-config stub mode. Key it to hear Live respond.
- Frame rate is deliberately low (a few FPS) — Live ingests image frames, not a
  video stream; a couple of FPS is plenty for "always-on eyes" while keeping
  bandwidth modest.
