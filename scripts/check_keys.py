"""Validate the keys in ../.env. Prints only 'NAME: OK' or 'NAME: FAIL - reason'. Never prints key values."""
import json, pathlib, urllib.request, urllib.error

ENV = pathlib.Path(__file__).resolve().parent.parent / ".env"
env = {}
for line in ENV.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

def get(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "meet-agi-check", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return None, type(e).__name__.encode()

def report(name, status, body=b""):
    if status == 200:
        print(f"{name}: OK")
    elif status is None:
        print(f"{name}: FAIL - network error ({body.decode()})")
    elif status in (401, 403):
        print(f"{name}: FAIL - rejected by vendor (HTTP {status}); re-copy the key")
    else:
        print(f"{name}: FAIL - HTTP {status}")

def need(name):
    if not env.get(name):
        print(f"{name}: FAIL - empty in .env")
        return False
    return True

def post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": "meet-agi-check", "content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, type(e).__name__.encode()

if need("ANTHROPIC_API_KEY"):
    # Review B item 1: listing models succeeds even with zero credit, so the old check said OK while
    # every real call failed. This sends one real 1-token message (costs well under US$0.001).
    s, b = post("https://api.anthropic.com/v1/messages",
                {"x-api-key": env["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                {"model": "claude-haiku-4-5-20251001", "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]})
    if s == 400 and b"credit balance" in b:
        print("ANTHROPIC_API_KEY: FAIL - key works but the account has no credit (console.anthropic.com > Plans & Billing)")
    else:
        report("ANTHROPIC_API_KEY", s, b"")

if need("GEMINI_API_KEY"):
    report("GEMINI_API_KEY", *get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
        {"x-goog-api-key": env["GEMINI_API_KEY"]}))

if env.get("BOT_PROVIDER","recall")=="attendee":
    if need("ATTENDEE_API_KEY"):
        base=env.get("ATTENDEE_BASE_URL") or "https://app.attendee.dev"
        report("ATTENDEE_API_KEY", *get(base+"/api/v1/bots", {"Authorization": "Token "+env["ATTENDEE_API_KEY"]}))
elif need("RECALL_API_KEY"):
    region = env.get("RECALL_REGION") or "us-west-2"
    url = f"https://{region}.recall.ai/api/v1/bot/?page_size=1"
    s, b = get(url, {"Authorization": env["RECALL_API_KEY"]})
    if s in (401, 403):
        s, b = get(url, {"Authorization": "Token " + env["RECALL_API_KEY"]})
    report("RECALL_API_KEY", s, b)

voices = None
if need("INWORLD_API_KEY"):
    s, b = get("https://api.inworld.ai/tts/v1/voices", {"Authorization": "Basic " + env["INWORLD_API_KEY"]})
    report("INWORLD_API_KEY", s, b)
    if s == 200:
        try:
            voices = {v.get("voiceId") for v in json.loads(b).get("voices", [])}
        except Exception:
            voices = set()

if need("INWORLD_VOICE_ID"):
    if voices is None:
        print("INWORLD_VOICE_ID: FAIL - cannot check until INWORLD_API_KEY is OK")
    elif env["INWORLD_VOICE_ID"] in voices:
        print("INWORLD_VOICE_ID: OK")
    else:
        print("INWORLD_VOICE_ID: FAIL - not in your Inworld voice list")

if need("RECALL_WEBHOOK_TOKEN"):
    t = env["RECALL_WEBHOOK_TOKEN"]
    print("RECALL_WEBHOOK_TOKEN: OK" if len(t) == 64 else f"RECALL_WEBHOOK_TOKEN: FAIL - length {len(t)}, expected 64")

if need("PUBLIC_BASE_URL"):
    u = env["PUBLIC_BASE_URL"]
    print("PUBLIC_BASE_URL: OK" if u.startswith("https://") and "." in u else "PUBLIC_BASE_URL: FAIL - must be https://<domain>")
