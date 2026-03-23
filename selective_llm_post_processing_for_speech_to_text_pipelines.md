# Implementation Plan: Regex-First Pipeline with Optional LLM for Remote Voice

## Context

Remote Voice is a phone-to-PC voice transcription pipeline. Audio comes in via a FastAPI server, gets transcribed by Parakeet V2 (NVIDIA NeMo, ONNX), then cleaned up by Qwen 2.5 7B via Ollama, and the cleaned text is returned.

**The problem:** The LLM cleanup step is the bottleneck. Benchmarks on this machine (RTX 4070 Ti, 12 GB VRAM) show:

| Prompt length | LLM time (warm) | LLM time (cold) |
|---|---|---|
| ~45 words | ~5.8s | ~7.5s |
| ~84 words | ~17.9s | ~20.8s |
| ~172 words | ~21.5s | ~21.8s |

Parakeet V2 transcription itself takes ~0.3-0.5s. The LLM adds 5-20+ seconds on top.

**The solution:** Replace the LLM with regex for all deterministic tasks. The LLM is removed from the default pipeline entirely. Users can explicitly invoke the LLM by saying "post-process LLM" at the end of their dictation.

**Benchmark data location:** Full benchmark results and methodology are in `../ollama-benchmarks/`.

---

## Repository Structure

```
remote-voice/
  server.py         <- FastAPI server (main pipeline: transcribe -> clean -> respond)
  gui.py            <- Tkinter GUI for config + server lifecycle
  tray.py           <- System tray app (hotkey recording + paste)
  config.json       <- Runtime config (model names, Ollama URL, cleanup prompt)
  tray_config.json  <- Tray app config (hotkey, mic device)
  requirements.txt  <- Python dependencies
```

---

## Current Pipeline (in server.py)

```
Audio -> transcribe_audio() -> cleanup_with_ollama() -> return text
          (Parakeet V2)          (Qwen 2.5 7B)
          ~0.3-0.5s              ~5-20s
```

The `process_audio()` function at line 132 orchestrates this. It calls `transcribe_audio()` (line 85) then `cleanup_with_ollama()` (line 107). The only existing optimization is skipping cleanup when the transcript is empty.

---

## New Pipeline

```
Audio -> transcribe_audio() -> lightweight_cleanup() -> return text
          (Parakeet V2)          (regex + word2number)
          ~0.3-0.5s              <5ms

         OR (if user says "post-process LLM" at end):

Audio -> transcribe_audio() -> lightweight_cleanup() -> cleanup_with_ollama() -> return text
          (Parakeet V2)          (regex first)            (LLM for semantic tasks)
          ~0.3-0.5s              <5ms                     ~1-18s (no cold start)
```

The LLM is **never** invoked automatically. It only runs when the user explicitly ends their dictation with "post-process LLM".

## What the Current LLM Prompt Does (and What Replaces Each Task)

| # | Task | Current Handler | New Handler | Why |
|---|---|---|---|---|
| 1 | Number words -> digits | LLM | `word2number` + regex | Deterministic, <1ms |
| 2 | Self-corrections (sorry, I meant, etc.) | LLM | **Not handled by default** | False positive risk too high. "I'm sorry for the delay" gets corrupted. User explicitly invokes LLM when needed. |
| 3 | Filler removal (um, uh, you know) | LLM | Regex | Deterministic, <1ms |
| 4 | Spoken punctuation -> symbols | LLM | Regex | Deterministic, <1ms |
| 5 | Capitalize + add period | LLM | **Parakeet already does this** | NVIDIA model card confirms: "Punctuations and Capitalizations included" |
| 6 | Do not translate | LLM | N/A (Parakeet doesn't translate) | Not needed |

### New Tasks (not in current pipeline)

| # | Task | Handler | How |
|---|---|---|---|
| 7 | "New line" / "New paragraph" | Regex | `\bnew line\b` -> `\n`, `\bnew paragraph\b` -> `\n\n` |
| 8 | "Scratch that" | Regex | Delete from previous sentence boundary to the marker |
| 9 | "Start over" | Regex | Clear all preceding text |
| 10 | Numbered lists (bullet N ... end list) | Regex | Detect `bullet \d+` markers, split, format as numbered list |
| 11 | Extended spoken punctuation | Regex | exclamation point, quotation mark, apostrophe, ellipsis, slash, etc. |
| 12 | "Post-process LLM" trigger | Regex | Detect at end of text, strip it, route to LLM |
| 13 | Filler "like" disambiguation | **LLM only** (when explicitly triggered) | Can't distinguish filler vs verb without semantics |
| 14 | Natural restatements without trigger words | **LLM only** (when explicitly triggered) | Requires semantic understanding |

---

## Implementation: 4 Changes to server.py

### Change 1: Eliminate Cold Start (`keep_alive: -1`)

**What:** Add `"keep_alive": -1` to the Ollama API request in `cleanup_with_ollama()`.

**Why:** Ollama unloads models from VRAM after 5 minutes of inactivity by default. Setting `keep_alive` to `-1` keeps the model loaded indefinitely. This eliminates the ~2 second cold start penalty when the LLM is explicitly invoked.

**Where:** In `server.py`, line 114-119, inside the `client.post()` call.

**Current code (line 113-120):**
```python
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
```

**Change to:**
```python
resp = await client.post(
    f"{OLLAMA_URL}/api/chat",
    json={
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "user", "content": CLEANUP_PROMPT + "\n" + raw_text},
        ],
        "stream": False,
        "keep_alive": -1,
    },
)
```

---

### Change 2: Update the LLM Cleanup Prompt

**What:** Replace the `cleanup_prompt` in `config.json`, the DEFAULTS in `gui.py`, AND the standalone prompt file on the desktop with a slimmed-down prompt that only handles tasks regex can't do.

**Why:** Regex now handles numbers, fillers, spoken punctuation, and formatting. The LLM only needs to handle semantic tasks: self-corrections, filler "like" disambiguation, grammar smoothing, and natural restatements.

**Where:** Three locations must be updated with the same new prompt:
1. `config.json` field `cleanup_prompt`
2. `gui.py` DEFAULTS dict `cleanup_prompt`
3. `C:\Users\Anwesh Mohapatra\OneDrive\Desktop\handy-prompt.txt` (standalone prompt file used for reference/copy-paste)

**New prompt:**
```
You clean transcripts that have already been partially processed.
Numbers, punctuation symbols, and filler words (um, uh) have already been handled.

Your remaining tasks:

1. Self-corrections: if the speaker corrects themselves (e.g., "sorry",
   "I meant", "no wait", "actually"), delete the wrong part and the
   correction word, keep only the fix. But if these words are used
   naturally (e.g., "I'm sorry for the delay"), keep the sentence intact.

2. Remove filler "like" ONLY when used as a filler. Keep "like" meaning
   enjoy or similar.

3. Smooth awkward phrasing left after filler removal.

4. If the speaker restates something (says the same thing differently),
   keep only the final version.

5. Do NOT translate. Do NOT delete meaningful words.

Output ONLY the cleaned text.
```

**Key difference from old prompt:** Rule 1 now explicitly instructs the LLM to distinguish natural usage of "sorry"/"actually" from self-correction usage. This reduces false positives when the user explicitly invokes the LLM path.

---

### Change 3: Add Lightweight Cleanup Function

**What:** A new function `lightweight_cleanup(text)` in `server.py` that handles all deterministic tasks using regex and the `word2number` library.

**New dependency:** `pip install word2number` (add to `requirements.txt`).

#### IMPORTANT: Parakeet Adds Its Own Punctuation

Parakeet V2 outputs punctuation natively. When the user pauses mid-speech, Parakeet may insert commas around voice commands and hyphens between multi-word phrases. For example:

- User says: `"first paragraph [pause] new paragraph [pause] second paragraph"`
- Parakeet outputs: `"First paragraph, new paragraph, second paragraph."` or `"First paragraph, new-paragraph, second paragraph."`

**All multi-word regex patterns in this section MUST handle two Parakeet behaviors — this applies to voice commands (start over, scratch that, new line, etc.), spoken punctuation (question mark, exclamation point, open parenthesis, etc.), and the LLM trigger (post-process LLM):**

**1. Optional surrounding punctuation (commas, periods):** Parakeet inserts commas at natural pauses.
- Use `[,.]?\s*` before and after each command phrase

**2. Hyphens between command words:** Parakeet may hyphenate multi-word phrases (e.g., `start-over`, `scratch-that`, `new-line`, `end-list`).
- Use `[\s-]+` instead of `\s+` between words in a command

Combined examples:

- `new line` -> `r'[,.]?\s*\bnew[\s-]+line\b[,.]?\s*'`
- `scratch that` -> `r'[,.]?\s*\bscratch[\s-]+that\b[,.]?\s*'`
- `start over` -> `r'[,.]?\s*\bstart[\s-]+over\b[,.]?\s*'`
- `end list` -> `r'[,.]?\s*\bend[\s-]+list\b[,.]?\s*'`
- `new paragraph` -> `r'[,.]?\s*\bnew[\s-]+paragraph\b[,.]?\s*'`
- `post-process LLM` -> `r'post[\s-]+process[\s-]+llm'`
- Multi-word spoken punctuation: `question[\s-]+mark`, `exclamation[\s-]+point`, etc.

The regex should consume surrounding commas/periods when replacing, so they don't leave artifacts in the output. **The code samples below show the core logic for each command. The implementing agent must adapt all patterns to follow this convention.**

---

**The function handles, in order:**

#### 3a. Detect and strip "post-process LLM" trigger

```python
LLM_TRIGGER = re.compile(r'\s*post[- ]?process\s+llm[.!?]?\s*$', re.IGNORECASE)

def check_llm_trigger(text: str) -> tuple[bool, str]:
    """Check if text ends with 'post-process LLM' command."""
    if LLM_TRIGGER.search(text):
        cleaned = LLM_TRIGGER.sub('', text).strip()
        return True, cleaned
    return False, text
```

This is checked BEFORE any other processing. If found, the trigger phrase is stripped and the text is routed through regex cleanup first, then the LLM.

#### 3b. Handle "start over"

```python
START_OVER = re.compile(r'^.*\bstart\s+over\b[.!?]?\s*', re.IGNORECASE)
```

If "start over" appears, delete everything before and including it. Only text after "start over" survives. If nothing follows, return empty string.

#### 3c. Handle "scratch that"

```python
SCRATCH_THAT = re.compile(r'[^.!?]*\bscratch\s+that\b[.!?]?\s*', re.IGNORECASE)
```

Delete the sentence containing "scratch that" and the preceding sentence. Operates on sentence boundaries (split on `.!?`).

Example: `"I need apples. Get oranges too. Scratch that."` -> `"I need apples."`

#### 3d. Remove filler words

```python
FILLER_PATTERN = re.compile(r'\b(um|uh|you\s+know)\b', re.IGNORECASE)
```

Simple removal. These words are never meaningful in dictation.

**Note:** Filler "like" is deliberately NOT removed. Cannot distinguish filler from verb without semantic understanding. Leave it for the LLM path when explicitly triggered.

#### 3e. Convert spoken punctuation to symbols

```python
SPOKEN_PUNCTUATION = {
    'comma': ',',
    'period': '.',
    'question mark': '?',
    'exclamation point': '!',
    'colon': ':',
    'semicolon': ';',
    'hyphen': '-',
    'dash': '-',
    'ellipsis': '...',
    'slash': '/',
    'apostrophe': "'",
    'single quote': "'",
    'quotation mark': '"',
    'double quote': '"',
    'open parenthesis': '(',
    'close parenthesis': ')',
    'percent sign': '%',
}
```

Use `re.sub(r'\b' + word + r'\b', symbol, text)` for each entry.

#### 3f. Handle "new line" and "new paragraph"

```python
text = re.sub(r'\bnew\s+paragraph\b[.!?]?\s*', '\n\n', text, flags=re.IGNORECASE)
text = re.sub(r'\bnew\s+line\b[.!?]?\s*', '\n', text, flags=re.IGNORECASE)
```

Order matters: match "new paragraph" before "new line" to avoid partial matches.

#### 3g. Convert number words to digits

Use `word2number` library to convert spans of number words:

```python
from word2number import w2n

NUMBER_WORDS = re.compile(
    r'\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|'
    r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|'
    r'eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|'
    r'eighty|ninety|hundred|thousand|million|billion)\b',
    re.IGNORECASE
)

def convert_number_words(text: str) -> str:
    """Find sequences of number words in text and convert to digits."""
    words = text.split()
    result = []
    i = 0

    while i < len(words):
        if NUMBER_WORDS.match(words[i]):
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
```

Also convert "percent" to `%`:
```python
text = re.sub(r'\bpercent\b', '%', text, flags=re.IGNORECASE)
```

#### 3h. Handle numbered lists

**Activation rules (BOTH must be true):**
1. Text contains `bullet` followed by a number (e.g., `bullet 1`, `bullet 2`)
2. Text contains `end list` somewhere after the bullet markers

**If `bullet N` is found but `end list` is NOT found, assume false positive — the word "bullet" was used naturally (e.g., "a bullet hit the wall"). Do NOT format as a list. Return text unchanged.**

This is a simple regex check — search for both patterns, and only proceed if both are present.

```python
BULLET_PATTERN = re.compile(r'\bbullet\s+(\d+)\b', re.IGNORECASE)
END_LIST = re.compile(r'\bend\s+list\b[.!?]?\s*', re.IGNORECASE)

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

    before = text[:first_bullet.start()].strip()
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
```

Example:
- Input: `"Here are my items. Bullet 1 apples. Bullet 2 bananas. Bullet 3 oranges. End list. That is all."`
- Output:
  ```
  Here are my items.
  1. Apples
  2. Bananas
  3. Oranges
  That is all.
  ```
- Input (false positive): `"The bullet 1 hit the wall"` — NO `end list` found, returned unchanged.

#### 3i. Final cleanup

```python
# Clean up extra spaces from removals
text = re.sub(r'\s{2,}', ' ', text).strip()

# Ensure first letter is capitalized (Parakeet usually does this,
# but our substitutions may break it)
if text and text[0].islower():
    text = text[0].upper() + text[1:]

# Ensure text ends with punctuation
if text and text[-1] not in '.?!\n':
    text += '.'
```

---

### Change 4: Modify `process_audio()` to Use New Pipeline

**Where:** `server.py`, `process_audio()` function starting at line 132.

**Current code:**
```python
async def process_audio(audio_bytes: bytes, filename: str) -> str:
    """Full pipeline: transcribe + clean."""
    t0 = time.perf_counter()
    raw_text = transcribe_audio(audio_bytes, filename)
    t1 = time.perf_counter()
    log.info(f"Transcription: {t1 - t0:.2f}s | raw: {raw_text[:100]}")

    if not raw_text.strip():
        log.info("Empty transcript -- skipping cleanup")
        return ""

    cleaned_text = await cleanup_with_ollama(raw_text)
    t2 = time.perf_counter()
    log.info(f"Cleanup: {t2 - t1:.2f}s | cleaned: {cleaned_text[:100]}")
    log.info(f"Total: {t2 - t0:.2f}s")
    return cleaned_text
```

**New code:**
```python
async def process_audio(audio_bytes: bytes, filename: str) -> str:
    """Full pipeline: transcribe + regex cleanup + optional LLM."""
    t0 = time.perf_counter()
    raw_text = transcribe_audio(audio_bytes, filename)
    t1 = time.perf_counter()
    log.info(f"Transcription: {t1 - t0:.2f}s | raw: {raw_text[:100]}")

    if not raw_text.strip():
        log.info("Empty transcript -- skipping cleanup")
        return ""

    # Check for explicit LLM trigger before regex cleanup
    use_llm, text = check_llm_trigger(raw_text)

    # Always run regex cleanup first
    cleaned_text = lightweight_cleanup(text)
    t2 = time.perf_counter()
    log.info(f"Regex cleanup: {t2 - t1:.4f}s | cleaned: {cleaned_text[:100]}")

    # If user said "post-process LLM", also run through LLM
    if use_llm:
        log.info("LLM trigger detected -- routing to LLM for semantic cleanup")
        cleaned_text = await cleanup_with_ollama(cleaned_text)
        t3 = time.perf_counter()
        log.info(f"LLM cleanup: {t3 - t2:.2f}s | final: {cleaned_text[:100]}")
        log.info(f"Total: {t3 - t0:.2f}s")
    else:
        log.info(f"Total: {t2 - t0:.2f}s")

    return cleaned_text
```

**Key design decision:** Regex runs FIRST, then LLM. This means the LLM receives already-cleaned text (numbers converted, fillers removed, etc.) and only needs to handle semantic tasks. This matches the new slimmed-down LLM prompt which says "Numbers, punctuation symbols, and filler words have already been handled."

---

## What Deliberately Does NOT Change

- **gui.py** -- No structural changes. Only the default `cleanup_prompt` in DEFAULTS dict is updated to match the new slimmed-down prompt.
- **config.json** -- The `cleanup_prompt` field is updated. No schema changes.
- **tray.py** -- No changes. It just sends audio to the server and receives text.
- **Endpoints** -- No API changes. `/asr` and `/v1/audio/transcriptions` behave identically from the client's perspective.
- **`cleanup_with_ollama()` function** -- Kept intact. Not auto-triggered, but available when "post-process LLM" is spoken.

---

## What Is Deliberately NOT Handled

| Feature | Why Not |
|---|---|
| Self-corrections (sorry, actually, I meant) in default path | False positive risk too high. "I'm sorry for the delay" would get corrupted. User invokes LLM explicitly when needed. |
| Filler "like" removal | Can't distinguish filler vs verb ("I like this" vs "it was like super") without semantics. Left for LLM path. |
| Natural restatements without trigger words | Requires fine-tuned LLM. Even Wispr Flow uses a fine-tuned Llama model on cloud GPUs for this. |
| Context-aware number formatting (currency, dates, measures) | `word2number` handles raw number conversion. Advanced formatting (e.g., "$123", "March 5th") would require NeMo ITN which has Windows compatibility concerns with `pynini`. Can revisit later. |

---

## New Dependency

Add to `requirements.txt`:
```
word2number
```

Install: `pip install word2number`

---

## Expected Performance

### Default Path (regex only, no "post-process LLM"):

| Input Length | Before (LLM always) | After (regex) | Saved |
|---|---|---|---|
| Short (~45 words) cold | ~7.5s | ~0.5s | **~7.0s** |
| Short (~45 words) warm | ~5.8s | ~0.5s | **~5.3s** |
| Medium (~84 words) warm | ~17.9s | ~0.5s | **~17.4s** |
| Long (~172 words) warm | ~21.5s | ~0.5s | **~21.0s** |

(The ~0.5s is Parakeet transcription time. Regex adds <5ms on top.)

### Explicit LLM Path (user says "post-process LLM"):

| Input Length | Before | After | Saved |
|---|---|---|---|
| Any length, cold | ~7-22s | ~5-20s | **~2s** (cold start eliminated by keep_alive: -1) |
| Any length, warm | ~5-20s | ~5-20s | **~0s** (LLM still runs, but prompt is shorter so slightly faster) |

### Estimated Usage Split

~95%+ of dictations will use the regex-only path. The LLM trigger is an explicit opt-in for edge cases.

---

## Testing Plan

### Regex Path Tests

| Input | Expected Output | Features Tested |
|---|---|---|
| `"Um I need twenty five dollars"` | `"I need 25 dollars."` | Filler removal + number conversion |
| `"the price is ten percent higher"` | `"The price is 10% higher."` | Number + percent |
| `"dear sir comma the answer is no period"` | `"Dear sir, the answer is no."` | Spoken punctuation |
| `"one hundred and thirty five"` | `"135."` | Complex number span |
| `"hello new line how are you"` | `"Hello\nHow are you."` | New line command (no pause/comma) |
| `"hello, new line, how are you"` | `"Hello\nHow are you."` | New line command (Parakeet added commas around pause) |
| `"first paragraph new paragraph second paragraph"` | `"First paragraph.\n\nSecond paragraph."` | New paragraph (no commas) |
| `"first paragraph, new paragraph, second paragraph"` | `"First paragraph.\n\nSecond paragraph."` | New paragraph (Parakeet added commas — must still work) |
| `"I need apples. No wait get oranges. Scratch that."` | `"I need apples."` | Scratch that (deletes preceding sentence) |
| `"I need apples. No wait get oranges, scratch that."` | `"I need apples."` | Scratch that (comma before command) |
| `"blah blah blah start over the real message"` | `"The real message."` | Start over |
| `"blah blah blah, start over, the real message"` | `"The real message."` | Start over (commas around command) |
| `"hello, new-line, how are you"` | `"Hello\nHow are you."` | New line with Parakeet hyphen |
| `"blah blah blah, start-over, the real message"` | `"The real message."` | Start over with Parakeet hyphen |
| `"I need apples. Get oranges, scratch-that."` | `"I need apples."` | Scratch that with Parakeet hyphen |
| `"is this correct question mark"` | `"Is this correct?"` | Multi-word spoken punctuation (question mark) |
| `"wow exclamation point that is great"` | `"Wow! That is great."` | Multi-word spoken punctuation (exclamation point) |
| `"he said double quote hello double quote"` | `"He said \"hello\"."` | Double quote spoken punctuation |
| `"it apostrophe s fine"` | `"It's fine."` | Single-word spoken punctuation (apostrophe) |
| `"use open parenthesis optional close parenthesis"` | `"Use (optional)."` | Parentheses spoken punctuation |
| `"Hello how are you"` | `"Hello how are you."` | Pass-through (only add period) |
| `""` | `""` | Empty input (existing logic) |

### Numbered List Tests

| Input | Expected Output | Features Tested |
|---|---|---|
| `"bullet 1 apples bullet 2 bananas bullet 3 oranges end list"` | `"1. Apples\n2. Bananas\n3. Oranges"` | Basic numbered list |
| `"Bullet 1 apples, bullet 2 bananas, bullet 3 oranges, end list."` | `"1. Apples\n2. Bananas\n3. Oranges"` | List with Parakeet commas between items |
| `"Here are my items. Bullet 1 apples. Bullet 2 bananas. End list. That is all."` | `"Here are my items.\n1. Apples\n2. Bananas\nThat is all."` | List with surrounding text |
| `"Bullet 1 buy groceries bullet 2 clean house bullet 3 call mom end list"` | `"1. Buy groceries\n2. Clean house\n3. Call mom"` | Multi-word list items |
| `"The bullet 1 hit the wall"` | `"The bullet 1 hit the wall."` | False positive: "bullet" used naturally, NO "end list" -> unchanged |
| `"A bullet proof vest"` | `"A bullet proof vest."` | False positive: "bullet" without a number -> unchanged |
| `"He dodged the bullet 3 times"` | `"He dodged the bullet 3 times."` | False positive: "bullet N" but no "end list" -> unchanged |

### LLM Trigger Tests

| Input | Expected Output | Features Tested |
|---|---|---|
| `"four sorry I meant two post-process LLM"` | `"2."` | Explicit LLM trigger + self-correction |
| `"I'm sorry for the delay post-process LLM"` | `"I'm sorry for the delay."` | LLM should keep natural "sorry" (new prompt) |
| `"I like this but like we should go post-process LLM"` | `"I like this, but we should go."` | Filler "like" removal by LLM |
| `"fix this for me, post-process-LLM."` | Routes to LLM with input `"Fix this for me."` | LLM trigger with Parakeet hyphen + comma |

### Edge Cases

| Input | Expected Output | Notes |
|---|---|---|
| `"I'm sorry for the delay"` | `"I'm sorry for the delay."` | Default path: NO LLM triggered. "Sorry" passes through naturally. |
| `"I actually think this is great"` | `"I actually think this is great."` | Default path: "actually" is NOT a trigger anymore. Kept as-is. |
| `"I need one apple"` | `"I need 1 apple."` | "one" converted to digit. Verify "I" is NOT converted. |
| `"a good idea"` | `"A good idea."` | Verify "a" is NOT converted to a number. |

---

## Summary of All Changes

| File | Change |
|---|---|
| `server.py` | Add `lightweight_cleanup()`, `check_llm_trigger()`, `convert_number_words()`, `format_numbered_list()` functions. Modify `process_audio()` to use regex-first pipeline with optional LLM. Add `"keep_alive": -1` to Ollama API call. Add `import re` and `from word2number import w2n`. |
| `config.json` | Update `cleanup_prompt` to the new slimmed-down LLM prompt (semantic tasks only). |
| `gui.py` | Update the `cleanup_prompt` in the DEFAULTS dict to match the new prompt. |
| `C:\Users\Anwesh Mohapatra\OneDrive\Desktop\handy-prompt.txt` | Replace entire contents with the new slimmed-down LLM prompt. This is a standalone copy of the prompt used for reference. |
| `requirements.txt` | Add `word2number`. |
| `tray.py` | No changes. |

---

## Order of Implementation

1. Add `word2number` to `requirements.txt` and install it.
2. Add all new functions to `server.py` (regex patterns, `lightweight_cleanup`, `check_llm_trigger`, `convert_number_words`, `format_numbered_list`).
3. Modify `process_audio()` in `server.py` to use the new pipeline.
4. Add `"keep_alive": -1` to the Ollama API call in `cleanup_with_ollama()`.
5. Update `cleanup_prompt` in `config.json`.
6. Update `cleanup_prompt` in `gui.py` DEFAULTS.
7. Update `C:\Users\Anwesh Mohapatra\OneDrive\Desktop\handy-prompt.txt` with the same new prompt.
8. Test with the test cases above.
