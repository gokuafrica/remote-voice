# CLAUDE.md — Project Instructions for AI Agents

This file is automatically loaded by Claude Code and other AI agents working on this repo. Follow these rules for every change.

## Propose Before Committing

Do NOT commit code changes without the user reviewing the approach first. When investigating an issue or considering a design change, present your findings and proposed solution to the user before writing code. This is especially important for changes that affect the pipeline's core behavior (prompt structure, cleanup logic, LLM interaction). Run experiments, share results, and let the user decide the direction.

## The Rule: Code, Tests, Docs — Always Together

Every change to the pipeline must update all three in the same commit:

1. **Code** — the feature or fix in `server.py`, `gui.py`, etc.
2. **Tests** — add or update tests in `tests.py` to cover the change
3. **Docs** — update `README.md` to reflect the new behavior

Do not merge or commit a change that updates code without corresponding test and documentation updates. If a regex pattern changes, the test for that pattern must change. If a voice command is added, the README must list it.

## Project Architecture

- `server.py` — FastAPI server. Contains the full pipeline: transcription (Parakeet V2) → regex cleanup (`lightweight_cleanup()`) → optional LLM (`cleanup_with_ollama()`). All regex patterns and voice command logic live here.
- `gui.py` — Tkinter GUI for config and server lifecycle. If `config.json` fields change, the `DEFAULTS` dict in gui.py must match.
- `tray.py` — System tray app for PC recording. Rarely changes.
- `config.json` — Runtime config. The `cleanup_prompt` field is the LLM prompt — if it changes, also update `handy-prompt.txt` and `gui.py` DEFAULTS.
- `handy-prompt.txt` — Standalone copy of the LLM cleanup prompt. Must stay in sync with `config.json`.
- `tests.py` — Test suite. Two parts: regex (deterministic, exact match) and LLM (property-based, requires Ollama).
- `README.md` — Single source of truth for documentation. No separate SPEC or design docs.

## How the Pipeline Works

```
Audio → Parakeet V2 (transcription) → apply_pronunciation_fixes() → check_llm_trigger() → lightweight_cleanup() → [optional] cleanup_with_ollama() → return text
```

- `apply_pronunciation_fixes()` replaces known mispronunciations (from `config.json` `pronunciation_fixes`) before any other processing. Uses `[\s-]+` between words and `\b` word boundaries. Does NOT consume surrounding punctuation — downstream command regexes handle that.
- `check_llm_trigger()` detects "deep clean" at end of text. Returns `(trigger: bool, text: str, instruction: str)`.
- `lightweight_cleanup()` runs all regex in this order: start over, fillers, spoken punctuation, period cleanup, duplicate comma collapse, quote spacing, new paragraph/line, scratch that, number conversion, numbered lists, final capitalization/spacing. Note: new paragraph/line runs BEFORE scratch that so that scratch that respects line/paragraph boundaries (stops at `\n`).
- `cleanup_with_ollama()` only runs when the user explicitly says "deep clean". Accepts an optional custom instruction via "deep clean plus <instruction>".

## Regex Pattern Conventions

All multi-word voice commands and spoken punctuation MUST handle Parakeet's behavior:

1. **Commas/periods around commands** — Parakeet inserts punctuation at natural pauses. Use `[,.]?\s*` before and `[,.]?` or `[,.]?\s*` after command phrases.
2. **Hyphens between words** — Parakeet may hyphenate multi-word phrases. Use `[\s-]+` instead of `\s+` between words in a command.

Example: `new line` → `r'[,.]?\s*\bnew[\s-]+line\b[,.]?\s*'`

## Testing

Run tests before committing:

```bash
python tests.py              # Full suite (regex + LLM)
python tests.py --regex-only # Fast, no Ollama needed
python tests.py --llm-only   # LLM tests only
```

- Regex tests use exact string matching — they are deterministic.
- LLM tests use property checks (must_contain / must_not_contain) because LLM output varies between runs.
- If Ollama is not available, LLM tests skip gracefully.

### Adding Tests

- For regex changes: add exact-match tests via `test()` in the appropriate section.
- For trigger changes: add `test_trigger()` calls with `(input, expected_trigger, expected_text, expected_instruction, label)`.
- For LLM behavior: add `test_llm()` calls with property assertions, not exact matches.

## Things That Should NOT Change Without Good Reason

- **Parakeet's punctuation is trusted** — do not force trailing periods.
- **"like" is NOT removed by regex** — it requires semantic understanding (LLM only).
- **Self-corrections are NOT handled by regex** — false positive risk too high. LLM only.
- **`cleanup_with_ollama()` is never called automatically** — only via explicit "deep clean" trigger.
- **`keep_alive: -1`** in the Ollama API call — keeps the model in VRAM to eliminate cold starts.

## Config Sync Points

These must stay in sync when the LLM prompt changes:
1. `config.json` → `cleanup_prompt` field
2. `gui.py` → `DEFAULTS["cleanup_prompt"]`
3. `handy-prompt.txt` → standalone copy

When `pronunciation_fixes` changes:
1. `config.json` → `pronunciation_fixes` field
2. `gui.py` → `DEFAULTS["pronunciation_fixes"]`
3. `server.py` → `PRONUNCIATION_FIX_PATTERNS` (compiled at module load from config)
