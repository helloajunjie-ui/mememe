import json, urllib.request, ssl, sys

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

cfg = json.load(open(r"F:\me\self-agent\config\llm.json", encoding="utf-8"))
srcs = [s for s in cfg["sources"] if s.get("enabled") and (s.get("api_key") or "").strip()]
only = sys.argv[1] if len(sys.argv) > 1 else None
if only:
    srcs = [s for s in srcs if s["name"].lower() == only.lower()]

def get(url, key, timeout=8):
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + key,
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status, r.read().decode("utf-8", "replace")

for s in srcs:
    name, base, key = s["name"], s["base_url"].rstrip("/"), s["api_key"]
    print("=" * 50, flush=True)
    print(f"[{name}] {base}", flush=True)
    hit = False
    for murl in (base + "/v1/models", base + "/models"):
        try:
            st, body = get(murl, key)
            data = json.loads(body).get("data", [])
            ids = [m.get("id", "") for m in data]
            hits = [i for i in ids if "mercur" in i.lower() or "inception" in i.lower()]
            print(f"  models -> {st} total={len(ids)} MERCURY={hits}", flush=True)
            print("  sample:", ids[:6], flush=True)
            hit = True
            break
        except Exception as e:
            print(f"  {murl} ERR {type(e).__name__} {str(e)[:100]}", flush=True)
    if not hit:
        print("  (models 端点不可用)", flush=True)
