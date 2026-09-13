"""
Engine pipeline tests — ported from master/tests.py (deterministic regex parts).

Run:  python engine/engine_tests.py
"""

import asyncio
import sys
import os
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine
from engine import (
    lightweight_cleanup,
    check_llm_trigger,
    cleanup_with_ollama,
    compile_pronunciation_fixes,
    apply_pronunciation_fixes,
    OLLAMA_URL,
    OLLAMA_MODEL,
)

passed = 0
failed = 0
section_passed = 0
section_failed = 0


def section(name):
    global section_passed, section_failed
    if section_passed or section_failed:
        print(f"  [{section_passed} passed, {section_failed} failed]\n")
    section_passed = 0
    section_failed = 0
    print(f"=== {name} ===")


def test(input_text, expected, label=""):
    """Exact-match test for regex cleanup."""
    global passed, failed, section_passed, section_failed
    result = lightweight_cleanup(input_text)
    if result == expected:
        passed += 1
        section_passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        section_failed += 1
        print(f"  FAIL: {label}")
        print(f"    Input:    {input_text!r}")
        print(f"    Expected: {expected!r}")
        print(f"    Got:      {result!r}")


def test_trigger(input_text, expected_trigger, expected_text,
                 expected_instruction="", label=""):
    """Test the deep format trigger detection."""
    global passed, failed, section_passed, section_failed
    trigger, text, instruction = check_llm_trigger(input_text)
    if (trigger == expected_trigger and text == expected_text
            and instruction == expected_instruction):
        passed += 1
        section_passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        section_failed += 1
        print(f"  FAIL: {label}")
        print(f"    Input:    {input_text!r}")
        print(f"    Expected: trigger={expected_trigger}, text={expected_text!r}, instruction={expected_instruction!r}")
        print(f"    Got:      trigger={trigger}, text={text!r}, instruction={instruction!r}")


def test_voice_model_providers():
    global passed, failed, section_passed, section_failed
    sentinel = object()
    with mock.patch.object(engine.onnx_asr, "load_model", return_value=sentinel) as loader:
        result = engine.load_voice_model()
        ok = result is sentinel and loader.call_args.kwargs["providers"] == [
            "CUDAExecutionProvider", "CPUExecutionProvider"
        ]
        label = "GPU load excludes unbundled TensorRT"
        if ok:
            passed += 1
            section_passed += 1
            print(f"  PASS: {label}")
        else:
            failed += 1
            section_failed += 1
            print(f"  FAIL: {label}")

    with mock.patch.object(engine.onnx_asr, "load_model", side_effect=[RuntimeError("GPU unavailable"), sentinel]) as loader:
        result = engine.load_voice_model()
        ok = result is sentinel and loader.call_args_list[1].kwargs["providers"] == ["CPUExecutionProvider"]
        label = "GPU initialization failure falls back to CPU"
        if ok:
            passed += 1
            section_passed += 1
            print(f"  PASS: {label}")
        else:
            failed += 1
            section_failed += 1
            print(f"  FAIL: {label}")


def test_headless_standard_streams():
    global passed, failed, section_passed, section_failed
    original_stdout, original_stderr = sys.stdout, sys.stderr
    created = []
    ok = False
    try:
        sys.stdout = None
        sys.stderr = None
        engine._ensure_standard_streams()
        created = [sys.stdout, sys.stderr]
        ok = all(stream is not None and not stream.closed for stream in created)
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
        for stream in created:
            stream.close()

    label = "Headless pythonw launch supplies missing streams"
    if ok:
        passed += 1
        section_passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        section_failed += 1
        print(f"  FAIL: {label}")


section("Voice Model Providers")
test_voice_model_providers()
test_headless_standard_streams()

# -------------------------------------------------------------------
section("Filler Removal")
test("Um I need something.", "I need something.", "Remove 'um'")
test("I uh need something.", "I need something.", "Remove 'uh'")
test("I you know need something.", "I need something.", "Remove 'you know'")
test("Um, uh, you know, hello.", "Hello.", "Remove multiple fillers")
test("I like this.", "I like this.", "'like' is NOT removed (could be verb)")

# -------------------------------------------------------------------
section("Number Conversion")
test("I need twenty five dollars.", "I need 25 dollars.", "Basic number")
test("one hundred and thirty five.", "135.", "Complex number span")
test("the price is ten percent higher.", "The price is 10% higher.", "Number + percent")
test("I need one apple.", "I need 1 apple.", "'one' converted")
test("a good idea.", "A good idea.", "'a' NOT converted to number")
test("I am fine.", "I am fine.", "'I' NOT converted to number")

# -------------------------------------------------------------------
section("Spoken Punctuation - Single-Word")
test("dear sir comma the answer is no.", "Dear sir, the answer is no.", "comma")
test("that is all period", "That is all.", "period")
test("use this colon value.", "Use this: value.", "colon")
test("first semicolon second.", "First; second.", "semicolon")
test("wait ellipsis never mind.", "Wait... Never mind.", "ellipsis")
test("it apostrophe s fine.", "It's fine.", "apostrophe")
test("use this hyphen that.", "Use this-that.", "hyphen")
test("use this dash that.", "Use this-that.", "dash")
test("a slash b.", "A/b.", "slash")

# -------------------------------------------------------------------
section("Spoken Punctuation - Multi-Word")
test("is this correct question mark", "Is this correct?", "question mark")
test("wow exclamation point that is great.", "Wow! That is great.", "exclamation point")
test('he said double quote hello double quote', 'He said "hello"', "double quote")
test("he said quotation mark hello quotation mark", 'He said "hello"', "quotation mark")
test("use open parenthesis optional close parenthesis", "Use (optional)", "parentheses")
test("fifty percent sign done.", "Fifty% done.", "percent sign (with number)")

# -------------------------------------------------------------------
section("Period Removal Before Manual Punctuation")
test("Dear sir. Comma the answer is no.", "Dear sir, the answer is no.", "Period before comma")
test("Is this correct. Question mark", "Is this correct?", "Period before question mark")
test("Wow. Exclamation point that is great.", "Wow! That is great.", "Period before exclamation point")
test("It. Apostrophe s fine.", "It's fine.", "Period before apostrophe")
test("Use this. Colon value.", "Use this: value.", "Period before colon")
test("First. Semicolon second.", "First; second.", "Period before semicolon")
test("Check this. Open parenthesis note. Close parenthesis done.", "Check this (note) done.", "Period before parentheses")

# -------------------------------------------------------------------
section("Duplicate Comma Collapse")
test("Dear sir, comma the answer is no.", "Dear sir, the answer is no.",
     "Parakeet comma + spoken comma collapsed")
test("items,, , and more.", "Items, and more.",
     "Triple comma collapsed")
test("wait ellipsis never mind.", "Wait... Never mind.",
     "Ellipsis NOT collapsed (only commas)")

# -------------------------------------------------------------------
section("No Forced Trailing Period")
test("Hello how are you", "Hello how are you", "No period added when Parakeet omits it")
test("Hello how are you.", "Hello how are you.", "Period preserved when Parakeet adds it")
test("Hello how are you?", "Hello how are you?", "Question mark preserved")
test("Hello how are you!", "Hello how are you!", "Exclamation preserved")

# -------------------------------------------------------------------
section("New Line / New Paragraph")
test("hello new line how are you", "Hello\nHow are you", "New line (no commas)")
test("hello, new line, how are you", "Hello,\nHow are you", "New line (Parakeet commas)")
test("hello, new-line, how are you", "Hello,\nHow are you", "New line (Parakeet hyphen)")
test("hello, new, line, how are you", "Hello,\nHow are you", "New line (Parakeet comma inside command)")
test("first paragraph new paragraph second paragraph",
     "First paragraph.\n\nSecond paragraph", "New paragraph (no commas)")
test("first paragraph, new paragraph, second paragraph",
     "First paragraph.\n\nSecond paragraph", "New paragraph (Parakeet commas)")
test("first paragraph. New paragraph, second paragraph",
     "First paragraph.\n\nSecond paragraph", "New paragraph (period before)")
test("first sentence. New line second sentence",
     "First sentence.\nSecond sentence", "New line preserves period before")
test("first sentence, new line, second sentence",
     "First sentence,\nSecond sentence", "New line preserves Parakeet comma")

# -------------------------------------------------------------------
section("Scratch That")
test("I need apples. No wait get oranges. Scratch that.",
     "I need apples.", "Deletes preceding sentence")
test("I need apples. No wait get oranges, scratch that.",
     "I need apples.", "Comma before command")
test("I need apples. Get oranges, scratch-that.",
     "I need apples.", "Parakeet hyphen")
test("I need apples. Get oranges, scratch, that.",
     "I need apples.", "Parakeet comma inside command")
test("first line. New line second line. Scratch that third line.",
     "First line.\nThird line.", "Respects newline boundary")
test("first para. New paragraph second para. Scratch that third para.",
     "First para.\n\nThird para.", "Respects paragraph boundary")
test("hello. New line your true scratch that yours truly",
     "Hello.\nYours truly", "Preserves newline before replacement")

# -------------------------------------------------------------------
section("Start Over")
test("blah blah blah start over the real message",
     "The real message", "Keeps text after command")
test("blah blah blah, start over, the real message",
     "The real message", "Parakeet commas")
test("blah blah blah, start-over, the real message",
     "The real message", "Parakeet hyphen")

# -------------------------------------------------------------------
section("Numbered Lists")
test("bullet 1 apples bullet 2 bananas bullet 3 oranges end list",
     "1. Apples\n2. Bananas\n3. Oranges", "Basic numbered list")
test("Bullet 1 apples, bullet 2 bananas, bullet 3 oranges, end list.",
     "1. Apples\n2. Bananas\n3. Oranges", "List with Parakeet commas")
test("Here are my items. Bullet 1 apples. Bullet 2 bananas. End list. That is all.",
     "Here are my items.\n1. Apples\n2. Bananas\nThat is all.", "List with surrounding text")
test("Bullet 1 buy groceries bullet 2 clean house bullet 3 call mom end list",
     "1. Buy groceries\n2. Clean house\n3. Call mom", "Multi-word list items")
test("The bullet 1 hit the wall",
     "The bullet 1 hit the wall", "False positive: no 'end list'")
test("A bullet proof vest.",
     "A bullet proof vest.", "False positive: no number")
test("He dodged the bullet 3 times.",
     "He dodged the bullet 3 times.", "False positive: 'bullet N' but no 'end list'")

# -------------------------------------------------------------------
section("Emoji Substitution")
# Basic triggers
test("thumbs up", "👍", "thumbs up — standalone")
test("Great work thumbs up", "Great work 👍", "thumbs up — end of phrase")
test("smiley face", "😊", "smiley face — standalone")
test("That was funny laughing face", "That was funny 😂", "laughing face")
test("heart emoji", "❤️", "heart emoji — ambiguous word needs suffix")
test("fire emoji", "🔥", "fire emoji — ambiguous word needs suffix")
test("hundred emoji", "💯", "hundred emoji — runs before number conversion")

# facepalm: one-word and two-word variants
test("facepalm", "🤦", "facepalm — one word")
test("face palm", "🤦", "face palm — two words")
test("face-palm", "🤦", "face-palm — Parakeet hyphen in command")

# Parakeet punctuation between command words
test("thumbs, up", "👍", "thumbs, up — Parakeet comma between words")
test("smiley. face", "😊", "smiley. face — Parakeet period between words")

# Parakeet punctuation before the command
test("Great work, thumbs up", "Great work 👍", "leading Parakeet comma before command")
test("Great work. Thumbs up", "Great work 👍", "leading Parakeet period before command")

# Multiple emojis in one phrase
test("thumbs up and smiley face", "👍 and 😊", "multiple emojis in one phrase")

# False positives — ambiguous standalone words must NOT convert
test("I love her heart.", "I love her heart.", "standalone 'heart' not converted")
test("The fire was huge.", "The fire was huge.", "standalone 'fire' not converted")
test("She is a star.", "She is a star.", "standalone 'star' not converted")

# -------------------------------------------------------------------
section("Deep Format Trigger Detection")
test_trigger("hello world deep format", True, "hello world",
             label="Basic trigger")
test_trigger("hello world, deep format.", True, "hello world",
             label="Trigger with Parakeet comma + period")
test_trigger("hello world, deep-format.", True, "hello world",
             label="Trigger with Parakeet hyphen")
test_trigger("hello world, deep, format.", True, "hello world",
             label="Trigger with Parakeet comma inside command")
test_trigger("hello world. Deep format", True, "hello world",
             label="Trigger with period before")
test_trigger("hello world", False, "hello world",
             label="No trigger present")

# -------------------------------------------------------------------
section("Deep Format Custom Instruction")
test_trigger("two plus two is five deep format check the math",
             True, "two plus two is five", "format: check the math",
             label="Trigger with instruction")
test_trigger("some text, deep format make it formal.",
             True, "some text", "format: make it formal",
             label="Instruction with Parakeet comma + period")
test_trigger("some text, deep-format fix grammar.",
             True, "some text", "format: fix grammar",
             label="Instruction with Parakeet hyphen")
test_trigger("some text, deep, format. Format this like a viral tweet.",
             True, "some text", "format: Format this like a viral tweet",
             label="Instruction with Parakeet commas inside command")
test_trigger("hello deep format verify the dates and names",
             True, "hello", "format: verify the dates and names",
             label="Longer instruction")
test_trigger("hello deep format", True, "hello", "",
             label="No instruction — empty")
test_trigger("100 minus 10 is 9000. Deep format? Check the math.",
             True, "100 minus 10 is 9000", "format: Check the math",
             label="Parakeet question mark after trigger")
test_trigger("Alexander the Great died in India. Deep format: check historical facts.",
             True, "Alexander the Great died in India", "format: check historical facts",
             label="Parakeet colon after trigger")

# -------------------------------------------------------------------
section("Pronunciation Fixes")

# Inject test fixes, then restore after
_original_patterns = engine.PRONUNCIATION_FIX_PATTERNS
engine.PRONUNCIATION_FIX_PATTERNS = compile_pronunciation_fixes({
    "new lion": "new line",
})


def test_fix(input_text, expected, label=""):
    """Test pronunciation fix + lightweight_cleanup combined."""
    global passed, failed, section_passed, section_failed
    fixed = apply_pronunciation_fixes(input_text)
    result = lightweight_cleanup(fixed)
    if result == expected:
        passed += 1
        section_passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        section_failed += 1
        print(f"  FAIL: {label}")
        print(f"    Input:    {input_text!r}")
        print(f"    After fix:{fixed!r}")
        print(f"    Expected: {expected!r}")
        print(f"    Got:      {result!r}")


def test_sub(input_text, expected, label=""):
    """Test pronunciation fix substitution only (no cleanup)."""
    global passed, failed, section_passed, section_failed
    result = apply_pronunciation_fixes(input_text)
    if result == expected:
        passed += 1
        section_passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        section_failed += 1
        print(f"  FAIL: {label}")
        print(f"    Input:    {input_text!r}")
        print(f"    Expected: {expected!r}")
        print(f"    Got:      {result!r}")


# Substitution only (no cleanup)
test_sub("hello new lion world", "hello new line world",
         "Substitution only: new lion -> new line")
test_sub("hello new-lion world", "hello new line world",
         "Substitution with Parakeet hyphen: new-lion -> new line")

# Full pipeline: substitution + cleanup
test_fix("hello new lion world", "Hello\nWorld",
         "Full pipeline: new lion triggers line break")
test_fix("hello, new lion, world", "Hello,\nWorld",
         "Full pipeline with Parakeet commas")
test_fix("hello, new-lion, world", "Hello,\nWorld",
         "Full pipeline with Parakeet hyphen")
test_fix("first new lion second new lion third", "First\nSecond\nThird",
         "Multiple occurrences both replaced")

# Original command still works
test_fix("hello new line world", "Hello\nWorld",
         "Original 'new line' command unaffected")

# No fixes configured
engine.PRONUNCIATION_FIX_PATTERNS = compile_pronunciation_fixes({})
test_sub("hello new lion world", "hello new lion world",
         "Empty fixes dict: no substitution")
engine.PRONUNCIATION_FIX_PATTERNS = _original_patterns

# -------------------------------------------------------------------
section("Edge Cases")
test("I'm sorry for the delay.", "I'm sorry for the delay.",
     "Natural 'sorry' passes through (no LLM)")
test("I actually think this is great.", "I actually think this is great.",
     "Natural 'actually' passes through (no LLM)")
test("", "", "Empty input")
test("   ", "", "Whitespace-only input")
test("Hello.", "Hello.", "Single word with period")

# -------------------------------------------------------------------
section("FFmpeg Lookup")
result = engine._find_ffmpeg()
if isinstance(result, str) and result:
    passed += 1
    section_passed += 1
    print(f"  PASS: falls back to PATH/shutil.which when no bundled ffmpeg ({result})")
else:
    failed += 1
    section_failed += 1
    print(f"  FAIL: expected non-empty fallback path, got {result!r}")

# Print final section count
if section_passed or section_failed:
    print(f"  [{section_passed} passed, {section_failed} failed]\n")
section_passed = 0
section_failed = 0


# =======================================================================
# Summary
# =======================================================================
print("=" * 60)
print("SUMMARY")
print("=" * 60)
if failed == 0:
    print(f"ALL {passed} TESTS PASSED")
else:
    print(f"{passed} passed, {failed} FAILED out of {passed + failed}")
sys.exit(1 if failed else 0)
