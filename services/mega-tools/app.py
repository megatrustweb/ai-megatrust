"""
Mega Tools — companion service for Mega AI (LibreChat), Mega Trust Group.

One container that provides:
  * Arabic dialect text-to-speech (Egyptian, Saudi, Emirati, Kuwaiti, Qatari,
    Bahraini, Omani, Levantine, Iraqi, Moroccan ...)
      - OpenAI-compatible  POST /v1/audio/speech   -> LibreChat native "read aloud"
      - MCP tool           arabic_voiceover        -> downloadable MP3 voice-overs
  * Free web search + page reading through a self-hosted SearXNG (MCP tools)
  * A "pending activation" OpenAI-compatible chat endpoint (/pending/v1) used to
    gate newly-registered accounts until an admin moves them to an active role.

Engines:
  - "microsoft" (default, free): Microsoft neural voices via edge-tts.
  - "elevenlabs" (premium): needs an ElevenLabs key, sent by LibreChat as the
    `X-ElevenLabs-Key` MCP header (set it encrypted in the Admin Panel), or the
    ELEVENLABS_API_KEY env var.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import edge_tts
import httpx
import trafilatura
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from mcp.server.fastmcp import Context, FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mega-tools")

# --------------------------------------------------------------------------- config
API_KEY = os.getenv("MEGA_TOOLS_KEY", "").strip()
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/data/media"))
MEDIA_PUBLIC_BASE = os.getenv("MEDIA_PUBLIC_BASE", "http://localhost:8095/media").rstrip("/")
MEDIA_TTL_DAYS = float(os.getenv("MEDIA_TTL_DAYS", "7"))
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")
ELEVENLABS_ENV_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
MAX_TTS_CHARS = int(os.getenv("MAX_TTS_CHARS", "20000"))
MAX_FETCH_BYTES = 5 * 1024 * 1024
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

PENDING_MESSAGE = os.getenv(
    "PENDING_MESSAGE",
    "⏳ **حسابك قيد المراجعة**\n\n"
    "شكرًا لتسجيلك في **Mega AI**. تم إنشاء حسابك وحُفظ رصيدك الافتراضي، "
    "لكن الحساب يحتاج إلى **تفعيل من إدارة المنصة** قبل استخدام النماذج والأدوات.\n\n"
    "سيُفعَّل حسابك قريبًا. للاستعجال تواصل مع قسم تقنية المعلومات في Mega Trust.\n\n"
    "---\n"
    "⏳ **Your account is pending activation.** Your default credits are reserved. "
    "An administrator will activate your access shortly.",
)

# --------------------------------------------------------------------------- voices
# Microsoft neural voices per Arabic dialect (female, male).
DIALECTS: dict[str, dict] = {
    "egyptian": {"ar": "مصري", "female": "ar-EG-SalmaNeural", "male": "ar-EG-ShakirNeural"},
    "saudi": {"ar": "سعودي", "female": "ar-SA-ZariyahNeural", "male": "ar-SA-HamedNeural"},
    "emirati": {"ar": "إماراتي", "female": "ar-AE-FatimaNeural", "male": "ar-AE-HamdanNeural"},
    "kuwaiti": {"ar": "كويتي", "female": "ar-KW-NouraNeural", "male": "ar-KW-FahedNeural"},
    "qatari": {"ar": "قطري", "female": "ar-QA-AmalNeural", "male": "ar-QA-MoazNeural"},
    "bahraini": {"ar": "بحريني", "female": "ar-BH-LailaNeural", "male": "ar-BH-AliNeural"},
    "omani": {"ar": "عماني", "female": "ar-OM-AyshaNeural", "male": "ar-OM-AbdullahNeural"},
    "jordanian": {"ar": "أردني", "female": "ar-JO-SanaNeural", "male": "ar-JO-TaimNeural"},
    "lebanese": {"ar": "لبناني", "female": "ar-LB-LaylaNeural", "male": "ar-LB-RamiNeural"},
    "syrian": {"ar": "سوري", "female": "ar-SY-AmanyNeural", "male": "ar-SY-LaithNeural"},
    "iraqi": {"ar": "عراقي", "female": "ar-IQ-RanaNeural", "male": "ar-IQ-BasselNeural"},
    "yemeni": {"ar": "يمني", "female": "ar-YE-MaryamNeural", "male": "ar-YE-SalehNeural"},
    "moroccan": {"ar": "مغربي", "female": "ar-MA-MounaNeural", "male": "ar-MA-JamalNeural"},
}

DIALECT_ALIASES = {
    # Egyptian
    "مصري": "egyptian", "مصرى": "egyptian", "مصر": "egyptian", "egypt": "egyptian", "eg": "egyptian",
    "masri": "egyptian", "عامية": "egyptian", "عامية مصرية": "egyptian",
    # Saudi
    "سعودي": "saudi", "سعودى": "saudi", "السعودية": "saudi", "ksa": "saudi", "sa": "saudi",
    "najdi": "saudi", "نجدي": "saudi", "hijazi": "saudi", "حجازي": "saudi",
    # Gulf (defaults to Emirati)
    "خليجي": "emirati", "خليجى": "emirati", "gulf": "emirati", "khaleeji": "emirati",
    "إماراتي": "emirati", "اماراتي": "emirati", "الامارات": "emirati", "uae": "emirati", "ae": "emirati",
    "كويتي": "kuwaiti", "kw": "kuwaiti", "قطري": "qatari", "qa": "qatari",
    "بحريني": "bahraini", "bh": "bahraini", "عماني": "omani", "om": "omani",
    # Others
    "أردني": "jordanian", "اردني": "jordanian", "شامي": "syrian", "levantine": "syrian",
    "لبناني": "lebanese", "سوري": "syrian", "عراقي": "iraqi", "يمني": "yemeni", "مغربي": "moroccan",
    # MSA / Fusha — Saudi voices read MSA cleanly
    "فصحى": "saudi", "فصحي": "saudi", "msa": "saudi", "fusha": "saudi", "arabic": "saudi",
}

# Friendly names exposed to LibreChat's voice picker -> Microsoft voice id.
FRIENDLY_VOICES: dict[str, str] = {}
for _key, _d in DIALECTS.items():
    for _g in ("female", "male"):
        _name = _d[_g].split("-")[2].replace("Neural", "")
        FRIENDLY_VOICES[f"{_key.capitalize()} {'Female' if _g == 'female' else 'Male'} ({_name})"] = _d[_g]


def resolve_dialect(dialect: str) -> str:
    d = (dialect or "egyptian").strip().lower()
    if d in DIALECTS:
        return d
    return DIALECT_ALIASES.get(d, DIALECT_ALIASES.get(dialect.strip(), "egyptian"))


def resolve_voice(voice: str) -> str:
    """Accept a Microsoft voice id, a friendly name, 'dialect-gender', or an OpenAI voice name."""
    v = (voice or "").strip()
    if re.fullmatch(r"[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural", v):
        return v
    if v in FRIENDLY_VOICES:
        return FRIENDLY_VOICES[v]
    m = re.fullmatch(r"([^\s\-_]+)[\s\-_]+(female|male|f|m)", v.lower())
    if m:
        g = "female" if m.group(2) in ("female", "f") else "male"
        return DIALECTS[resolve_dialect(m.group(1))][g]
    if v.lower() in DIALECTS or v in DIALECT_ALIASES or v.lower() in DIALECT_ALIASES:
        return DIALECTS[resolve_dialect(v)]["female"]
    return DIALECTS["egyptian"]["female"]  # OpenAI names (alloy, nova, ...) fall back here


# --------------------------------------------------------------------------- TTS engines
async def synth_microsoft(text: str, voice: str, speed: float = 1.0, pitch_hz: int = 0) -> bytes:
    rate_pct = int(round((max(0.5, min(2.0, speed)) - 1.0) * 100))
    comm = edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%", pitch=f"{pitch_hz:+d}Hz")
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            buf.extend(chunk["data"])
    if not buf:
        raise RuntimeError("TTS returned no audio")
    return bytes(buf)


async def synth_elevenlabs(text: str, voice_id: str, api_key: str, model: str) -> bytes:
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": api_key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": model},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")
        return r.content


def save_media(data: bytes, ext: str = "mp3") -> tuple[str, str]:
    name = f"{uuid.uuid4().hex}.{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    return name, f"{MEDIA_PUBLIC_BASE}/{name}"


async def cleanup_media_loop() -> None:
    while True:
        try:
            cutoff = time.time() - MEDIA_TTL_DAYS * 86400
            for f in MEDIA_DIR.glob("*"):
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception as e:  # pragma: no cover
            log.warning("media cleanup failed: %s", e)
        await asyncio.sleep(6 * 3600)


# --------------------------------------------------------------------------- web helpers
def _host_is_public(host: str) -> bool:
    """Block SSRF to private, loopback, link-local and reserved addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


# --------------------------------------------------------------------------- MCP server
INSTRUCTIONS = (
    "Mega Tools for Mega Trust Group. "
    "Use `arabic_voiceover` to produce downloadable Arabic voice-overs in a specific dialect "
    "(Egyptian, Saudi, Emirati/Gulf, Kuwaiti, Qatari, Bahraini, Omani, Levantine, Iraqi, Moroccan). "
    "IMPORTANT: write the `text` in the target colloquial dialect itself (e.g. Egyptian: 'إزيك؟ عامل إيه النهارده؟', "
    "Saudi: 'وش أخبارك؟ عساك طيب'), not Modern Standard Arabic, unless MSA is requested. "
    "Use `web_search` then `read_webpage` for up-to-date information, and cite the URLs you used."
)

mcp = FastMCP(name="Mega Tools", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)
mcp.settings.streamable_http_path = "/mcp"
try:  # internal service, authenticated by bearer token: allow any Host header
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
except Exception:  # older SDKs have no DNS-rebinding guard
    pass


def _header(ctx: Optional[Context], name: str) -> str:
    try:
        req = ctx.request_context.request  # type: ignore[union-attr]
        return (req.headers.get(name) or "").strip() if req is not None else ""
    except Exception:
        return ""


@mcp.tool()
async def arabic_voiceover(
    text: str,
    dialect: str = "egyptian",
    gender: str = "female",
    speed: float = 1.0,
    engine: str = "microsoft",
    voice_id: str = "",
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """Generate a downloadable MP3 Arabic voice-over in a chosen dialect.

    Args:
        text: The script to speak. Write it in the target colloquial dialect (not MSA) for natural results.
        dialect: egyptian | saudi | emirati (gulf) | kuwaiti | qatari | bahraini | omani | jordanian |
                 lebanese | syrian | iraqi | yemeni | moroccan. Arabic names also work (مصري، سعودي، خليجي ...).
        gender: female | male.
        speed: 0.5 - 2.0 (1.0 = normal).
        engine: "microsoft" (free, default) or "elevenlabs" (premium; needs an ElevenLabs key and a voice_id).
        voice_id: ElevenLabs voice id (only for engine="elevenlabs"), e.g. an Egyptian or Saudi voice from your library.
    """
    text = (text or "").strip()
    if not text:
        return "No text provided."
    if len(text) > MAX_TTS_CHARS:
        return f"Text too long ({len(text)} chars). Maximum is {MAX_TTS_CHARS}."
    d = resolve_dialect(dialect)
    g = "male" if str(gender).lower().startswith(("m", "ذ", "رج")) else "female"

    if engine.lower() == "elevenlabs":
        key = _header(ctx, "x-elevenlabs-key") or ELEVENLABS_ENV_KEY
        if not key:
            return "ElevenLabs is not configured (no API key). Use engine='microsoft' or ask an admin to add the key."
        if not voice_id:
            return "ElevenLabs needs a voice_id (pick an Egyptian/Saudi/Gulf voice from your ElevenLabs library)."
        audio = await synth_elevenlabs(text, voice_id, key, os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2"))
        voice_desc = f"ElevenLabs voice {voice_id}"
    else:
        voice = DIALECTS[d][g]
        audio = await synth_microsoft(text, voice, speed=speed)
        voice_desc = f"{voice} (Microsoft)"

    _, url = save_media(audio, "mp3")
    secs = round(len(audio) / 6000)  # ~48 kbps mp3
    return (
        f"🔊 **[تشغيل / تحميل الملف الصوتي]({url})**\n\n"
        f"- اللهجة: {DIALECTS[d]['ar']} ({d}) — {'رجل' if g == 'male' else 'امرأة'}\n"
        f"- الصوت: {voice_desc}\n"
        f"- المدة التقريبية: ~{secs} ثانية\n"
        f"- الرابط صالح لمدة {int(MEDIA_TTL_DAYS)} أيام\n\n"
        f"Direct link: {url}"
    )


@mcp.tool()
async def list_arabic_voices() -> str:
    """List the available Arabic dialects and their free Microsoft voices."""
    rows = ["| Dialect | اللهجة | Female | Male |", "|---|---|---|---|"]
    for k, d in DIALECTS.items():
        rows.append(f"| {k} | {d['ar']} | {d['female']} | {d['male']} |")
    return "\n".join(rows) + "\n\nGulf (خليجي) defaults to Emirati. MSA/فصحى uses the Saudi voices."


@mcp.tool()
async def web_search(query: str, max_results: int = 8, language: str = "auto", time_range: str = "") -> str:
    """Search the web (free, via self-hosted SearXNG). Returns titles, URLs and snippets.

    Args:
        query: Search query (Arabic or English).
        max_results: 1-20.
        language: "auto", "ar", "en", ...
        time_range: "", "day", "week", "month" or "year".
    """
    params = {"q": query, "format": "json", "safesearch": 1}
    if language and language != "auto":
        params["language"] = language
    if time_range in ("day", "week", "month", "year"):
        params["time_range"] = time_range
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.get(f"{SEARXNG_URL}/search", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"Search failed: {e}"
    results = data.get("results", [])[: max(1, min(20, max_results))]
    if not results:
        return "No results."
    out = []
    for i, res in enumerate(results, 1):
        snippet = (res.get("content") or "").strip().replace("\n", " ")
        out.append(f"{i}. **{res.get('title', '').strip()}**\n   {res.get('url')}\n   {snippet[:300]}")
    return "\n".join(out)


@mcp.tool()
async def read_webpage(url: str, max_chars: int = 15000) -> str:
    """Fetch a public web page and return its main readable text (ads/menus removed).

    Args:
        url: http(s) URL of a public page.
        max_chars: Truncate the extracted text to this many characters (max 50000).
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "Only public http(s) URLs are allowed."
    if not _host_is_public(p.hostname):
        return "Blocked: that address is private or internal."
    try:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (MegaAI reader)"}
        ) as c:
            target = url
            r = None
            for _ in range(5):  # manual redirects so every hop is SSRF-checked
                r = await c.get(target)
                if r.is_redirect and r.next_request is not None:
                    nxt = urlparse(str(r.next_request.url))
                    if not nxt.hostname or not _host_is_public(nxt.hostname):
                        return "Blocked: redirect to a private or invalid address."
                    target = str(r.next_request.url)
                    continue
                break
            if r is None or r.is_redirect:
                return "Fetch failed: too many redirects."
            r.raise_for_status()
            body = r.content[:MAX_FETCH_BYTES]
            encoding = r.encoding or "utf-8"
    except Exception as e:
        return f"Fetch failed: {e}"
    text = trafilatura.extract(body.decode(encoding, errors="ignore"), include_links=False) or ""
    if not text:
        return "Could not extract readable text from this page."
    limit = max(1000, min(50000, max_chars))
    return f"Source: {url}\n\n{text[:limit]}" + ("\n\n[truncated]" if len(text) > limit else "")


mcp_app = mcp.streamable_http_app()


# --------------------------------------------------------------------------- HTTP app
@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleaner = asyncio.create_task(cleanup_media_loop())
    async with mcp.session_manager.run():
        yield
    cleaner.cancel()


app = FastAPI(title="Mega Tools", lifespan=lifespan, docs_url=None, redoc_url=None)

# /pending only ever returns a fixed "awaiting activation" message, so it needs no key
# (keeps the per-role override free of secrets).
PROTECTED_PREFIXES = ("/mcp", "/v1")


@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    if API_KEY and request.url.path.startswith(PROTECTED_PREFIXES):
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        if token != API_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True, "service": "mega-tools"}


# ---- OpenAI-compatible TTS (LibreChat "read aloud") -------------------------
@app.post("/v1/audio/speech")
async def openai_speech(request: Request):
    body = await request.json()
    text = (body.get("input") or "").strip()
    if not text:
        raise HTTPException(400, "input is required")
    voice = resolve_voice(body.get("voice", ""))
    speed = float(body.get("speed") or 1.0)
    try:
        audio = await synth_microsoft(text[:MAX_TTS_CHARS], voice, speed=speed)
    except Exception as e:
        log.exception("tts failed")
        raise HTTPException(502, f"tts failed: {e}")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/v1/audio/voices")
async def openai_voices():
    return {"voices": list(FRIENDLY_VOICES.keys())}


# ---- "Pending activation" OpenAI-compatible chat endpoint -------------------
@app.get("/pending/v1/models")
async def pending_models():
    return {"object": "list", "data": [{"id": "pending", "object": "model", "owned_by": "mega-trust"}]}


@app.post("/pending/v1/chat/completions")
async def pending_chat(request: Request):
    body = await request.json()
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if body.get("stream"):

        async def gen():
            first = {
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": "pending",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": PENDING_MESSAGE}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
            last = {
                "id": cid, "object": "chat.completion.chunk", "created": created, "model": "pending",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage,
            }
            yield f"data: {json.dumps(last)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")
    return {
        "id": cid, "object": "chat.completion", "created": created, "model": "pending",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": PENDING_MESSAGE}, "finish_reason": "stop"}],
        "usage": usage,
    }


# ---- generated media --------------------------------------------------------
@app.get("/media/{name}")
async def media(name: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.(mp3|mp4|wav)", name):
        raise HTTPException(404)
    f = MEDIA_DIR / name
    if not f.is_file():
        raise HTTPException(404, "expired or not found")
    mt = {"mp3": "audio/mpeg", "mp4": "video/mp4", "wav": "audio/wav"}[name.rsplit(".", 1)[1]]
    return FileResponse(f, media_type=mt, headers={"Cache-Control": "private, max-age=86400"})


# MCP (streamable HTTP) at /mcp — mounted last so the routes above take precedence.
app.mount("/", mcp_app)
