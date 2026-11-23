import http.client
import json
import os
import sys
import time


def main() -> None:
    port = int(sys.argv[1])
    stop_path = sys.argv[2]
    out_path = sys.argv[3]
    interval = float(sys.argv[4]) if len(sys.argv) > 4 else 0.01

    latencies: list[float] = []
    errors: list[str] = []
    conn: http.client.HTTPConnection | None = None

    print("ready", flush=True)

    try:
        while True:  # garante pelo menos um sample
            t0 = time.perf_counter()
            try:
                if conn is None:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                resp.read()
                elapsed_ms = (time.perf_counter() - t0) * 1000
                latencies.append(round(elapsed_ms, 2))
                if resp.status != 200:
                    errors.append(f"status {resp.status}")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
                if conn is not None:
                    conn.close()
                    conn = None

            if os.path.exists(stop_path):
                break
            time.sleep(interval)
    finally:
        if conn is not None:
            conn.close()
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"lat": latencies, "errors": errors}, f)


if __name__ == "__main__":
    main()
