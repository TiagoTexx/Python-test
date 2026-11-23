import json, socket, sys, time

dur, port = float(sys.argv[1]), int(sys.argv[2])
out, end = [], time.perf_counter() + dur
while time.perf_counter() < end:
    t0 = time.perf_counter()
    s = socket.create_connection(("127.0.0.1", port))
    out.append(round((time.perf_counter() - t0) * 1000, 1))
    s.close()
    time.sleep(0.01)
print(json.dumps(out))
