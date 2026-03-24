# Remote Voice

Local voice transcription pipeline that runs on your own hardware. Record voice on your phone or PC, get cleaned text back — powered by NVIDIA Parakeet V2 with fast regex-based post-processing.

Think WhisperFlow / Superwhisper, but fully local, GPU-accelerated, and with built-in voice commands for formatting, punctuation, and editing.

## How It Works

```
Phone or PC Mic
      |
      v
  FastAPI Server (port 8787)
      |
      +-- ffmpeg (convert to WAV if needed)
      |
      +-- Parakeet V2 via ONNX Runtime (GPU transcription, ~0.3-0.5s)
      |
      +-- Regex cleanup (fillers, numbers, punctuation, commands, <5ms)
      |
      +-- [Optional] Ollama LLM (only when you say "deep format")
      |
      v
  Cleaned text returned
      |
      +-- Phone: inserted into any app via whisper-to-input keyboard
      +-- PC: pasted into focused window via tray app hotkey
```

By default, no LLM is used. Regex handles all deterministic cleanup tasks in under 5ms. You can explicitly invoke the LLM for semantic tasks (self-corrections, filler "like" disambiguation) by ending your dictation with **"deep format"**.

## Voice Commands

These commands are recognized during dictation and processed by the regex pipeline.

**Parakeet compatibility:** Parakeet V2 adds its own punctuation — it may insert commas around pauses and hyphens between multi-word phrases. For example, if you pause around "new line", Parakeet might output `hello, new line, world` or `hello, new-line, world`. All voice commands and spoken punctuation patterns handle this automatically: commas/periods before and after the command are consumed, and hyphens between command words are accepted.

### Formatting

| You say | Result | Example |
|---------|--------|---------|
| **new line** | Inserts a line break | "first line **new line** second line" → `first line`<br>`second line` |
| **new paragraph** | Inserts a paragraph break (double newline) with a period before it | "intro text **new paragraph** body text" → `intro text.`<br><br>`body text` |

### Editing

| You say | Result | Example |
|---------|--------|---------|
| **scratch that** | Deletes the current and preceding sentence (respects line/paragraph boundaries) | "I need apples. Get oranges. **Scratch that.**" → `I need apples.` |
| **start over** | Deletes everything before it, keeps only what follows | "blah blah **start over** the real message" → `The real message` |

### Numbered Lists

Start each item with **"bullet N"** and end the list with **"end list"**. Both markers must be present — if you say "bullet" without "end list", it's treated as the regular word.

| You say | Result |
|---------|--------|
| "**Bullet 1** apples **bullet 2** bananas **bullet 3** oranges **end list**" | `1. Apples`<br>`2. Bananas`<br>`3. Oranges` |
| "Here are my items. **Bullet 1** apples. **Bullet 2** bananas. **End list.** That is all." | `Here are my items.`<br>`1. Apples`<br>`2. Bananas`<br>`That is all.` |
| "The bullet hit the wall" | `The bullet hit the wall` (unchanged — no "end list") |

### Spoken Punctuation

Say the punctuation name and it gets replaced with the symbol.

| You say | Result |
|---------|--------|
| **comma** | `,` |
| **period** | `.` |
| **question mark** | `?` |
| **exclamation point** | `!` |
| **colon** | `:` |
| **semicolon** | `;` |
| **hyphen** / **dash** | `-` |
| **ellipsis** | `...` |
| **slash** | `/` |
| **apostrophe** | `'` |
| **quotation mark** / **double quote** | `"` |
| **single quote** | `'` |
| **open parenthesis** | `(` |
| **close parenthesis** | `)` |
| **percent sign** | `%` |

**Examples:**

| You say | You get |
|---------|---------|
| "dear sir **comma** the answer is no **period**" | `Dear sir, the answer is no.` |
| "is this correct **question mark**" | `Is this correct?` |
| "it **apostrophe** s fine" | `It's fine` |
| "use **open parenthesis** optional **close parenthesis**" | `Use (optional)` |

### Numbers

Number words are automatically converted to digits. Multi-word numbers and "percent" are supported.

| You say | You get |
|---------|---------|
| "I need **twenty five** dollars" | `I need 25 dollars` |
| "**one hundred and thirty five**" | `135` |
| "the price is **ten percent** higher" | `The price is 10% higher` |

The words "I" and "a" are never converted to numbers.

### Filler Words

**um**, **uh**, and **you know** are automatically removed. The word **like** is deliberately *not* removed by regex because it can't distinguish filler ("it was like super hard") from verb ("I like this"). Use the **"deep format"** command if you need filler "like" cleaned up.

### Pronunciation Fixes

If Parakeet consistently mishears a word or phrase due to your accent, you can define pronunciation fixes. These run before all other processing — the mispronounced words are silently replaced with the correct ones, and then the pipeline handles them normally.

Configure fixes in the GUI under **Pronunciation Fixes** (format: `wrong = correct`, one per line), or in `config.json`:

```json
{
    "pronunciation_fixes": {
        "new lion": "new line"
    }
}
```

**How it works:** The fix replaces the mispronounced words with the correct words in the raw transcript. Parakeet hyphens between alias words are handled automatically (e.g., "new-lion" also matches). Surrounding punctuation (commas, periods) is left untouched — the downstream voice command regex handles that as usual.

| Parakeet hears | Fix produces | Pipeline result |
|---|---|---|
| "hello **new lion** world" | "hello **new line** world" | `hello`<br>`world` |
| "hello, **new-lion**, world" | "hello, **new line**, world" | `hello`<br>`world` |

**Important:** Every occurrence of the mispronounced phrase will be replaced — the fix doesn't know whether you meant it literally. Only add entries you're confident won't appear naturally in your speech. The server must be restarted after changing fixes.

### Punctuation Behavior

**Parakeet's punctuation is trusted.** The pipeline does not force a trailing period onto your text. If Parakeet adds a period at the end, it stays. If it doesn't (e.g., for sentence fragments or questions), nothing is added. This means the output preserves Parakeet's own judgment about sentence structure.

**Period removal before manual punctuation:** When you dictate spoken punctuation (e.g., "comma", "question mark"), Parakeet may have already added a period before it — thinking the sentence ended. The pipeline automatically removes that stale period. For example, Parakeet might output `"Dear sir. Comma the answer is no."` and the pipeline produces `Dear sir, the answer is no.` (the period before "comma" is cleaned up).

**Duplicate comma collapse:** If Parakeet adds a comma at a natural pause and you also say "comma", the resulting double comma (`,,`) is automatically collapsed to a single comma.

**Punctuation preserved before new line:** If Parakeet adds a period or comma before "new line", it is preserved. For example, `"end of sentence. New line next sentence"` produces `end of sentence.` followed by a line break, and `"Dear President, new line, hello"` produces `Dear President,` followed by a line break.

### Deep Format — LLM Post-Processing (Optional)

End your dictation with **"deep format"** to route the text through the configured Ollama model after regex cleanup. The regex pipeline runs first (fillers, numbers, punctuation all handled), then the LLM receives already-cleaned text and only handles semantic tasks:

- **Self-corrections**: "I need four, sorry, I meant two" → `I need 2`
- **Filler "like" removal**: "I like this but like we should go" → `I like this, but we should go.`
- **Natural restatements**: Keeps only the final version when you rephrase
- **Grammar smoothing**: Fixes awkward phrasing left after filler removal

#### Custom Instructions

Anything you say after **"deep format"** becomes a custom instruction to the LLM:

| You say | What happens |
|---------|-------------|
| "...text **deep format**" | Standard deep format (self-corrections, filler removal, etc.) |
| "...text **deep format** check the math" | Deep format + LLM also verifies the math |
| "...text **deep format** check the facts" | Deep format + LLM also fact-checks the content |
| "...text **deep format** make it formal" | Deep format + LLM also adjusts the tone |
| "...text **deep format** like an email" | Deep format + LLM also formats as an email |

The custom instruction is prefixed with "format:" and injected into the cleanup prompt as an additional directive — the standard cleanup rules still apply. This means the LLM will clean the transcript *and* follow your instruction, without throwing away the conservative cleanup behavior that keeps transcriptions accurate.

**Examples:**

| You say | You get |
|---------|---------|
| "2 + 2 is 5. **deep format** check the math" | `2 + 2 is 4.` |
| "The Eiffel Tower is in London. **deep format** check the facts" | `The Eiffel Tower is in Paris.` |
| "hey can u come 2morrow. **deep format** make it formal" | `Can you come tomorrow?` |

**Email formatting example** — dictate a stream-of-consciousness message and let the LLM add structure:

> **You say:** "Hey Sarah thanks for the update on the deployment. Two things first the staging environment is ready and QA signed off on all the test cases. Second we found a minor bug in the payment flow where the discount code field doesn't clear after submission but Jake is already on it and should have a fix by end of day. Oh and one more thing can we schedule a quick sync tomorrow morning to go over the launch checklist? Let me know what time works for you. Thanks **deep format** like an email"

> **You get:**
> ```
> Hey Sarah,
>
> Thanks for the update on the deployment.
>
> First, the staging environment is ready, and QA has signed off on all test cases.
> Second, we found a minor bug in the payment flow where the discount code field
> doesn't clear after submission. But Jake is already on it, and he should have
> a fix by the end of the day.
>
> Oh, one more thing: can we schedule a quick sync tomorrow morning to go over
> the launch checklist? Let me know what time works for you.
>
> Thanks
> ```

#### Known Quirks (qwen2.5:7b)

- The LLM reliably handles clear corrections ("sorry I meant", "no wait", "I mean", "actually X"). `"Send it to John, no wait, Mike. deep format"` → `Send it to Mike.`
- `"I'm sorry for the delay"` stays intact — the LLM correctly identifies this as a natural apology, not a self-correction.
- However, the LLM sometimes over-corrects borderline cases. For example, it may remove a natural "actually" from `"I actually think this is great"` → `I think this is great.` This is the model being overly aggressive, not a pipeline bug.
- Similarly, `"I'm sorry but X"` may get shortened to just `X` because "sorry + but" looks like a correction pattern to the model.
- The LLM may rephrase slightly (e.g., "I'm sorry" → "I apologize"). This is the grammar smoothing task doing its job, but it means output won't always be word-for-word identical to the input.

These quirks are inherent to using a 7B parameter model for semantic tasks. For most dictation use cases — correcting mistakes, removing filler "like", cleaning up restatements — it works well.

**Note:** The LLM adds ~0.5-1s per sentence (model kept warm in VRAM via `keep_alive: -1`). Without the trigger, responses are near-instant (~0.5s total).

## Components

| Component | File | What it does |
|-----------|------|-------------|
| **Server** | `server.py` | Accepts audio, transcribes + cleans, returns text |
| **Server GUI** | `gui.py` | Configure models, prompt, start/stop server, view logs |
| **Tray App** | `tray.py` | System tray hotkey — record mic, transcribe, paste into any app |
| **Phone Client** | [whisper-to-input](https://github.com/j3soon/whisper-to-input) | Android keyboard that sends audio to the server |
| **Tests** | `tests.py` | 103 tests covering regex pipeline + LLM deep format path |

## Setup

### Prerequisites

- Windows 11 with NVIDIA GPU (tested on RTX 4070 Ti)
- Python 3.10+
- [Ollama](https://ollama.ai) running locally with a model (e.g. `ollama pull qwen2.5:7b`) — only needed if you plan to use the "deep format" command
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

### Mac Client Setup

The Mac client records audio via hotkey and sends it to the Windows server over Tailscale. No transcription runs on the Mac.

```bash
brew install portaudio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-mac.txt
python mac_tray.py
```

Each new terminal session, activate the venv before running:
```bash
source venv/bin/activate
python mac_tray.py
```

On first run, click the menu bar icon and select **"Server URL..."** to enter your Windows PC's Tailscale IP (e.g. `http://100.x.y.z:8787`).

**Required macOS permissions** (System Settings > Privacy & Security):
- **Accessibility** — add Terminal.app (for hotkey suppression and paste simulation)
- **Input Monitoring** — add Terminal.app (for global hotkey detection)
- **Microphone** — prompted automatically on first recording

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
| `ollama_model` | LLM for transcript cleanup (used only with "deep format") | `qwen2.5:7b` |
| `voice_model` | Speech-to-text model | `nemo-parakeet-tdt-0.6b-v2` |
| `cleanup_prompt` | Instructions sent to the LLM when explicitly triggered | See config.json |

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

On RTX 4070 Ti with Parakeet V2:

| Path | Latency | When |
|------|---------|------|
| Default (regex only) | ~0.3-0.5s | Every transcription |
| With LLM (regex + Ollama) | ~5-20s | Only when you say "deep format" |

The regex cleanup adds <5ms on top of transcription time. The LLM is kept loaded in VRAM (`keep_alive: -1`) to eliminate cold start delays when explicitly invoked.

## Testing

The test suite lives in `tests.py` and covers both the regex pipeline (deterministic) and the LLM deep format path (requires Ollama).

```bash
python tests.py              # Run all tests (regex + LLM)
python tests.py --regex-only # Regex tests only (no Ollama needed)
python tests.py --llm-only   # LLM tests only
```

**Part 1 — Regex tests (89 tests):** Exact-match tests for all deterministic cleanup. These cover filler removal, number conversion, all 17 spoken punctuation symbols, period removal before manual punctuation, duplicate comma collapse, Parakeet comma/hyphen variations, new line/paragraph (including period preservation), scratch that (including line/paragraph boundary respect), start over, numbered lists (including false positive rejection), deep format trigger detection (with and without custom instructions), pronunciation fixes (substitution and full pipeline), and edge cases. These tests require no external dependencies and always produce the same result.

**Part 2 — LLM tests (14 tests):** End-to-end tests that send text through the full deep format path (regex cleanup → Ollama). These verify self-corrections, natural usage preservation, filler "like" disambiguation, restatements, the combined regex+LLM pipeline, and custom instructions (math checking, fact checking, formality). Because LLM output is non-deterministic, these tests check properties (must contain / must not contain) rather than exact strings. They require Ollama running with the configured model — if Ollama is unavailable, LLM tests are skipped gracefully.

## Contributing

Every change must update **code, tests, and docs together** in the same commit:

1. **Code** — implement the change in `server.py` / `gui.py` / etc.
2. **Tests** — add or update tests in `tests.py` covering the change
3. **Docs** — update this README to reflect the new behavior

Run `python tests.py --regex-only` before committing (fast, no dependencies). Run `python tests.py` for the full suite if Ollama is available.

AI agents: see `CLAUDE.md` for detailed project conventions, architecture, and regex pattern rules.

## Privacy

- All processing is local — audio never leaves your network
- Temp audio files are deleted immediately after processing
- Tailscale encrypts all phone-to-PC traffic
- No cloud APIs, no telemetry
