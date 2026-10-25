import json
p = r"F:\me\self-agent\config\llm.json"
d = json.load(open(p, encoding="utf-8"))
print("TOP KEYS:", list(d.keys()) if isinstance(d, dict) else type(d))
print("default/main:", d.get("default"), d.get("main"), d.get("active"))

def mask(k):
    if not k: return "(empty)"
    return k[:7] + "..." + k[-4:] + f" len={len(k)}"

srcs = d.get("sources") or d.get("providers") or []
if isinstance(srcs, dict):
    srcs = [dict(v, name=k) for k, v in srcs.items()]
print("SOURCE COUNT:", len(srcs))
for s in srcs:
    print("---")
    print(" name    :", s.get("name"))
    print(" enabled :", s.get("enabled"))
    print(" base_url:", s.get("base_url"))
    print(" model   :", s.get("model"))
    print(" models  :", s.get("models"))
    print(" key     :", mask(s.get("api_key") or s.get("key")))
    extra = {k: v for k, v in s.items() if k not in ("name","enabled","base_url","models","model","api_key","key")}
    if extra: print(" extra   :", json.dumps(extra, ensure_ascii=False)[:300])
