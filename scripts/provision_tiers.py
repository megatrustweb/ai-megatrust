#!/usr/bin/env python3
"""Provision Mega AI access tiers through LibreChat's admin API.

Creates/updates roles (permissions) and per-role configuration overrides:
  USER (default)  -> ⏳ Pending activation gate
  CHAT_MEDIA      -> 💬 chat + Arabic voice + images/video (free models)
  CODER           -> 💻 + agents, code, Claude
  POWER           -> ⭐ everything

Usage (normally via scripts/provision.sh):
  MEGA_SVC_EMAIL=... MEGA_SVC_PASSWORD=... provision_tiers.py <base_url>
      -> logs in as a temporary service admin, provisions, then the account deletes itself.
  provision_tiers.py <base_url> <token_file>
      -> uses an existing admin JWT.
Idempotent: safe to re-run.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("MEGA_BASE_URL", "http://api:3080")).rstrip("/")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
TOKEN = ""


def http(method, path, body=None, token=None):
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def api(method, path, body=None):
    return http(method, f"/api/admin{path}", body, TOKEN)


# ----------------------------------------------------------------- permissions
SECTIONS = {
    "BOOKMARKS": ["USE"],
    "PROMPTS": ["USE", "CREATE", "SHARE", "SHARE_PUBLIC"],
    "MEMORIES": ["USE", "CREATE", "UPDATE", "READ", "OPT_OUT"],
    "AGENTS": ["USE", "CREATE", "SHARE", "SHARE_PUBLIC"],
    "MULTI_CONVO": ["USE"],
    "TEMPORARY_CHAT": ["USE"],
    "RUN_CODE": ["USE"],
    "WEB_SEARCH": ["USE"],
    "PEOPLE_PICKER": ["VIEW_USERS", "VIEW_GROUPS", "VIEW_ROLES"],
    "MARKETPLACE": ["USE"],
    "FILE_SEARCH": ["USE"],
    "FILE_CITATIONS": ["USE"],
    "MCP_SERVERS": ["USE", "CREATE", "SHARE", "SHARE_PUBLIC", "CONFIGURE_OBO"],
    "REMOTE_AGENTS": ["USE", "CREATE", "SHARE", "SHARE_PUBLIC"],
    "SKILLS": ["USE", "CREATE", "SHARE", "SHARE_PUBLIC"],
    "SHARED_LINKS": ["CREATE", "SHARE", "SHARE_PUBLIC"],
    "SCHEDULES": ["USE", "CREATE"],
}


def perms(allow: dict[str, list[str]] | str) -> dict:
    """allow = {"SECTION": ["BIT", ...]} or "ALL"; everything else is False."""
    out = {}
    for sec, bits in SECTIONS.items():
        on = bits if allow == "ALL" else allow.get(sec, [])
        out[sec] = {b: (b in on) for b in bits}
    return out


PENDING = perms({})  # nothing
CHAT_MEDIA = perms({
    "BOOKMARKS": ["USE"], "PROMPTS": ["USE", "CREATE"], "MEMORIES": ["USE", "CREATE", "UPDATE", "READ", "OPT_OUT"],
    "AGENTS": ["USE"], "TEMPORARY_CHAT": ["USE"], "WEB_SEARCH": ["USE"], "MARKETPLACE": ["USE"],
    "FILE_SEARCH": ["USE"], "FILE_CITATIONS": ["USE"], "MCP_SERVERS": ["USE"], "SKILLS": ["USE"],
    "SHARED_LINKS": ["CREATE", "SHARE"],
})
CODER = perms({
    "BOOKMARKS": ["USE"], "PROMPTS": ["USE", "CREATE", "SHARE"], "MEMORIES": ["USE", "CREATE", "UPDATE", "READ", "OPT_OUT"],
    "AGENTS": ["USE", "CREATE", "SHARE"], "MULTI_CONVO": ["USE"], "TEMPORARY_CHAT": ["USE"], "RUN_CODE": ["USE"],
    "WEB_SEARCH": ["USE"], "PEOPLE_PICKER": ["VIEW_USERS", "VIEW_GROUPS"], "MARKETPLACE": ["USE"],
    "FILE_SEARCH": ["USE"], "FILE_CITATIONS": ["USE"], "MCP_SERVERS": ["USE", "CREATE", "SHARE"],
    "REMOTE_AGENTS": ["USE", "CREATE"], "SKILLS": ["USE", "CREATE", "SHARE"], "SHARED_LINKS": ["CREATE", "SHARE"],
    "SCHEDULES": ["USE", "CREATE"],
})
POWER = perms("ALL")
POWER["MCP_SERVERS"]["CONFIGURE_OBO"] = False  # advanced on-behalf-of auth stays admin-only

# ----------------------------------------------------------------- model specs
VOICE_PROMPT = (
    "أنت «استوديو الصوت» في Mega AI لمجموعة Mega Trust. عندما يطلب المستخدم تعليقًا صوتيًا: "
    "1) اكتب النص باللهجة المطلوبة نفسها (مصري، سعودي، خليجي…) وليس بالفصحى إلا إذا طُلبت صراحة؛ "
    "2) اعرض النص على المستخدم؛ 3) استدعِ أداة arabic_voiceover باللهجة والجنس المناسبين؛ "
    "4) أعطِ المستخدم رابط الملف الصوتي. اللهجة الافتراضية: المصرية. "
    "اسم العلامة الصحيح: QN Automotive (وليس QN Motors)."
)
MEDIA_PROMPT = (
    "أنت «استوديو الوسائط» في Mega AI لمجموعة Mega Trust. لتوليد الصور والفيديو استخدم أدوات fal.ai: "
    "اختر النموذج المناسب، واعرض التكلفة التقديرية، واطلب موافقة المستخدم قبل التوليد، ثم أعطِ رابط النتيجة. "
    "للتعليق الصوتي العربي باللهجات استخدم arabic_voiceover. اسم العلامة الصحيح: QN Automotive."
)


def spec(name, label, desc, endpoint, model, mcp=None, default=False, prompt=None, **extra):
    preset = {"endpoint": endpoint, "model": model}
    if prompt:
        preset["promptPrefix"] = prompt
    s = {"name": name, "label": label, "description": desc, "preset": preset}
    if default:
        s["default"] = True
    if mcp:
        s["mcpServers"] = mcp
    s.update(extra)
    return s


S_CHAT = spec("mega-chat", "💬 Mega Chat - سريع ومجاني", "Llama 3.3 70B على Groq: دردشة سريعة جدًا",
              "Groq", "llama-3.3-70b-versatile", ["mega-tools"], default=True)
S_GEMINI = spec("gemini-flash", "✨ Gemini Flash - مجاني", "قوي في العربية وفهم الصور والملفات",
                "Gemini", "gemini-2.5-flash", ["mega-tools"])
S_VOICE = spec("voice-studio", "🎙️ استوديو الصوت العربي", "اكتب الفكرة واطلب تعليقًا صوتيًا مصريًا أو سعوديًا أو خليجيًا",
               "Gemini", "gemini-2.5-flash", ["mega-tools"], prompt=VOICE_PROMPT)
S_MEDIA = spec("media-studio", "🎬 استوديو الصور والفيديو", "توليد صور وفيديو عبر fal.ai (يعمل بعد إضافة مفتاح fal من لوحة الإدارة)",
               "Gemini", "gemini-2.5-flash", ["mega-tools", "fal-ai"], prompt=MEDIA_PROMPT)
S_CLAUDE = spec("claude-coder", "🧠 Claude Sonnet - برمجة", "أفضل نموذج للبرمجة والمهام القوية (يُخصم من رصيدك)",
                "Claude", "claude-sonnet-5-5", ["mega-tools"], default=True, artifacts=True)


def daily(amount):
    return {"autoRefillEnabled": True, "refillIntervalValue": 1, "refillIntervalUnit": "days",
            "refillAmount": amount, "refillMode": "reset"}


OVERRIDES = {
    "USER": {
        "endpoints": {"custom": [{
            "name": "Mega Pending", "apiKey": "pending", "baseURL": "http://mega-tools:8000/pending/v1",
            "models": {"default": ["pending"], "fetch": False}, "titleConvo": False,
            "modelDisplayLabel": "Mega AI",
            "tokenConfig": {"pending": {"prompt": 0, "completion": 0, "context": 8192}},
        }]},
        "modelSpecs": {"enforce": True, "prioritize": True, "list": [
            spec("pending-activation", "⏳ حسابك قيد التفعيل", "سيُفعَّل حسابك من إدارة المنصة قريبًا",
                 "Mega Pending", "pending", default=True)]},
        "interface": {"customWelcome": "⏳ أهلًا {{user.name}} — حسابك قيد المراجعة، وسيُفعَّل قريبًا من إدارة Mega AI",
                      "modelSelect": False, "parameters": False, "presets": False},
    },
    "CHAT_MEDIA": {
        "modelSpecs": {"enforce": True, "prioritize": True, "list": [S_CHAT, S_GEMINI, S_VOICE, S_MEDIA]},
        "interface": {"modelSelect": False, "parameters": False, "presets": False},
        "balance": daily(300_000),          # $0.30/day for any paid usage; free models cost 0
    },
    "CODER": {
        "modelSpecs": {"enforce": False, "prioritize": True, "list": [S_CLAUDE, S_CHAT, S_GEMINI, S_VOICE]},
        "balance": daily(3_000_000),        # $3/day
    },
    "POWER": {
        "balance": daily(10_000_000),       # $10/day
    },
}

ROLES = {
    "USER": ("⏳ قيد التفعيل — الدور الافتراضي لكل حساب جديد حتى يفعّله المسؤول", PENDING),
    "CHAT_MEDIA": ("💬 دردشة + صوت عربي + صور وفيديو (نماذج مجانية)", CHAT_MEDIA),
    "CODER": ("💻 مبرمج: + Agents وتشغيل الكود وGitHub وClaude", CODER),
    "POWER": ("⭐ صلاحيات كاملة", POWER),
}


def main():
    global TOKEN
    svc_email, svc_pw = os.getenv("MEGA_SVC_EMAIL"), os.getenv("MEGA_SVC_PASSWORD")
    if svc_email and svc_pw:
        st, res = http("POST", "/api/auth/login", {"email": svc_email, "password": svc_pw})
        TOKEN = res.get("token", "") if isinstance(res, dict) else ""
        print(f"service login -> {st} {'OK' if TOKEN else res}")
        if not TOKEN:
            sys.exit(1)
    else:
        TOKEN = open(sys.argv[2]).read().strip()

    try:
        provision()
    finally:
        if svc_email and svc_pw:
            st, _ = http("DELETE", "/api/user/delete", token=TOKEN)
            print(f"service account self-delete -> {st}")


def provision():
    st, existing = api("GET", "/roles")
    names = set()
    if st == 200:
        items = existing.get("roles", existing) if isinstance(existing, dict) else existing
        names = {str(r.get("name")) for r in items if isinstance(r, dict)}
    print(f"GET /roles -> {st}; existing: {sorted(names)}")

    for name, (desc, p) in ROLES.items():
        if name in names:
            st, res = api("PATCH", f"/roles/{name}/permissions", {"permissions": p})
            print(f"PATCH /roles/{name}/permissions -> {st} {'' if st < 300 else str(res)[:300]}")
            if name != "USER":
                st, res = api("PATCH", f"/roles/{name}", {"description": desc})
                print(f"PATCH /roles/{name} (description) -> {st} {'' if st < 300 else str(res)[:200]}")
        else:
            st, res = api("POST", "/roles", {"name": name, "description": desc, "permissions": p})
            print(f"POST /roles {name} -> {st} {'' if st < 300 else str(res)[:300]}")

    for name, ov in OVERRIDES.items():
        st, res = api("PUT", f"/config/role/{name}", {"overrides": ov, "priority": 10})
        msg = "" if st < 300 else json.dumps(res, ensure_ascii=False)[:600]
        print(f"PUT /config/role/{name} -> {st} {msg}")


if __name__ == "__main__":
    main()
