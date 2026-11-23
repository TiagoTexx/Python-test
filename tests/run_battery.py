import re
import subprocess
import sys
import time
from collections import Counter

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
TEST = "tests/test_burst_load.py::test_burst_traffic_idempotency_and_event_loop_health"

results = []
for i in range(1, N + 1):
    t0 = time.perf_counter()
    p = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-x", "-q", "-s", "-p", "no:cacheprovider"],
        capture_output=True, text=True,
    )
    out = p.stdout + p.stderr
    dur = time.perf_counter() - t0

    if p.returncode == 0:
        reason, value = "PASS", None
    elif m := re.search(r"Ingest p95 latency too high: ([\d.]+)ms", out):
        reason, value = "FAIL p95", float(m.group(1))
    elif m := re.search(r"Max /health latency: ([\d.]+)ms", out):
        reason, value = "FAIL health", float(m.group(1))
    elif "Failed responses" in out:
        reason, value = "FAIL status codes", None
    elif "did not start" in out:
        reason, value = "ERROR server start", None
    else:
        reason, value = "FAIL other", None

    results.append((reason, value))
    extra = f" ({value:.0f} ms)" if value is not None else ""
    print(f"run {i:>2}/{N}: {reason}{extra}  [{dur:.1f}s]", flush=True)
    time.sleep(1.0)  # deixa as portas libertarem

print("\n=== RESUMO ===")
for reason, n in Counter(r for r, _ in results).most_common():
    print(f"{reason:<20} {n}/{N}")

p95s = sorted(v for r, v in results if r == "FAIL p95" and v)
if p95s:
    print(f"\np95 nas falhas: min={p95s[0]:.0f} mediana={p95s[len(p95s)//2]:.0f} max={p95s[-1]:.0f} ms")