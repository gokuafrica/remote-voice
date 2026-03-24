"""
Test suite for Remote Voice pipeline.

Part 1 — Regex tests: deterministic, no external dependencies.
Part 2 — LLM tests: require Ollama running with the configured model.
                     These simulate the full "deep format" path (regex + LLM)
                     and verify the LLM handles semantic tasks correctly.

Run all:        python tests.py
Regex only:     python tests.py --regex-only
LLM only:       python tests.py --llm-only
"""
import asyncio
import sys

sys.path.insert(0, '.')
from server import (
    lightweight_cleanup,
    check_llm_trigger,
    cleanup_with_ollama,
    compile_pronunciation_fixes,
    apply_pronunciation_fixes,
    OLLAMA_URL,
    OLLAMA_MODEL,
)
import server

passed = 0
failed = 0
skipped = 0
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


async def deep_format(input_text):
    """Simulate the full deep format path: trigger detection -> regex -> LLM."""
    _, stripped, instruction = check_llm_trigger(input_text)
    regex_cleaned = lightweight_cleanup(stripped)
    llm_result = await cleanup_with_ollama(regex_cleaned, instruction)
    return llm_result


def test_llm(input_text, must_contain=None, must_not_contain=None,
             exact=None, label=""):
    """Test the full deep format pipeline (regex + LLM).

    LLM output is non-deterministic, so we check properties:
      must_contain:     list of strings that MUST appear in the output
      must_not_contain: list of strings that must NOT appear in the output
      exact:            if set, require exact match (for simple cases)
    """
    global passed, failed, section_passed, section_failed
    must_contain = must_contain or []
    must_not_contain = must_not_contain or []

    result = asyncio.run(deep_format(input_text))
    result_lower = result.lower()

    errors = []
    if exact is not None and result.rstrip('.') != exact.rstrip('.'):
        errors.append(f"Expected exact: {exact!r}, got: {result!r}")
    for s in must_contain:
        if s.lower() not in result_lower:
            errors.append(f"Missing: {s!r}")
    for s in must_not_contain:
        if s.lower() in result_lower:
            errors.append(f"Should not contain: {s!r}")

    if not errors:
        passed += 1
        section_passed += 1
        print(f"  PASS: {label}")
        print(f"    LLM returned: {result!r}")
    else:
        failed += 1
        section_failed += 1
        print(f"  FAIL: {label}")
        print(f"    Input:      {input_text!r}")
        print(f"    LLM output: {result!r}")
        for e in errors:
            print(f"    Error: {e}")


def check_ollama_available():
    """Check if Ollama is running and the model is available."""
    import urllib.request
    import json
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            if OLLAMA_MODEL in models:
                return True
            print(f"  Model {OLLAMA_MODEL} not found. Available: {models}")
            return False
    except Exception as e:
        print(f"  Ollama not reachable at {OLLAMA_URL}: {e}")
        return False


# =======================================================================
# Parse args
# =======================================================================
run_regex = "--llm-only" not in sys.argv
run_llm = "--regex-only" not in sys.argv


# =======================================================================
# PART 1: REGEX TESTS (deterministic)
# =======================================================================
if run_regex:
    print("\n" + "=" * 60)
    print("PART 1: REGEX PIPELINE TESTS")
    print("=" * 60 + "\n")

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

    # -------------------------------------------------------------------
    section("Pronunciation Fixes")

    # Inject test fixes, then restore after
    _original_patterns = server.PRONUNCIATION_FIX_PATTERNS
    server.PRONUNCIATION_FIX_PATTERNS = compile_pronunciation_fixes({
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
    server.PRONUNCIATION_FIX_PATTERNS = compile_pronunciation_fixes({})
    test_sub("hello new lion world", "hello new lion world",
             "Empty fixes dict: no substitution")
    server.PRONUNCIATION_FIX_PATTERNS = _original_patterns

    # -------------------------------------------------------------------
    section("Edge Cases")
    test("I'm sorry for the delay.", "I'm sorry for the delay.",
         "Natural 'sorry' passes through (no LLM)")
    test("I actually think this is great.", "I actually think this is great.",
         "Natural 'actually' passes through (no LLM)")
    test("", "", "Empty input")
    test("   ", "", "Whitespace-only input")
    test("Hello.", "Hello.", "Single word with period")

    # Print final section count
    if section_passed or section_failed:
        print(f"  [{section_passed} passed, {section_failed} failed]\n")
    section_passed = 0
    section_failed = 0


# =======================================================================
# PART 2: LLM TESTS (require Ollama)
# =======================================================================
if run_llm:
    print("\n" + "=" * 60)
    print("PART 2: LLM PIPELINE TESTS (deep format)")
    print(f"  Model: {OLLAMA_MODEL} @ {OLLAMA_URL}")
    print("=" * 60 + "\n")

    if not check_ollama_available():
        print("\n  SKIPPING LLM tests — Ollama not available.\n")
        skipped_llm = True
    else:
        skipped_llm = False

        # ---------------------------------------------------------------
        section("Self-Corrections")
        test_llm(
            "1 + 1 is 4, sorry I meant 2. deep format",
            must_contain=["2"],
            must_not_contain=["4", "sorry", "meant"],
            label="Number correction with 'sorry I meant'",
        )
        test_llm(
            "Send it to John, no wait, Mike. deep format",
            must_contain=["Mike"],
            must_not_contain=["John", "no wait"],
            label="Name correction with 'no wait'",
        )
        test_llm(
            "The meeting is Monday, actually Tuesday. deep format",
            must_contain=["Tuesday"],
            must_not_contain=["Monday"],
            label="Correction with 'actually'",
        )
        test_llm(
            "I need the red one, I mean the blue one. deep format",
            must_contain=["blue"],
            must_not_contain=["red", "I mean"],
            label="Correction with 'I mean'",
        )

        # ---------------------------------------------------------------
        section("Natural Usage Preserved (Not Corrections)")
        test_llm(
            "I'm sorry for the delay. deep format",
            must_contain=["sorry", "delay"],
            label="Natural 'sorry' kept intact",
        )
        test_llm(
            "I actually think this is great. deep format",
            must_contain=["great"],
            must_not_contain=["sorry", "meant"],
            label="Natural 'actually' — meaning preserved",
        )

        # ---------------------------------------------------------------
        section("Filler 'like' Removal")
        test_llm(
            "I like this but like we should go. deep format",
            must_contain=["like this"],
            must_not_contain=["but like"],
            label="Filler 'like' removed, verb 'like' kept",
        )
        test_llm(
            "It was like super hard to do. deep format",
            must_not_contain=["like"],
            must_contain=["super hard"],
            label="Filler 'like' removed from sentence",
        )
        test_llm(
            "I like pizza. deep format",
            must_contain=["like pizza"],
            label="Verb 'like' preserved",
        )

        # ---------------------------------------------------------------
        section("Restatements")
        test_llm(
            "We should go to the park, or rather, let's go to the beach. deep format",
            must_contain=["beach"],
            must_not_contain=["park", "rather"],
            label="Restatement keeps final version",
        )

        # ---------------------------------------------------------------
        section("Regex + LLM Combined")
        test_llm(
            "Um I need like twenty five no wait thirty dollars. deep format",
            must_contain=["30"],
            must_not_contain=["um", "25", "no wait"],
            label="Fillers removed by regex, correction by LLM, numbers converted",
        )

        # ---------------------------------------------------------------
        section("Deep Format Custom Instruction")
        test_llm(
            "2 + 2 is 5. deep format check the math",
            must_not_contain=["2 + 2 is 5"],
            label="Math check instruction",
        )
        test_llm(
            "The Eiffel Tower is in London. deep format check the facts",
            must_contain=["Paris"],
            must_not_contain=["London"],
            label="Fact check instruction",
        )
        test_llm(
            "hey can u come 2morrow. deep format make it formal",
            must_not_contain=["hey"],
            label="Formality instruction",
        )

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
    extra = ""
    if run_llm and skipped_llm:
        extra = " (LLM tests skipped — Ollama not available)"
    print(f"ALL {passed} TESTS PASSED{extra}")
else:
    print(f"{passed} passed, {failed} FAILED out of {passed + failed}")
sys.exit(1 if failed else 0)
