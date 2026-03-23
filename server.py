"""
Remote Voice Server
Accepts audio from phone, transcribes with Parakeet V2, cleans with Ollama,
returns text. Compatible with whisper-to-input Android app.
"""

import glob
import json
import os
import re
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
from word2number import w2n

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
    log.info(f"Voice model loaded. LLM model (from config): {OLLAMA_MODEL}")
    log.info("Server ready.")
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


async def cleanup_with_ollama(raw_text: str, instruction: str = "") -> str:
    """Send raw transcript to Ollama for cleanup."""
    log.info(f"LLM cleanup using model: {OLLAMA_MODEL}")
    prompt = CLEANUP_PROMPT
    if instruction:
        prompt += f"\n\nAdditional instruction: {instruction}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt + "\n" + raw_text},
                    ],
                    "stream": False,
                    "keep_alive": -1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            actual_model = data.get("model", "unknown")
            log.info(f"Ollama responded with model: {actual_model}")
            return data["message"]["content"].strip()
    except Exception as e:
        log.warning(f"Ollama cleanup failed ({e}), returning raw transcript")
        return raw_text


# ---------------------------------------------------------------------------
# Regex-based lightweight cleanup
# ---------------------------------------------------------------------------

LLM_TRIGGER = re.compile(
    r'[,.]?\s*\bdeep[\s-]+clean\b'          # "deep clean" command
    r'(?:\s+with\s+(.+?))?'                  # optional "with <instruction>"
    r'[,.]?\s*$',                             # trailing punct + end
    re.IGNORECASE,
)

START_OVER = re.compile(r'^.*[,.]?\s*\bstart[\s-]+over\b[,.]?\s*', re.IGNORECASE)

SCRATCH_THAT = re.compile(r'[^.!?]*[,.]?\s*\bscratch[\s-]+that\b[,.]?\s*', re.IGNORECASE)

FILLER_PATTERN = re.compile(r'\b(um|uh|you[\s-]+know)\b[,.]?\s*', re.IGNORECASE)

NEW_PARAGRAPH = re.compile(r'[,.]?\s*\bnew[\s-]+paragraph\b[,.]?\s*', re.IGNORECASE)
NEW_LINE = re.compile(r'[,.]?\s*\bnew[\s-]+line\b[,.]?\s*', re.IGNORECASE)

SPOKEN_PUNCTUATION = [
    # Multi-word: consume leading space/punct only (preserve trailing space for natural flow)
    (re.compile(r'[,.]?\s*\bquestion[\s-]+mark\b[,.]?', re.IGNORECASE), '? '),
    (re.compile(r'[,.]?\s*\bexclamation[\s-]+point\b[,.]?', re.IGNORECASE), '! '),
    (re.compile(r'[,.]?\s*\bopen[\s-]+parenthesis\b[,.]?\s*', re.IGNORECASE), ' ('),
    (re.compile(r'[,.]?\s*\bclose[\s-]+parenthesis\b[,.]?\s*', re.IGNORECASE), ') '),
    (re.compile(r'\s*\bdouble[\s-]+quote\b\s*', re.IGNORECASE), '"'),
    (re.compile(r'\s*\bquotation[\s-]+mark\b\s*', re.IGNORECASE), '"'),
    (re.compile(r'\s*\bsingle[\s-]+quote\b\s*', re.IGNORECASE), "'"),
    (re.compile(r'[,.]?\s*\bpercent[\s-]+sign\b[,.]?', re.IGNORECASE), '% '),
    # Single-word: consume leading space, replacement includes trailing space where needed
    (re.compile(r'\s*\bcomma\b', re.IGNORECASE), ', '),
    (re.compile(r'\s*\bperiod\b[.]?', re.IGNORECASE), '. '),
    (re.compile(r'\s*\bcolon\b', re.IGNORECASE), ': '),
    (re.compile(r'\s*\bsemicolon\b', re.IGNORECASE), '; '),
    (re.compile(r'\s*\bellipsis\b', re.IGNORECASE), '... '),
    # Joining punctuation: consume both surrounding spaces
    (re.compile(r'\s*\bhyphen\b\s*', re.IGNORECASE), '-'),
    (re.compile(r'\s*\bdash\b\s*', re.IGNORECASE), '-'),
    (re.compile(r'\s*\bslash\b\s*', re.IGNORECASE), '/'),
    (re.compile(r'\s*\bapostrophe\b\s*', re.IGNORECASE), "'"),
]

NUMBER_WORDS = re.compile(
    r'\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|'
    r'eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|'
    r'eighty|ninety|hundred|thousand|million|billion)\b',
    re.IGNORECASE
)

BULLET_PATTERN = re.compile(r'[,.]?\s*\bbullet[\s-]+(\d+)\b[,.]?\s*', re.IGNORECASE)
END_LIST = re.compile(r'[,.]?\s*\bend[\s-]+list\b[,.]?\s*', re.IGNORECASE)


def check_llm_trigger(text: str) -> tuple[bool, str, str]:
    """Check if text ends with 'deep clean' command.

    Returns (trigger, cleaned_text, instruction).
    'instruction' is empty for plain 'deep clean', or the user's
    custom instruction for 'deep clean with <instruction>'.
    """
    m = LLM_TRIGGER.search(text)
    if m:
        cleaned = text[:m.start()].strip()
        instruction = (m.group(1) or '').strip().rstrip('.,!?')
        return True, cleaned, instruction
    return False, text, ""


def convert_number_words(text: str) -> str:
    """Find sequences of number words in text and convert to digits."""
    words = text.split()
    result = []
    i = 0

    while i < len(words):
        if NUMBER_WORDS.match(words[i].rstrip('.,!?')):
            # Collect consecutive number words (including "and" for "one hundred and twenty")
            span = []
            j = i
            while j < len(words) and (
                NUMBER_WORDS.match(words[j].rstrip('.,!?'))
                or words[j].lower() == 'and'
            ):
                span.append(words[j].rstrip('.,!?'))
                j += 1

            # Preserve trailing punctuation from last word in span
            trailing = ''
            if span and words[j-1] and words[j-1][-1] in '.,!?':
                trailing = words[j-1][-1]

            # Never convert single "a" or "I" to numbers
            span_text = ' '.join(span)
            if span_text.lower() in ('a', 'i'):
                result.append(words[i])
                i += 1
                continue

            try:
                number = w2n.word_to_num(span_text)
                result.append(str(number) + trailing)
                i = j
            except ValueError:
                result.append(words[i])
                i += 1
        else:
            result.append(words[i])
            i += 1

    return ' '.join(result)


def format_numbered_list(text: str) -> str:
    """Convert 'bullet N item ... end list' to formatted numbered list.

    BOTH 'bullet N' AND 'end list' must be present to activate.
    If 'bullet' appears without 'end list', it's treated as natural
    speech (e.g., 'a bullet hit the wall') and left unchanged.
    """
    first_bullet = BULLET_PATTERN.search(text)
    end_list = END_LIST.search(text)

    # BOTH markers required — if either is missing, return unchanged
    if not first_bullet or not end_list:
        return text

    # Ensure end_list comes AFTER first_bullet
    if end_list.start() < first_bullet.start():
        return text

    before_end = first_bullet.start()
    before = text[:before_end].strip()
    # If the bullet pattern consumed a period that belongs to the preceding sentence, restore it
    if before_end < len(text) and text[before_end] == '.' and before and before[-1] not in '.!?':
        before += '.'
    list_region = text[first_bullet.start():end_list.start()]
    after = text[end_list.end():].strip()

    # Split list region by "bullet N" markers
    parts = BULLET_PATTERN.split(list_region)
    # parts alternates: [pre-text, number, item, number, item, ...]
    items = []
    for idx in range(1, len(parts), 2):
        num = parts[idx]
        item_text = parts[idx + 1].strip().rstrip('.') if idx + 1 < len(parts) else ''
        if item_text:
            # Capitalize first letter of each item
            item_text = item_text[0].upper() + item_text[1:] if item_text else ''
            items.append(f"{num}. {item_text}")

    list_output = '\n'.join(items)

    # Combine parts
    result_parts = []
    if before:
        result_parts.append(before)
    result_parts.append(list_output)
    if after:
        result_parts.append(after)

    return '\n'.join(result_parts)


def lightweight_cleanup(text: str) -> str:
    """Fast regex-based transcript cleanup. Handles all deterministic tasks."""
    if not text or not text.strip():
        return ""

    # 3b. Handle "start over"
    text = START_OVER.sub('', text).strip()
    if not text:
        return ""

    # 3c. Handle "scratch that"
    text = SCRATCH_THAT.sub('', text).strip()
    if not text:
        return ""

    # 3d. Remove filler words
    text = FILLER_PATTERN.sub('', text)

    # 3e. Convert spoken punctuation to symbols
    for pattern, symbol in SPOKEN_PUNCTUATION:
        text = pattern.sub(symbol, text)

    # Remove Parakeet's period before manually dictated punctuation
    text = re.sub(r'\.\s*([,;:?!\-/\'\"()%])', r'\1', text)

    # Fix spacing around quotes: add space before opening quote (between two letters)
    text = re.sub(r'([a-zA-Z])"([a-zA-Z])', r'\1 "\2', text)

    # 3f. Handle "new paragraph" and "new line" (order matters)
    def _new_paragraph_repl(m):
        if m.start() == 0:
            return '\n\n'
        return '.\n\n'
    text = NEW_PARAGRAPH.sub(_new_paragraph_repl, text)
    text = NEW_LINE.sub('\n', text)

    # 3g. Convert number words to digits
    # Process each line separately to preserve newlines
    lines = text.split('\n')
    lines = [convert_number_words(line) for line in lines]
    text = '\n'.join(lines)

    # Convert "percent" to %
    text = re.sub(r'\s*\bpercent\b', '%', text, flags=re.IGNORECASE)

    # 3h. Handle numbered lists
    text = format_numbered_list(text)

    # 3i. Final cleanup
    # Clean up extra spaces from removals (but preserve newlines)
    lines = text.split('\n')
    lines = [re.sub(r'\s{2,}', ' ', line).strip() for line in lines]
    # Remove empty lines that aren't paragraph breaks
    text = '\n'.join(lines)
    text = text.strip()

    # Ensure first letter is capitalized
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    # Capitalize first letter after newline or sentence-ending punctuation
    result = []
    for i, char in enumerate(text):
        if char.islower() and i > 0:
            prev = text[i-1]
            if prev == '\n':
                result.append(char.upper())
            elif prev == ' ' and i > 1 and text[i-2] in '.?!':
                result.append(char.upper())
            else:
                result.append(char)
        else:
            result.append(char)
    text = ''.join(result)


    return text


async def process_audio(audio_bytes: bytes, filename: str) -> str:
    """Full pipeline: transcribe + regex cleanup + optional LLM."""
    t0 = time.perf_counter()

    raw_text = transcribe_audio(audio_bytes, filename)
    t1 = time.perf_counter()
    log.info(f"Transcription: {t1 - t0:.2f}s | raw: {raw_text}")

    if not raw_text.strip():
        log.info("Empty transcript -- skipping cleanup")
        return ""

    # Check for "deep clean" trigger before regex cleanup
    use_llm, text, instruction = check_llm_trigger(raw_text)

    # Always run regex cleanup first
    cleaned_text = lightweight_cleanup(text)
    t2 = time.perf_counter()
    log.info(f"Regex cleanup: {t2 - t1:.4f}s | cleaned: {cleaned_text}")

    # If user said "deep clean", also run through LLM
    if use_llm:
        if instruction:
            log.info(f"Deep clean: YES — routing to LLM ({OLLAMA_MODEL}) with instruction: {instruction}")
        else:
            log.info(f"Deep clean: YES — routing to LLM ({OLLAMA_MODEL})")
        cleaned_text = await cleanup_with_ollama(cleaned_text, instruction)
        t3 = time.perf_counter()
        log.info(f"LLM cleanup: {t3 - t2:.2f}s | final: {cleaned_text}")
        log.info(f"Total: {t3 - t0:.2f}s (regex + LLM)")
    else:
        log.info("Deep clean: NO (regex only)")
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
