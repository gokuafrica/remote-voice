# Remote Voice: Phone-to-PC Voice Transcription Pipeline

## Problem

Mobile speech-to-text (Google, Apple) is unreliable — poor accent handling, gets stuck mid-transcription, misses context. A local NVIDIA Parakeet V2 model running on an RTX 4070 Ti is vastly superior (~1200x real-time, accent-robust) but only accessible from the PC.

## Goal

Use an Android phone to record voice anywhere, send audio to the PC for transcription + LLM post-processing, and receive cleaned text back on the phone — usable as keyboard input in any app (WhatsApp, Telegram, email, Termius, etc.).

## Architecture

```
[Android Phone]                         [Windows 11 PC - RTX 4070 Ti]
     |                                           |
  Record audio                                   |
     |  ---- audio bytes over Tailscale ---->     |
     |                                    Parakeet V2 (local STT)
     |                                           |
     |                                    Ollama qwen2.5:3b (cleanup)
     |                                           |
     |  <---- cleaned text over Tailscale ---    |
     |
  Insert into keyboard / clipboard
  (works in any app)
```

## Existing Infrastructure

- **Tailscale** mesh VPN already running on both devices (PC IP: `100.84.204.105`)
- **Handy STT** on PC uses Parakeet V2 for local transcription (works, tested, fast)
- **Ollama** running on PC with `qwen2.5:3b` model at `http://localhost:11434/v1`
- **LLM post-processing prompt** already tuned and tested (see `handy-prompt.txt` on Desktop), handles: number conversion, self-corrections, filler removal, spoken punctuation, capitalization

## What Needs to Be Built

### 1. PC Server Component

A lightweight HTTP/WebSocket server running on the PC that:
- Accepts audio uploads (WAV/PCM) from the phone
- Runs Parakeet V2 transcription on the audio (GPU-accelerated)
- Sends raw transcript to Ollama for post-processing
- Returns cleaned text to the phone

Key decisions:
- **How to invoke Parakeet**: Handy STT is a desktop app with no API. Options:
  - (a) Use Handy's internals if it exposes any CLI/API (unlikely, it's a Tauri app)
  - (b) Run Parakeet V2 directly via ONNX Runtime or the NVIDIA NeMo toolkit
  - (c) Use a Python wrapper with `nemo_toolkit` or `faster-whisper` with Parakeet model
  - (d) Use Whisper.cpp or similar with Parakeet ONNX weights
- **Ollama integration**: Straightforward — POST to `http://localhost:11434/v1/chat/completions` with the post-processing prompt
- **Framework**: A simple FastAPI (Python) or Actix (Rust) server would work
- **Port**: Pick something like 8787, add Windows Firewall rule for Tailscale

### 2. Android App Component

A lightweight Android app (or Tasker/Automate workflow) that:
- Records audio via push-to-talk button or floating widget
- Sends audio to PC server over Tailscale
- Receives cleaned text response
- Copies text to clipboard and/or injects it as keyboard input via Android accessibility service or Input Method Editor (IME)

Options for text insertion:
- (a) **Custom IME (keyboard)**: App acts as a keyboard with a mic button — most seamless, works in all apps
- (b) **Accessibility service**: Pastes into focused text field — less seamless
- (c) **Clipboard + notification**: Simplest — copies to clipboard, user pastes manually

### 3. Post-Processing Prompt

Already built and tested. Located at: `C:\Users\Anwesh Mohapatra\OneDrive\Desktop\handy-prompt.txt`

```
You clean transcripts. Apply ALL rules below to the transcript.

RULES:
1. Number words become digits (one→1, twenty five→25, etc.)
2. Self-corrections: "sorry", "I meant", "actually" = delete wrong part, keep fix
3. Delete filler words: um, uh, "you know", filler "like"
4. Spoken punctuation: "comma"→, "period"→. "question mark"→? "colon"→:
5. Capitalize first letter of each sentence, add period if missing
6. Do NOT translate

Output ONLY the cleaned text.
```

## Tech Stack Recommendation

| Component | Recommendation | Why |
|-----------|---------------|-----|
| PC server | Python + FastAPI | Easiest Parakeet/NeMo integration, async support |
| STT engine | NVIDIA Parakeet V2 via NeMo or ONNX | Already proven on this GPU, ~1200x realtime |
| LLM cleanup | Ollama qwen2.5:3b | Already installed and tested |
| Android app | Kotlin + Jetpack Compose | Native, can implement custom IME |
| Network | Tailscale (already set up) | Encrypted, works from anywhere |

## Non-Functional Requirements

- Latency: Total round-trip (record → transcribe → cleanup → text on phone) should be under 3 seconds for a 10-second utterance
- The PC server should auto-start with Windows (or run as a service)
- Should work from anywhere — different WiFi, mobile data, different country — as long as Tailscale is connected
- Audio should NOT be stored on the PC after processing (privacy)

## Out of Scope (for now)

- Streaming/real-time transcription (batch is fine)
- iOS support
- Multi-user support
- Wake word detection
