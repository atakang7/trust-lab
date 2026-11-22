"""1 Hz user-journey probe. Prints one char per second: . ok, x fail. Usage: probe.py SECONDS"""
import sys, time, uuid, random, requests, threading
S = "http://10.0.20.49:30081"
P = ['0PUK6V6EV0','1YMWWN1N4O','2ZYFJ3GM2N','66VCHSJNUP','6E92ZMYYFZ','9SIQT8TOJO','HQTGWGPNH4','L9ECAV7KIM','LS4PSXUNUM','OLJCESPC7Z']
ADDR = {"streetAddress": "1", "city": "MV", "state": "CA", "country": "United States", "zipCode": "94043"}
CC = {"creditCardCvv": 672, "creditCardExpirationMonth": 1, "creditCardExpirationYear": 2030, "creditCardNumber": "4432801561520454"}
res = []
def journey(t):
    u, pid = "p" + uuid.uuid4().hex[:8], random.choice(P)
    try:
        requests.get(f"{S}/api/products/{pid}", timeout=3).raise_for_status()
        requests.get(f"{S}/api/recommendations", params={"productIds": pid}, timeout=3).raise_for_status()
        requests.get(f"{S}/api/data", params={"contextKeys": "telescopes"}, timeout=3).raise_for_status()
        requests.post(f"{S}/api/cart", json={"item": {"productId": pid, "quantity": 1}, "userId": u}, timeout=3).raise_for_status()
        r = requests.post(f"{S}/api/checkout?currencyCode=USD", timeout=3, json={"userId": u, "email": "p@e.com", "userCurrency": "USD", "address": ADDR, "creditCard": CC})
        ok = r.status_code == 200 and "orderId" in r.text and r.elapsed.total_seconds() < 1.5
    except Exception:
        ok = False
    res.append((t, ok))
t0 = time.time(); n = int(sys.argv[1])
for i in range(n):
    threading.Thread(target=journey, args=(i,), daemon=True).start()
    time.sleep(max(0, t0 + i + 1 - time.time()))
time.sleep(4)
res.sort(); print("".join("." if ok else "x" for _, ok in res)); print(f"{sum(o for _, o in res)}/{len(res)} ok")
