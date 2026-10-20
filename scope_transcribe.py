"""Site Scope Tool - speech to text for dictated site notes (phase 3).

Uses Deepgram's pre-recorded API with the Nova-3 model (chosen Oct 20, 2026:
about $0.0056/min with keyterm hints, and the $200 free credit never expires -
about 15 years at OCC's volume). AssemblyAI is the backup; switching means
rewriting only this file.

Deepgram downloads the audio itself from its Vercel Blob URL, so the audio
never passes through this server's memory. One request returns the finished
text - no polling.

    POST https://api.deepgram.com/v1/listen?model=nova-3&keyterm=...
    Authorization: Token <DEEPGRAM_API_KEY>
    {"url": "<audio url>"}

Standard library only.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.deepgram.com/v1/listen"
MODEL = "nova-3"
TIMEOUT_SECONDS = 120

# OCC trade words Deepgram should listen for. Deepgram caps the whole list at
# 500 tokens and suggests the 20-50 that matter most, so keep it focused on
# words it would otherwise mishear. Edit freely.
TRADE_KEYTERMS = [
    "CMU", "wainscot", "wainscoting", "LVP", "LVT", "soffit", "fascia", "sill", "header", "lintel",
    "GFCI", "AFCI", "drywall", "sheetrock", "baseboard", "quarter round", "shoe molding",
    "crown molding", "chair rail", "casing", "jamb", "joist", "subfloor", "rafter", "truss",
    "OSB", "Hardie", "LP SmartSide", "J-channel", "drip edge", "kick-out flashing", "downspout",
    "backsplash", "vanity", "P-trap", "PEX", "CPVC", "Romex", "receptacle", "sconce", "can light",
    "board and batten", "shiplap", "beadboard", "knockdown", "popcorn ceiling", "crawlspace",
    "vapor barrier", "sister", "pocket door", "bifold", "prehung", "egress", "double-hung",
    "casement", "transom", "baluster", "newel post", "stringer", "ledger board", "joist hanger",
    "Trex", "pressure treated", "thinset", "grout", "Schluter", "Durock", "Kerdi", "cement board",
    "quartz", "AFF", "on center", "Owners Choice",
]


class TranscribeError(Exception):
    pass


def api_key():
    return os.environ.get("DEEPGRAM_API_KEY", "").strip()


def is_configured():
    return bool(api_key())


def request_url(keyterms=None):
    params = [
        ("model", MODEL),
        ("smart_format", "true"),   # "twelve feet six" -> "12 feet 6"
        ("punctuate", "true"),
        ("paragraphs", "true"),
    ]
    params += [("keyterm", t) for t in (TRADE_KEYTERMS if keyterms is None else keyterms)]
    return API_URL + "?" + urllib.parse.urlencode(params)


def parse_response(body):
    """(text, duration_seconds) from a Deepgram response. Paragraph breaks
    come through as blank lines when Deepgram found them."""
    try:
        alt = body["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError, TypeError):
        raise TranscribeError("Deepgram's answer had no transcript in it.")
    text = ((alt.get("paragraphs") or {}).get("transcript") or alt.get("transcript") or "").strip()
    duration = (body.get("metadata") or {}).get("duration")
    return text, (float(duration) if isinstance(duration, (int, float)) else None)


def transcribe_url(audio_url):
    """Transcribe audio Deepgram can download. Returns (text, duration_seconds);
    text is "" if nobody spoke."""
    if not is_configured():
        raise TranscribeError("DEEPGRAM_API_KEY is not set on this server.")
    req = urllib.request.Request(
        request_url(),
        data=json.dumps({"url": audio_url}).encode("utf-8"),
        headers={"Authorization": f"Token {api_key()}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            return parse_response(json.loads(r.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 401:
            detail += " - Deepgram did not accept DEEPGRAM_API_KEY. Make a new key in the Deepgram console and put it in Render."
        raise TranscribeError(f"Deepgram returned HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise TranscribeError(f"Could not reach Deepgram: {e.reason}")
    except (ValueError, TimeoutError) as e:
        raise TranscribeError(f"Deepgram call failed: {e}")
