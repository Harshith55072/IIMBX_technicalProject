"""Live end-to-end check of the running server (spawns uvicorn, hits it over HTTP, stops it).

    venv\\Scripts\\python scripts/serve_check.py          # health + stats only (no Gemini quota)
    venv\\Scripts\\python scripts/serve_check.py --real   # + 2 real chat calls (1 Gemini call, 1 cache hit)
"""
import subprocess
import sys
import time

import httpx

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
Q = "What is the minimum score needed to pass the course?"


def main() -> int:
    real = "--real" in sys.argv
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"]
    )
    try:
        for _ in range(40):
            try:
                if httpx.get(f"{BASE}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            print("server did not start")
            return 1
        print("health:", httpx.get(f"{BASE}/health").json())
        print("stats :", httpx.get(f"{BASE}/api/stats").json())
        if real:
            a = httpx.post(f"{BASE}/api/chat", json={"message": Q}, timeout=60)
            print(a.status_code, a.json()["answer"][:200], a.json()["sources"], "cached=", a.json()["cached"])
            b = httpx.post(f"{BASE}/api/chat", json={"message": Q, "session_id": a.json()["session_id"]}, timeout=60)
            print(b.status_code, "cached=", b.json()["cached"], b.json()["via"])
            print("stats :", httpx.get(f"{BASE}/api/stats").json())
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
