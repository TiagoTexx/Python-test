import http.client, json, sys, time

dur, port = float(sys.argv[1]), int(sys.argv[2])
c = http.client.HTTPConnection("127.0.0.1", port)
c.request("GET", "/health"); c.getresponse().read()   # aquece a ligação

out, end = [], time.perf_counter() + dur
while time.perf_counter() < end:
    t0 = time.perf_counter()
    c.request("GET", "/health"); c.getresponse().read()
    out.append(round((time.perf_counter() - t0) * 1000, 1))
    time.sleep(0.01)
print(json.dumps(out))