"""
Remote Voice Server
Accepts audio from phone, transcribes with Parakeet V2, cleans with Ollama,
returns text. Compatible with whisper-to-input Android app.
"""

import glob
import json
import os
import subprocess
import tempfile
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Add CUDA DLL directories to PATH before importing onnxruntime
_nvidia_base = os.path.join(
    os.path.expanduser("~"), "AppData", "Roaming", "Python",
    f"Python{__import__('sys').version_info.major}{__import__('sys').version_info.minor}",
    "site-packages", "nvidia",
)
_cuda_paths = [d for d in glob.glob(os.path.join(_nvidia_base, "*", "bin")) if os.path.isdir(d)]
if _cuda_paths:
    os.environ["PATH"] = os.pathsep.join(_cuda_paths) + os.pathsep + os.environ.get("PATH", "")

import httpx
import onnx_asr
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {}


cfg = load_config()
OLLAMA_URL = cfg.get("ollama_url", "http://localhost:11434")
OLLAMA_MODEL = cfg.get("ollama_model", "qwen2.5:3b")
SERVER_PORT = int(cfg.get("server_port", 8787))
VOICE_MODEL = cfg.get("voice_model", "nemo-parakeet-tdt-0.6b-v2")
CLEANUP_PROMPT = cfg.get("cleanup_prompt", "Clean this transcript:\n")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remote-voice")

model = None


@asynccontextmanager
async def lifespan(app):
    global model
    log.info(f"Loading voice model: {VOICE_MODEL}")
    model = onnx_asr.load_model(VOICE_MODEL)
    log.info("Model loaded and ready.")
    yield


app = FastAPI(title="Remote Voice Server", lifespan=lifespan)


def convert_to_wav(input_path: str) -> str:
    """Convert any audio format to 16kHz mono WAV using ffmpeg."""
    wav_path = input_path + ".wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", wav_path],
        capture_output=True,
        check=True,
    )
    return wav_path


def transcribe_audio(audio_bytes: bytes, filename: str) -> str:
    """Save audio, convert to WAV if needed, run Parakeet V2."""
    suffix = os.path.splitext(filename)[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    wav_path = None
    try:
        tmp.write(audio_bytes)
        tmp.close()

        if suffix.lower() != ".wav":
            wav_path = convert_to_wav(tmp.name)
            recognize_path = wav_path
        else:
            recognize_path = tmp.name

        return str(model.recognize(recognize_path))
    finally:
        os.unlink(tmp.name)
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)


async def cleanup_with_ollama(raw_text: str) -> str:
    """Send raw transcript to Ollama for cleanup."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "user", "content": CLEANUP_PROMPT + "\n" + raw_text},
                    ],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
    except Exception as e:
        log.warning(f"Ollama cleanup failed ({e}), returning raw transcript")
        return raw_text


async def process_audio(audio_bytes: bytes, filename: str) -> str:
    """Full pipeline: transcribe + clean."""
    t0 = time.perf_counter()

    raw_text = transcribe_audio(audio_bytes, filename)
    t1 = time.perf_counter()
    log.info(f"Transcription: {t1 - t0:.2f}s | raw: {raw_text[:100]}")

    if not raw_text.strip():
        log.info("Empty transcript — skipping cleanup")
        return ""

    cleaned_text = await cleanup_with_ollama(raw_text)
    t2 = time.perf_counter()
    log.info(f"Cleanup: {t2 - t1:.2f}s | cleaned: {cleaned_text[:100]}")
    log.info(f"Total: {t2 - t0:.2f}s")

    return cleaned_text


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/asr")
async def asr_endpoint(
    audio_file: UploadFile = File(...),
    encode: str = Query(default="true"),
    task: str = Query(default="transcribe"),
    language: str = Query(default="en"),
    word_timestamps: str = Query(default="false"),
    output: str = Query(default="txt"),
):
    """Whisper ASR Webservice-compatible endpoint (used by whisper-to-input app)."""
    audio_bytes = await audio_file.read()
    cleaned_text = await process_audio(audio_bytes, audio_file.filename or "audio.m4a")
    return PlainTextResponse(cleaned_text)


@app.post("/v1/audio/transcriptions")
async def transcribe_openai(
    file: UploadFile = File(...),
    model_name: str = Form(default="parakeet", alias="model"),
    language: str = Form(default="en"),
    response_format: str = Form(default="json"),
    encode: str = Query(default=""),
    task: str = Query(default=""),
    word_timestamps: str = Query(default=""),
    output: str = Query(default=""),
):
    """OpenAI Whisper-compatible endpoint."""
    audio_bytes = await file.read()
    cleaned_text = await process_audio(audio_bytes, file.filename or "audio.m4a")

    if response_format == "text":
        return PlainTextResponse(cleaned_text)
    return JSONResponse(content={"text": cleaned_text})


@app.get("/health")
async def health():
    return {"status": "ok", "model": VOICE_MODEL, "llm": OLLAMA_MODEL}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
