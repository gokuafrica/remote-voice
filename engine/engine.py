"""
Remote Voice engine — stdio sidecar (port of master/server.py pipeline).

Speaks newline-delimited JSON over stdio (see SPEC.md "Engine contract").
stdout carries protocol ONLY; all diagnostics go to stderr.

Run:  python -u engine/engine.py --config <abs path to config.json>
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import asyncio
import logging
from pathlib import Path

# pythonw.exe has no console streams. Logging and protocol writes need
# file-like stdout/stderr objects; patch null sinks in before any logging use.
# NOTE: stdout is the protocol channel — it is only replaced when it is None
# (headless pythonw), in which case the caller has no use for protocol anyway.
_stdio_sinks = []


def _ensure_standard_streams():
    for stream_name in ("stdout", "stderr"):
        if getattr(sys, stream_name) is None:
            sink = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, stream_name, sink)
            _stdio_sinks.append(sink)


_ensure_standard_streams()

# Add CUDA DLL directories to PATH before importing onnxruntime.
# Wheels may live in <sys.prefix>\Lib\site-packages (venv/embedded Python)
# or in %APPDATA%\Roaming\Python\Python3XX\site-packages (pip install --user).
_nvidia_candidates = [
    os.path.join(sys.prefix, "Lib", "site-packages", "nvidia"),
    os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming", "Python",
        f"Python{sys.version_info.major}{sys.version_info.minor}",
        "site-packages", "nvidia",
    ),
]
_cuda_paths = []
for _base in _nvidia_candidates:
    if os.path.isdir(_base):
        _cuda_paths.extend(d for d in glob.glob(os.path.join(_base, "*", "bin")) if os.path.isdir(d))
_cuda_paths = list(dict.fromkeys(_cuda_paths))
if _cuda_paths:
    for _dir in _cuda_paths:
        os.add_dll_directory(_dir)
    os.environ["PATH"] = os.pathsep.join(_cuda_paths) + os.pathsep + os.environ.get("PATH", "")

import httpx
import onnx_asr
from word2number import w2n

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = None
cfg = {}
VOICE_MODEL = "nemo-parakeet-tdt-0.6b-v2"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"
CLEANUP_PROMPT = "Clean this transcript:\n"
PRONUNCIATION_FIXES = {}
PRONUNCIATION_FIX_PATTERNS = []


def load_config(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config() -> None:
    """Persist current config back to disk (used by set_fixes hot-reload)."""
    if not CONFIG_PATH:
        log.warning("No --config path given; cannot persist config changes")
        return
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------------------
# Pronunciation fixes — accent-based alias substitution
# ---------------------------------------------------------------------------


def compile_pronunciation_fixes(fixes: dict) -> list[tuple[re.Pattern, str]]:
    r"""Compile a {mispronunciation: correction} dict into regex patterns.

    Each multi-word key gets ``[,.\s-]+`` between words (handles Parakeet
    commas, periods, and hyphens) and ``\b`` word boundaries (prevents
    partial word matches). Sorted longest-first so more specific phrases
    match before shorter ones.
    """
    patterns = []
    for wrong, right in sorted(fixes.items(), key=lambda x: -len(x[0].split())):
        words = wrong.strip().split()
        if not words:
            continue
        regex = r'\b' + r'[,.\s-]+'.join(re.escape(w) for w in words) + r'\b'
        patterns.append((re.compile(regex, re.IGNORECASE), right))
    return patterns


def apply_pronunciation_fixes(text: str) -> str:
    """Replace known mispronunciations before pipeline processing."""
    for pattern, replacement in PRONUNCIATION_FIX_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("remote-voice")

model = None
model_ready = False
model_error = None


def load_voice_model():
    """Prefer CUDA, but keep the engine usable without a compatible GPU.

    The GPU wheel also advertises TensorRT even when TensorRT is not installed,
    so do not let onnx-asr select providers from the wheel's advertised list.
    """
    try:
        return onnx_asr.load_model(
            VOICE_MODEL,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
    except Exception as exc:
        log.warning("GPU model load failed; retrying on CPU: %s", exc)
        return onnx_asr.load_model(
            VOICE_MODEL,
            providers=["CPUExecutionProvider"],
        )


def _find_ffmpeg() -> str:
    bundled = Path(__file__).parent / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)
    return shutil.which("ffmpeg") or "ffmpeg"


def convert_to_wav(input_path: str) -> str:
    """Convert any audio format to 16kHz mono WAV using ffmpeg."""
    wav_path = input_path + ".wav"
    subprocess.run(
        [_find_ffmpeg(), "-y", "-i", input_path, "-ar", "16000", "-ac", "1", wav_path],
        capture_output=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return wav_path


def transcribe_file(audio_path: str) -> str:
    """Convert to WAV if needed, run Parakeet V2.

    Handles CUDA error 999 (Windows TDR GPU driver reset) by reloading
    the model once and retrying. TDR invalidates the CUDA context in all
    running processes — a fresh model load creates a new context.
    """
    global model
    suffix = os.path.splitext(audio_path)[1].lower()
    work_path = audio_path
    wav_path = None

    if suffix != ".wav":
        wav_path = convert_to_wav(audio_path)
        work_path = wav_path

    try:
        try:
            return str(model.recognize(work_path))
        except Exception as e:
            if "CUDA failure 999" in str(e) or "ONNXRuntimeError" in str(e):
                log.warning(f"CUDA context lost ({e.__class__.__name__}) — reloading model and retrying...")
                model = load_voice_model()
                log.info("Model reloaded successfully.")
                return str(model.recognize(work_path))
            raise
    finally:
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)


async def cleanup_with_ollama(raw_text: str, instruction: str = "") -> str:
    """Send raw transcript to Ollama for cleanup."""
    log.info(f"LLM cleanup using model: {OLLAMA_MODEL}")
    prompt = CLEANUP_PROMPT
    if instruction:
        prompt += f"\n\nAdditional instruction from the speaker: {instruction}. Apply this while still following all cleanup rules above."
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
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        log.warning(f"Ollama not reachable ({e.__class__.__name__}) — returning regex-cleaned text")
        return raw_text
    except httpx.HTTPStatusError as e:
        log.warning(f"Ollama returned HTTP {e.response.status_code} — returning regex-cleaned text")
        return raw_text
    except Exception as e:
        log.warning(f"Ollama cleanup failed ({e}), returning regex-cleaned transcript")
        return raw_text


# ---------------------------------------------------------------------------
# Regex-based lightweight cleanup
# ---------------------------------------------------------------------------

LLM_TRIGGER = re.compile(
    r'[,.]?\s*\bdeep[,.\s-]+format\b'        # "deep format" command
    r'(?:[,.?!:\s]+(.+?))?'                   # optional instruction (anything after = custom prompt)
    r'[,.?!:]?\s*$',                          # trailing punct + end
    re.IGNORECASE,
)

START_OVER = re.compile(r'^.*[,.]?\s*\bstart[,.\s-]+over\b[,.]?\s*', re.IGNORECASE)

SCRATCH_THAT = re.compile(r'[^.!?\n]*[,.]?[^\S\n]*\bscratch[,.\s-]+that\b[,.]?[^\S\n]*', re.IGNORECASE)

FILLER_PATTERN = re.compile(r'\b(um|uh|you[\s-]+know)\b[,.]?\s*', re.IGNORECASE)

NEW_PARAGRAPH = re.compile(r'[,.]?\s*\bnew[,.\s-]+paragraph\b[,.]?\s*', re.IGNORECASE)
NEW_LINE = re.compile(r'([,.]?)\s*\bnew[,.\s-]+line\b[,.]?\s*', re.IGNORECASE)

SPOKEN_PUNCTUATION = [
    # Multi-word: consume leading space/punct only (preserve trailing space for natural flow)
    (re.compile(r'[,.]?\s*\bquestion[,.\s-]+mark\b[,.]?', re.IGNORECASE), '? '),
    (re.compile(r'[,.]?\s*\bexclamation[,.\s-]+point\b[,.]?', re.IGNORECASE), '! '),
    (re.compile(r'[,.]?\s*\bopen[,.\s-]+parenthesis\b[,.]?\s*', re.IGNORECASE), ' ('),
    (re.compile(r'[,.]?\s*\bclose[,.\s-]+parenthesis\b[,.]?\s*', re.IGNORECASE), ') '),
    (re.compile(r'\s*\bdouble[,.\s-]+quote\b\s*', re.IGNORECASE), '"'),
    (re.compile(r'\s*\bquotation[,.\s-]+mark\b\s*', re.IGNORECASE), '"'),
    (re.compile(r'\s*\bsingle[,.\s-]+quote\b\s*', re.IGNORECASE), "'"),
    (re.compile(r'[,.]?\s*\bpercent[,.\s-]+sign\b[,.]?', re.IGNORECASE), '% '),
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

# ---------------------------------------------------------------------------
# Emoji patterns
# Replacement includes surrounding spaces (' 😊 ') because [,.]?\s* before
# the command consumes the leading space; the final cleanup collapses any
# double-spaces that result.
#
# Two-word phrases (e.g. "smiley face") use [,.\s-]+ between words to handle
# Parakeet inserting a comma, period, or hyphen at the pause.
#
# Ambiguous single words (heart, fire, star, …) require an explicit "emoji"
# suffix so common English words are never accidentally converted.
# ---------------------------------------------------------------------------

EMOJI_PATTERNS = [
    # ── Faces ──────────────────────────────────────────────────────────────
    (re.compile(r'[,.]?\s*\bsmiley[,.\s-]+face\b[,.]?',    re.IGNORECASE), ' 😊 '),
    (re.compile(r'[,.]?\s*\blaughing[,.\s-]+face\b[,.]?',  re.IGNORECASE), ' 😂 '),
    (re.compile(r'[,.]?\s*\bwinking[,.\s-]+face\b[,.]?',   re.IGNORECASE), ' 😉 '),
    (re.compile(r'[,.]?\s*\bthinking[,.\s-]+face\b[,.]?',  re.IGNORECASE), ' 🤔 '),
    (re.compile(r'[,.]?\s*\braised[,.\s-]+eyebrow\b[,.]?', re.IGNORECASE), ' 🤨 '),
    (re.compile(r'[,.]?\s*\bface[,.\s-]+palm\b[,.]?',      re.IGNORECASE), ' 🤦 '),
    (re.compile(r'[,.]?\s*\bfacepalm\b[,.]?',              re.IGNORECASE), ' 🤦 '),
    (re.compile(r'[,.]?\s*\beye[,.\s-]+roll\b[,.]?',       re.IGNORECASE), ' 🙄 '),
    # ── Hands ──────────────────────────────────────────────────────────────
    (re.compile(r'[,.]?\s*\bthumbs[,.\s-]+up\b[,.]?',       re.IGNORECASE), ' 👍 '),
    (re.compile(r'[,.]?\s*\bthumbs[,.\s-]+down\b[,.]?',     re.IGNORECASE), ' 👎 '),
    (re.compile(r'[,.]?\s*\bclapping[,.\s-]+hands\b[,.]?',  re.IGNORECASE), ' 👏 '),
    (re.compile(r'[,.]?\s*\bwaving[,.\s-]+hand\b[,.]?',     re.IGNORECASE), ' 👋 '),
    (re.compile(r'[,.]?\s*\bcrossed[,.\s-]+fingers\b[,.]?', re.IGNORECASE), ' 🤞 '),
    (re.compile(r'[,.]?\s*\bfolded[,.\s-]+hands\b[,.]?',    re.IGNORECASE), ' 🙏 '),
    (re.compile(r'[,.]?\s*\bok[,.\s-]+hand\b[,.]?',         re.IGNORECASE), ' 👌 '),
    (re.compile(r'[,.]?\s*\bpeace[,.\s-]+sign\b[,.]?',      re.IGNORECASE), ' ✌️ '),
    # ── Symbols ────────────────────────────────────────────────────────────
    (re.compile(r'[,.]?\s*\bcheck[,.\s-]+mark\b[,.]?',      re.IGNORECASE), ' ✅ '),
    (re.compile(r'[,.]?\s*\bred[,.\s-]+x\b[,.]?',           re.IGNORECASE), ' ❌ '),
    (re.compile(r'[,.]?\s*\bparty[,.\s-]+popper\b[,.]?',    re.IGNORECASE), ' 🎉 '),
    (re.compile(r'[,.]?\s*\bbroken[,.\s-]+heart\b[,.]?',    re.IGNORECASE), ' 💔 '),
    # ── Require "emoji" suffix (ambiguous standalone words) ────────────────
    (re.compile(r'[,.]?\s*\bshrug[,.\s-]+emoji\b[,.]?',     re.IGNORECASE), ' 🤷 '),
    (re.compile(r'[,.]?\s*\bmuscle[,.\s-]+emoji\b[,.]?',    re.IGNORECASE), ' 💪 '),
    (re.compile(r'[,.]?\s*\bsparkles[,.\s-]+emoji\b[,.]?',  re.IGNORECASE), ' ✨ '),
    (re.compile(r'[,.]?\s*\brocket[,.\s-]+emoji\b[,.]?',    re.IGNORECASE), ' 🚀 '),
    (re.compile(r'[,.]?\s*\bskull[,.\s-]+emoji\b[,.]?',     re.IGNORECASE), ' 💀 '),
    (re.compile(r'[,.]?\s*\bpoop[,.\s-]+emoji\b[,.]?',      re.IGNORECASE), ' 💩 '),
    (re.compile(r'[,.]?\s*\bheart[,.\s-]+emoji\b[,.]?',     re.IGNORECASE), ' ❤️ '),
    (re.compile(r'[,.]?\s*\bfire[,.\s-]+emoji\b[,.]?',      re.IGNORECASE), ' 🔥 '),
    (re.compile(r'[,.]?\s*\bstar[,.\s-]+emoji\b[,.]?',      re.IGNORECASE), ' ⭐ '),
    (re.compile(r'[,.]?\s*\bhundred[,.\s-]+emoji\b[,.]?',   re.IGNORECASE), ' 💯 '),
]


def apply_emoji_patterns(text: str) -> str:
    """Replace spoken emoji phrases with their Unicode emoji characters."""
    for pattern, emoji_char in EMOJI_PATTERNS:
        text = pattern.sub(emoji_char, text)
    return text


NUMBER_WORDS = re.compile(
    r'\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|'
    r'eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|'
    r'eighty|ninety|hundred|thousand|million|billion)\b',
    re.IGNORECASE
)

BULLET_PATTERN = re.compile(r'[,.]?\s*\bbullet[,.\s-]+(\d+)\b[,.]?\s*', re.IGNORECASE)
END_LIST = re.compile(r'[,.]?\s*\bend[,.\s-]+list\b[,.]?\s*', re.IGNORECASE)


def check_llm_trigger(text: str) -> tuple[bool, str, str]:
    """Check if text ends with 'deep format' command.

    Returns (trigger, cleaned_text, instruction).
    'instruction' is empty for plain 'deep format', or 'format: <text>'
    when the user appends a custom instruction after 'deep format'.
    """
    m = LLM_TRIGGER.search(text)
    if m:
        cleaned = text[:m.start()].strip()
        raw_instruction = (m.group(1) or '').strip().rstrip('.,!?')
        instruction = f"format: {raw_instruction}" if raw_instruction else ""
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

    # 1. Handle "start over"
    text = START_OVER.sub('', text).strip()
    if not text:
        return ""

    # 2. Remove filler words
    text = FILLER_PATTERN.sub('', text)

    # 3. Convert spoken punctuation to symbols
    for pattern, symbol in SPOKEN_PUNCTUATION:
        text = pattern.sub(symbol, text)

    # 4. Remove Parakeet's period before manually dictated punctuation
    text = re.sub(r'\.\s*([,;:?!\-/\'\"()%])', r'\1', text)

    # 5. Collapse duplicate commas (e.g., Parakeet comma + spoken "comma")
    text = re.sub(r',([\s]*,)+', ',', text)

    # 6. Fix spacing around quotes: add space before opening quote (between two letters)
    text = re.sub(r'([a-zA-Z])"([a-zA-Z])', r'\1 "\2', text)

    # 7. Handle "new paragraph" and "new line" (before scratch-that so
    #    line/paragraph boundaries are real \n chars that scratch-that respects)
    def _new_paragraph_repl(m):
        if m.start() == 0:
            return '\n\n'
        return '.\n\n'

    def _new_line_repl(m):
        pre = m.group(1)
        if pre:
            return pre + '\n'
        return '\n'

    text = NEW_PARAGRAPH.sub(_new_paragraph_repl, text)
    text = NEW_LINE.sub(_new_line_repl, text)

    # 8. Handle "scratch that" — runs after new paragraph/line so it
    #    respects line and paragraph boundaries (stops at \n)
    text = SCRATCH_THAT.sub(' ', text).strip()
    if not text:
        return ""

    # 8.5. Convert spoken emoji phrases to emoji characters
    # Per-line to prevent [,.\s-]+ from matching across line breaks.
    # Must run BEFORE number conversion: "hundred emoji" would otherwise
    # become "100 emoji" first and never match the emoji pattern.
    lines = text.split('\n')
    lines = [apply_emoji_patterns(line) for line in lines]
    text = '\n'.join(lines)

    # 9. Convert number words to digits
    # Process each line separately to preserve newlines
    lines = text.split('\n')
    lines = [convert_number_words(line) for line in lines]
    text = '\n'.join(lines)

    # Convert "percent" to %
    text = re.sub(r'\s*\bpercent\b', '%', text, flags=re.IGNORECASE)

    # 10. Handle numbered lists
    text = format_numbered_list(text)

    # 11. Final cleanup
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


# ---------------------------------------------------------------------------
# Stdio protocol
# ---------------------------------------------------------------------------


def send(obj: dict) -> None:
    """Write one JSON line to stdout and flush immediately."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_ping(msg: dict) -> dict:
    return {
        "id": msg.get("id"),
        "ok": True,
        "ready": model_ready,
        "model": VOICE_MODEL,
        "error": model_error,
    }


def handle_transcribe(msg: dict) -> dict:
    global model_ready, model_error
    wav_path = msg.get("wav_path")
    if not wav_path or not os.path.isabs(wav_path):
        return {"id": msg.get("id"), "ok": False, "error": "wav_path must be an absolute path"}
    if not os.path.isfile(wav_path):
        return {"id": msg.get("id"), "ok": False, "error": f"file not found: {wav_path}"}
    if model is None:
        return {"id": msg.get("id"), "ok": False, "error": model_error or "model not loaded"}

    try:
        t0 = time.perf_counter()
        raw_text = transcribe_file(wav_path)
        t1 = time.perf_counter()
        log.info(f"Transcription: {t1 - t0:.2f}s | raw: {raw_text}")

        if not raw_text.strip():
            log.info("Empty transcript -- skipping cleanup")
            return {
                "id": msg.get("id"),
                "ok": True,
                "text": "",
                "timings": {"transcribe_s": round(t1 - t0, 2), "cleanup_s": 0.0},
            }

        # Apply pronunciation fixes before any processing
        raw_text = apply_pronunciation_fixes(raw_text)

        # Check for "deep format" trigger before regex cleanup
        use_llm, text, instruction = check_llm_trigger(raw_text)

        # Always run regex cleanup first
        cleaned_text = lightweight_cleanup(text)
        t2 = time.perf_counter()
        log.info(f"Regex cleanup: {t2 - t1:.4f}s | cleaned: {cleaned_text}")

        # If user said "deep format" AND the LLM path is enabled, also run through LLM
        if use_llm and cfg.get("use_llm", False):
            log.info(f"Deep format: YES — routing to LLM ({OLLAMA_MODEL})")
            cleaned_text = asyncio.run(cleanup_with_ollama(cleaned_text, instruction))
            t3 = time.perf_counter()
            log.info(f"LLM cleanup: {t3 - t2:.2f}s | final: {cleaned_text}")
        else:
            log.info(f"Total: {t2 - t0:.2f}s")

        return {
            "id": msg.get("id"),
            "ok": True,
            "text": cleaned_text,
            "timings": {
                "transcribe_s": round(t1 - t0, 2),
                "cleanup_s": round(t2 - t1, 4),
            },
        }
    except Exception as e:
        log.exception("transcribe failed")
        return {"id": msg.get("id"), "ok": False, "error": str(e)}


def handle_set_fixes(msg: dict) -> dict:
    global PRONUNCIATION_FIX_PATTERNS
    fixes = msg.get("fixes")
    if not isinstance(fixes, dict):
        return {"id": msg.get("id"), "ok": False, "error": "fixes must be an object"}
    for key in fixes:
        if not isinstance(key, str) or not isinstance(fixes[key], str):
            return {"id": msg.get("id"), "ok": False, "error": "fixes must map strings to strings"}

    try:
        compile_pronunciation_fixes(fixes)
    except re.error as e:
        return {"id": msg.get("id"), "ok": False, "error": f"invalid fixes: {e}"}

    cfg["pronunciation_fixes"] = fixes
    PRONUNCIATION_FIX_PATTERNS = compile_pronunciation_fixes(fixes)
    log.info(f"Pronunciation fixes hot-reloaded ({len(PRONUNCIATION_FIX_PATTERNS)} patterns)")

    try:
        save_config()
    except Exception as e:
        log.warning(f"Could not persist fixes to config: {e}")

    return {"id": msg.get("id"), "ok": True}


def handle_line(line: str) -> None:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        send({"id": None, "ok": False, "error": f"bad JSON: {e}"})
        return

    msg_id = msg.get("id") if isinstance(msg, dict) else None
    op = msg.get("op") if isinstance(msg, dict) else None

    try:
        if op == "ping":
            send(handle_ping(msg))
        elif op == "transcribe":
            send(handle_transcribe(msg))
        elif op == "set_fixes":
            send(handle_set_fixes(msg))
        elif op == "shutdown":
            log.info("Shutdown requested — exiting.")
            sys.exit(0)
        else:
            send({"id": msg_id, "ok": False, "error": f"unknown op: {op!r}"})
    except SystemExit:
        raise
    except Exception as e:
        log.exception("request handler failed")
        send({"id": msg_id, "ok": False, "error": str(e)})


def parse_args(argv: list[str]) -> str:
    config_path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--config" and i + 1 < len(argv):
            config_path = argv[i + 1]
            i += 2
        elif arg.startswith("--config="):
            config_path = arg.split("=", 1)[1]
            i += 1
        else:
            i += 1
    return os.path.abspath(config_path) if config_path else None


def main() -> None:
    global CONFIG_PATH, cfg, VOICE_MODEL, OLLAMA_URL, OLLAMA_MODEL
    global CLEANUP_PROMPT, PRONUNCIATION_FIXES, PRONUNCIATION_FIX_PATTERNS
    global model, model_ready, model_error

    CONFIG_PATH = parse_args(sys.argv[1:])
    cfg = load_config(CONFIG_PATH)

    OLLAMA_URL = cfg.get("ollama_url", OLLAMA_URL)
    OLLAMA_MODEL = cfg.get("ollama_model", OLLAMA_MODEL)
    VOICE_MODEL = cfg.get("voice_model", VOICE_MODEL)
    CLEANUP_PROMPT = cfg.get("cleanup_prompt", CLEANUP_PROMPT)
    PRONUNCIATION_FIXES = cfg.get("pronunciation_fixes", {})
    PRONUNCIATION_FIX_PATTERNS = compile_pronunciation_fixes(PRONUNCIATION_FIXES)

    log.info(f"Engine starting. Config: {CONFIG_PATH or '(none)'}")
    log.info(f"Voice model: {VOICE_MODEL} | fixes: {len(PRONUNCIATION_FIX_PATTERNS)} patterns")

    # Preload the model BEFORE reading stdin so ping can report ready immediately.
    t0 = time.perf_counter()
    try:
        model = load_voice_model()
        model_ready = True
        log.info(f"Voice model loaded in {time.perf_counter() - t0:.2f}s. Engine ready.")
    except Exception as e:
        model_error = f"model load failed: {e}"
        log.exception("Voice model load failed — engine will report not ready")

    # Robust line reader: binary stdin, split on newline (partial lines buffered).
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    if stdin is None:
        log.error("No stdin available — nothing to do.")
        return

    while True:
        line = stdin.readline()
        if not line:
            log.info("stdin EOF — exiting.")
            break
        line = line.decode("utf-8", errors="replace").strip()
        if line:
            handle_line(line)


if __name__ == "__main__":
    main()
