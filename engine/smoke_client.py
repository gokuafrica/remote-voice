"""
Smoke-test client for engine/engine.py — talks to it exactly like the
Electron app will: spawn as subprocess, newline-delimited JSON over stdio.

Run:  python engine/smoke_client.py
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.py")
SEED_CONFIG = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.json"))
WAV = os.path.abspath(os.path.join(os.path.dirname(__file__), "testdata", "hello.wav"))


def pcm_request_payload():
    with wave.open(WAV, "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
            raise AssertionError("hello.wav must be 16 kHz mono signed 16-bit PCM")
        pcm = wav.readframes(wav.getnframes())
    return base64.b64encode(pcm).decode("ascii")


def main() -> int:
    # set_fixes persists into the config file — work on a copy so the
    # seeded config.json stays pristine.
    tmpdir = tempfile.mkdtemp(prefix="rv-smoke-")
    CONFIG = os.path.join(tmpdir, "config.json")
    shutil.copyfile(SEED_CONFIG, CONFIG)

    proc = subprocess.Popen(
        [sys.executable, "-u", ENGINE, "--config", CONFIG],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("engine died")
        return json.loads(line.decode("utf-8"))

    ok = True

    t0 = time.perf_counter()
    send({"id": 1, "op": "ping"})
    ping = recv()
    print(f"ping: {ping}  (waited {time.perf_counter() - t0:.2f}s for startup)")
    if not (ping.get("ok") and ping.get("ready")):
        print("FAIL: engine not ready")
        ok = False

    pcm_b64 = pcm_request_payload()
    send({"id": 2, "op": "transcribe", "pcm_s16le_b64": pcm_b64, "sample_rate": 16000})
    r = recv()
    print(f"transcribe: {json.dumps(r, ensure_ascii=False)}")
    if not r.get("ok"):
        ok = False
    else:
        text = r.get("text", "")
        if "twenty five" in text or "25" not in text:
            print("WARN: number conversion may not have applied")

    send({"id": 3, "op": "set_fixes", "fixes": {"world": "planet"}})
    fixes = recv()
    print(f"set_fixes: {fixes}")

    send({"id": 4, "op": "transcribe", "pcm_s16le_b64": pcm_b64, "sample_rate": 16000})
    r2 = recv()
    print(f"transcribe after fixes: {json.dumps(r2, ensure_ascii=False)}")
    if r2.get("ok") and "planet" in r2.get("text", ""):
        print("PASS: hot-reloaded fixes changed transcription output")
    else:
        print("FAIL: fixes did not apply")
        ok = False

    # restore empty fixes
    send({"id": 5, "op": "set_fixes", "fixes": {}})
    print(f"restore: {recv()}")

    send({"id": 6, "op": "shutdown"})
    proc.stdin.close()
    code = proc.wait(timeout=10)
    print(f"engine exit code: {code}")
    if code != 0:
        ok = False

    print("SMOKE OK" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
