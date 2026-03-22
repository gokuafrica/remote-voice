# Remote Voice

Local voice transcription pipeline that runs on your own hardware. Record voice on your phone or PC, get cleaned text back — powered by NVIDIA Parakeet V2 and Ollama.

Think WhisperFlow / Superwhisper, but fully local, GPU-accelerated, and with LLM post-processing to clean up filler words, self-corrections, and spoken punctuation.

## How It Works

```
Phone or PC Mic
      |
      v
  FastAPI Server (port 8787)
      |
      +-- ffmpeg (convert to WAV if needed)
      |
      +-- Parakeet V2 via ONNX Runtime (GPU transcription, ~0.5s)
      |
      +-- Ollama qwen2.5 (cleanup: fillers, numbers, punctuation, ~1s)
      |
      v
  Cleaned text returned
      |
      +-- Phone: inserted into any app via whisper-to-input keyboard
      +-- PC: pasted into focused window via tray app hotkey
```

## Components

| Component | File | What it does |
|-----------|------|-------------|
| **Server** | `server.py` | Accepts audio, transcribes + cleans, returns text |
| **Server GUI** | `gui.py` | Configure models, prompt, start/stop server, view logs |
| **Tray App** | `tray.py` | System tray hotkey — record mic, transcribe, paste into any app |
| **Phone Client** | [whisper-to-input](https://github.com/j3soon/whisper-to-input) | Android keyboard that sends audio to the server |

## Setup

### Prerequisites

- Windows 11 with NVIDIA GPU (tested on RTX 4070 Ti)
- Python 3.10+
- [Ollama](https://ollama.ai) running locally with a model (e.g. `ollama pull qwen2.5:3b`)
- [Tailscale](https://tailscale.com) (for phone access from anywhere)

### Install

```bash
pip install -r requirements.txt
pip install onnxruntime-gpu
pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-cusparse-cu12 nvidia-cusolver-cu12 nvidia-curand-cu12 nvidia-nvjitlink-cu12
```

FFmpeg is also required for audio format conversion:
```bash
winget install Gyan.FFmpeg
```

The Parakeet V2 ONNX model (~2 GB) downloads automatically from HuggingFace on first server start.

### One-Time Admin Setup

Right-click `setup.bat` > "Run as administrator". This creates:
- A Windows Firewall rule for port 8787
- A scheduled task to auto-start the server at login

### Phone Setup

1. Install [whisper-to-input APK](https://github.com/j3soon/whisper-to-input/releases)
2. Backend: **Whisper ASR Webservice**
3. Endpoint: `http://<your-tailscale-ip>:8787/asr`
4. Language: `en`
5. Enable the keyboard in Android Settings > Languages & Input

## Usage

### Start the Server

Double-click `Remote Voice.bat` to open the GUI. Click **Start Server**.

Or run directly:
```bash
python server.py
```

### From Your Phone

Switch to the Whisper to Input keyboard in any app, tap the mic button, speak. Text appears in the text field.

### From Your PC

Launch `Remote Voice Tray.bat`. A mic icon appears in the system tray. Use the configured hotkey (default: `Left Ctrl + '`) to record. Right-click the tray icon to select microphone and recording mode (push-to-talk or toggle).

## Configuration

All settings are managed through the GUI (`Remote Voice.bat`), or by editing the JSON files directly.

### config.json (Server)

| Setting | Description | Default |
|---------|-------------|---------|
| `server_port` | Server listening port | `8787` |
| `ollama_url` | Ollama API URL | `http://localhost:11434` |
| `ollama_model` | LLM for transcript cleanup | `qwen2.5:3b` |
| `voice_model` | Speech-to-text model | `nemo-parakeet-tdt-0.6b-v2` |
| `cleanup_prompt` | Instructions sent to the LLM | See config.json |

### tray_config.json (Tray App)

| Setting | Description | Default |
|---------|-------------|---------|
| `hotkey` | Global hotkey for recording | `left ctrl+'` |
| `mic_device` | Microphone name (or null for system default) | `null` |
| `sample_rate` | Audio sample rate (Hz) | `16000` |
| `mode` | `push_to_talk` (hold) or `toggle` (press twice) | `push_to_talk` |

## API Endpoints

The server exposes two transcription endpoints:

**Whisper ASR Webservice format** (used by whisper-to-input):
```
POST /asr?encode=true&task=transcribe&language=en&output=txt
Content-Type: multipart/form-data
Body: audio_file=<audio bytes>
Response: plain text
```

**OpenAI Whisper format**:
```
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
Body: file=<audio bytes>, model=parakeet, response_format=json
Response: {"text": "..."}
```

**Health check**:
```
GET /health
Response: {"status": "ok", "model": "...", "llm": "..."}
```

## Supported Voice Models

Any model supported by [onnx-asr](https://github.com/istupakov/onnx-asr):

| Model | Language | Notes |
|-------|----------|-------|
| `nemo-parakeet-tdt-0.6b-v2` | English | Default. Best accuracy for English. |
| `nemo-parakeet-tdt-0.6b-v3` | 25 European languages | Multilingual variant |
| `nemo-parakeet-ctc-0.6b` | English | CTC variant |
| `nemo-canary-1b-v2` | Multilingual | Larger, more accurate |
| `whisper` | Multilingual | OpenAI Whisper via onnx-community |
| `whisper-ort` | Multilingual | Whisper via ONNX Runtime |

Models download automatically from HuggingFace on first use.

## Performance

On RTX 4070 Ti with Parakeet V2 + qwen2.5:3b:

- Transcription: ~0.3-0.5s for 10s of audio
- LLM cleanup: ~0.6-1.0s
- Total round-trip: ~1-2s

## Privacy

- All processing is local — audio never leaves your network
- Temp audio files are deleted immediately after processing
- Tailscale encrypts all phone-to-PC traffic
- No cloud APIs, no telemetry
