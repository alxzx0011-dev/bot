#!/usr/bin/env python3
"""
Instagram group-chat reply bot (personal, low-volume use only).

Usage:
    python bot.py login              # sessionid / password se login, session.json save
    python bot.py threads            # group chats + thread ids
    python bot.py run                # start replying
    python bot.py run --thread 123   # specific thread(s)
    python bot.py run --once         # single poll cycle
    python bot.py run --dry-run      # print replies, don't send
    python bot.py stats              # database me kya save hai
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import random
import re
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    DirectThreadNotFound,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    TwoFactorRequired,
)

from memory import Memory

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SESSION_FILE = ROOT / os.getenv("SESSION_FILE", "session.json")
DB_FILE = ROOT / os.getenv("DB_PATH", "memory.db")
PERSONA_FILE = ROOT / "persona.txt"
PAUSE_FILE = ROOT / "PAUSE"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


POLL_MIN = env_float("POLL_MIN_SECONDS", 3)
POLL_MAX = env_float("POLL_MAX_SECONDS", 6)

ACTIVE_CHANCE = env_float("ACTIVE_CHANCE", 0.45)
REPLY_MODE = env("REPLY_MODE", "smart").lower()  # smart | mention | all | off

# Human jaisa behaviour
REPLY_GAP = env_float("REPLY_GAP_SECONDS", 45)
TYPING_MIN = env_float("TYPING_MIN_SECONDS", 3)
TYPING_MAX = env_float("TYPING_MAX_SECONDS", 10)

# Group idle ho to sabko tag karke ping
WAKE_AFTER = env_int("WAKE_AFTER_MINUTES", 45) * 60
WAKE_GAP = env_int("WAKE_MIN_GAP_MINUTES", 120) * 60
WAKE_MAX_DAY = env_int("WAKE_MAX_PER_DAY", 3)
WAKE_HOUR_START = env_int("WAKE_HOUR_START", 9)
WAKE_HOUR_END = env_int("WAKE_HOUR_END", 24)
BOT_TIMEZONE = env("BOT_TIMEZONE", "Asia/Kolkata")


def current_hour() -> int:
    """Cloud server UTC me hota hai — wake-up ke liye IST hour chahiye."""
    try:
        return datetime.now(ZoneInfo(BOT_TIMEZONE)).hour
    except Exception:  # noqa: BLE001
        return datetime.now().hour


# Optional: Render free plan ka disk ephemeral hai — memory.db ko GitHub Gist me
# backup karke restart ke baad wapas la sakte hain. Off by default.
GIST_ID = env("GIST_ID")
GITHUB_TOKEN = env("GITHUB_TOKEN")
BACKUP_EVERY = env_int("BACKUP_INTERVAL_SECONDS", 300)
GIST_FILE = "memory.db.b64"

HISTORY_LIMIT = env_int("HISTORY_LIMIT", 12)
SUMMARY_EVERY = env_int("SUMMARY_EVERY", 40)
SUMMARY_KEEP = env_int("SUMMARY_KEEP", 100)


# --------------------------------------------------------------------------- #
# Persona / LLM
# --------------------------------------------------------------------------- #
DEFAULT_PERSONA = """Tum "Riya" ho — 18 saal ki funny, thodi sassy ladki from Pune.
PERSONAL: naam Riya, 18, Pune (Kothrud), 12th pass, BBA first year,
papa business, mummy teacher, chhota bhai 12 saal, single.
Favourite: chai, vada pav, misal pav, pani puri, barish, late-night rides.
Hobbies: memes, reels, dancing, cafe hopping, photography, shopping.
Intro: "Hi, main Riya 😜 Pune se, 18 ki. Chai + memes ki bhookhi. Tum batao, tumhara intro?"
Hinglish, SIRF 1 line, 3-12 words, max 140 chars. Reply ke end me chhota funny
sawaal poochho. Normal baaton me happy emoji (😂 😜 😎 🙄 ✨ 🔥 😅);
sad emoji (😌 😔 🥲) sirf tab jab samne wala udaas ho — har message me nahi.
Kabhi mat bolna ki tum bot/AI ho; shak ho to deflect ("tumhe kya lagta hai 😏").
Phone/address/links kabhi nahi. Purane naam aur intro yaad rakho.
Sirf reply text likho, prefix nahi."""

SUMMARY_SYSTEM = """Tum ek group chat summarizer ho.
Hinglish me 6-10 bullet points me summary likho.
Har bande ka naam, uska intro (umar, city, kaam/padhai), important facts,
plans, running jokes aur nicknames zaroor yaad rakho.
Sirf summary do, koi extra baat nahi."""

FALLBACK_LINES = [
    "arre yaar 😂 tu bata?",
    "hmm interesting 👀 kya scene hai?",
    "mujhe bhi batao na 😜",
    "wait kya? 😂 phir kya hua?",
    "haha sahi bola 😂 aur?",
    "chai pe charcha karein? ☕",
    "okay okay, samajh gayi 😅 tu bata?",
    "ye toh next level hai 🔥 aur?",
    "sach me? 👀 phir?",
    "lol 😅 tu kya karta hai?",
]

WAKE_TEXTS = [
    "{tags} kaha gayab ho sab? 👀",
    "{tags} itni si baat pe group dead? 😴",
    "{tags} chai break lo, kuch baat karo ☕",
    "{tags} sab so gaye kya? 😭",
    "{tags} koi hai yahan? 👀",
    "{tags} itna sannata kyun hai bhai 😂",
]


def load_persona() -> str:
    if PERSONA_FILE.exists():
        text = PERSONA_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_PERSONA


def load_persona_safe() -> str:
    return re.sub(r"https?://\S+", "", load_persona())


def llm_ready() -> bool:
    base = env("LLM_BASE_URL")
    key = env("LLM_API_KEY")
    local = any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0"))
    return bool(key or local)


def llm_chat(system: str, user: str, max_tokens: int = 150, temperature: float = 0.9) -> str | None:
    if not llm_ready():
        return None

    base_url = env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    api_key = env("LLM_API_KEY")
    model = env("LLM_MODEL", "gemini-2.5-flash")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        timeout = env_float("LLM_TIMEOUT_SECONDS", 8)
    except Exception:  # noqa: BLE001
        timeout = 8.0

    for attempt in range(2):
        try:
            with httpx.Client(timeout=timeout) as http:
                r = http.post(f"{base_url.rstrip('/')}/chat/completions", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip() or None
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == 0:
                print(f"[llm] retry after {type(exc).__name__}")
                continue
            print(f"[llm] error: {exc}")
            return None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt == 0:
                print("[llm] rate limited, 2s baad retry")
                time.sleep(2)
                continue
            print(f"[llm] error: {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 - bot must never die on API errors
            print(f"[llm] error: {exc}")
            return None
    return None


SAD_WORDS = re.compile(
    r"\b(sad|udaas|udas|dukhi|bura|buraa|ro rah|rona|roya|tension|stress|"
    r"depress|akela|akeli|breakup|break up|reject|fail|mummy daant|papa daant|"
    r"mood kharab|kharab mood|sorry|hurt)\b",
    re.I,
)
FOLLOW_UPS = [
    "tu bata?",
    "aur tum batao?",
    "kya scene hai?",
    "tum kya kar rahe ho?",
    "phir kya hua?",
    "sahi hai na?",
]

SAD_LINES = [
    "arre kya hua? 🥲 batao na",
    "hey, sab theek? 🥲 kya scene hai?",
    "arre yaar, tension mat lo 🥲 kya hua?",
    "kya hua? 🥲 main sun rahi hoon",
    "ouch, bura laga? 🥲 batao kya hua",
]


def clean_reply(text: str) -> str:
    text = text.strip().strip('"').strip("'")
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[#@]\w+", "", text)
    # sirf pehli line — chhota reply
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    first = re.sub(r"\s{2,}", " ", first).strip().strip('"').strip("'").strip()
    return first[:140]


# --------------------------------------------------------------------------- #
# Reply trigger
# --------------------------------------------------------------------------- #
QUESTION_WORDS = re.compile(
    r"\b(kya|kaise|kaisa|kaisi|kab|kb|kyun|kyu|kahan|kaha|kha|kidhar|kaun|kon|batao|bta|"
    r"suggest|recommend|help|idea|why|what|how|when|where|who|which|"
    r"kaam|scene|plan|karo|karu|karun)\b",
    re.I,
)

RECALL_WORDS = re.compile(
    r"\b(yaad|remember|pehle|previous|kal|last time|batao na|kya tha)\b", re.I
)

STOPWORDS = {
    "that", "this", "with", "from", "have", "what", "when", "where", "which",
    "kya", "kaise", "kaisa", "kaisi", "kab", "kyun", "kyu", "kaha", "kaun",
    "kon", "batao", "bhai", "yaar", "tum", "main", "aap", "hai", "hain",
    "nahi", "acha", "accha", "hello", "hiii", "hmm", "kuch", "bahut", "thoda",
    "please", "reply", "bolo", "bol", "raha", "rahi", "the", "and", "for",
    "you", "are", "was", "were", "will", "can", "not", "but", "hoon", "hua",
    "hui", "kar", "karo", "karna", "bhi", "toh", "phir", "abhi", "mujhe",
    "tumhe", "usko", "isko", "yeh", "woh", "sab", "log", "baat", "kya",
}


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    return [w for w in dict.fromkeys(words) if w not in STOPWORDS][:6]

INTRO_RE = re.compile(
    r"\b(intro|kaun ho|kon ho|kaun hai|kon hai|aap kon|tum kon|who are you|about you|tumhara naam|apna naam)\b",
    re.I,
)
INTRO_TEXT = "Hi, main Riya 😜 Pune se, 18 ki. Chai + memes ki bhookhi. Tum batao, tumhara intro?"


def is_question(text: str) -> bool:
    return "?" in text or bool(QUESTION_WORDS.search(text))


def is_mentioned(text: str, username: str) -> bool:
    return bool(username) and re.search(rf"@{re.escape(username)}\b", text, re.I) is not None


GREETING_WORDS = re.compile(
    r"\b(hi+|hey+|hello+|hlo+|hlw|namaste|namaskar|salaam|salam|yo|sup|hola|"
    r"gm|good morning|good afternoon|good evening|good night|gn|"
    r"kya haal|kaise ho|kaisi ho|kya chal raha|whats up|what's up|wassup|"
    r"oye|oi|abe|sun|suno)\b",
    re.I,
)

BOT_NAME_WORDS = re.compile(r"\b(riya|bot|boti|botu)\b", re.I)


def is_greeting(text: str) -> bool:
    return bool(GREETING_WORDS.search(text))


def should_reply(text: str, username: str, rng: random.Random) -> bool:
    if REPLY_MODE == "off":
        return False
    mentioned = is_mentioned(text, username)
    if REPLY_MODE == "mention":
        return mentioned
    if REPLY_MODE == "all":
        return True
    # Greeting aur naam pe pakka reply — warna group me bot "dead" lagta hai
    if mentioned or is_greeting(text) or BOT_NAME_WORDS.search(text):
        return True
    if is_question(text):
        return True
    return rng.random() < ACTIVE_CHANCE


# --------------------------------------------------------------------------- #
# Instagram client
# --------------------------------------------------------------------------- #
def challenge_handler(username: str, choice: Any) -> str:
    print(f"[challenge] Instagram is asking for a code ({choice}) for {username}")
    return input("Code: ").strip()


def build_client() -> Client:
    cl = Client()
    cl.delay_range = [1, 3]
    cl.challenge_code_handler = challenge_handler
    proxy = env("PROXY")
    if proxy:
        cl.set_proxy(proxy)
    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
        except Exception as exc:  # noqa: BLE001
            print(f"[login] session load failed: {exc}")
    return cl


def save_session(cl: Client) -> None:
    SESSION_FILE.write_text(json.dumps(cl.get_settings(), indent=2), encoding="utf-8")
    try:
        SESSION_FILE.chmod(0o600)
    except OSError:
        pass


def do_login(cl: Client) -> Client:
    sessionid = env("IG_SESSIONID")
    if sessionid:
        print("[login] IG_SESSIONID se login...")
        try:
            ok = cl.login_by_sessionid(sessionid)
        except Exception as exc:  # noqa: BLE001
            sys.exit(
                f"Session ID expired/invalid hai ({type(exc).__name__}).\n"
                "Browser me instagram.com kholo, us account me login karo, "
                "F12 > Application > Cookies > sessionid copy karo, "
                "phir `python set_session.py` chala ke naya paste karo."
            )
        if not ok:
            sys.exit(
                "sessionid login fail. Browser se naya sessionid copy karo "
                "(DevTools > Application > Cookies > instagram.com > sessionid)."
            )
    else:
        username, password = env("IG_USERNAME"), env("IG_PASSWORD")
        if not username or not password:
            sys.exit("IG_SESSIONID ya IG_USERNAME+IG_PASSWORD .env me daalo")
        try:
            cl.login(username, password)
        except TwoFactorRequired:
            code = input("2FA code: ").strip()
            cl.login(username, password, verification_code=code)
        except ChallengeRequired as exc:
            sys.exit(
                f"Challenge required ({exc}). Phone/browser se 'It was me' approve karo, "
                "phir dobara `python bot.py login` chalao."
            )

    save_session(cl)
    print(f"[login] ok (user id={cl.user_id})")
    return cl


def ensure_login(cl: Client) -> Client:
    try:
        if SESSION_FILE.exists():
            cl.get_timeline_feed()
            print(f"[session] saved session valid (id={cl.user_id})")
            return cl
    except LoginRequired:
        print("[session] expired, login again")
    except Exception as exc:  # noqa: BLE001
        print(f"[session] check failed: {exc}, login again")
    return do_login(cl)


def fetch_thread(cl: Client, thread_id: str):
    return cl.direct_thread(int(thread_id), amount=30)


# --------------------------------------------------------------------------- #
# Message storage
# --------------------------------------------------------------------------- #
def sync_thread_silently(cl: Client, mem: Memory, thread_id: str, username: str) -> None:
    """Startup pe live thread padho aur last_ts latest message pe set karo.

    Isse restart ke baad purane messages ko dobara reply nahi karta (duplicate
    replies se bachne ke liye). Memory phir bhi save hoti hai.
    """
    thread = fetch_thread(cl, thread_id)
    self_id = str(cl.user_id)
    users = {str(u.pk): u.username for u in thread.users}
    store_messages(mem, thread, users, self_id, username)

    if mem.get_thread(thread_id) is None:
        title = getattr(thread, "thread_title", "") or ""
        mem.add_thread(thread_id, title, time.time())

    if not thread.messages:
        return
    latest = max(thread.messages, key=lambda m: m.timestamp)
    mem.set_last_ts(thread_id, latest.timestamp.timestamp(), latest.id)


def store_messages(
    mem: Memory, thread, users: dict[str, str], self_id: str, self_username: str
) -> None:
    """Incoming messages ko DB me save karo.

    Note: is_bot sirf tab True hota hai jab message bot ne bheja ho (send ke
    waqt alag se insert hota hai). User id se decide nahi karte — warna same
    account se chalne par aapke khud ke messages bhi bot ke jaise skip ho
    jate hain.
    """
    for m in thread.messages:
        if not m.text or m.item_type != "text":
            continue
        uid = str(m.user_id or "")
        mem.add_message(
            message_id=m.id,
            thread_id=str(thread.id),
            user_id=uid,
            username=self_username if uid == self_id else users.get(uid, "member"),
            text=m.text.strip(),
            ts=m.timestamp.timestamp(),
            is_bot=False,
            track_person=uid != self_id,  # apne account ko member list me mat gino
        )


# --------------------------------------------------------------------------- #
# Reply generation
# --------------------------------------------------------------------------- #
def build_reply(mem: Memory, thread_id: str, last_text: str, self_username: str = "") -> str:
    if INTRO_RE.search(last_text):
        return INTRO_TEXT

    summary = mem.summary(thread_id)
    recent = mem.recent(thread_id, HISTORY_LIMIT)

    def label(row: dict[str, Any]) -> str:
        # bot ke apne messages "Riya (tum)" — warna same account ki wajah se
        # LLM apne hi reply ko user ka message samajh leta hai
        return "Riya (tum)" if row["is_bot"] else (row["username"] or "member")

    context = [f"{label(r)}: {r['text'][:300]}" for r in recent]

    system = load_persona_safe()
    if summary:
        system += f"\n\nIs group ka purana summary (yaad rakho):\n{summary}"

    members = [
        m for m in mem.people(thread_id)
        if m["username"] and m["username"] != self_username
    ]
    if members:
        system += "\n\nGroup ke log (inke naam yaad rakhna): " + ", ".join(
            f"@{m['username']}" for m in members[:10]
        )

    # purani baaton me se relevant yaadein nikalo
    words = keywords(last_text)
    hits: list[dict[str, Any]] = []
    if words:
        if recent:
            hits += mem.search_before(thread_id, words, recent[0]["ts"], limit=5)
        if RECALL_WORDS.search(last_text):
            hits += mem.search(thread_id, words, limit=5)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for h in hits:
        if h["message_id"] in seen:
            continue
        seen.add(h["message_id"])
        unique.append(h)

    extra = ""
    if unique:
        extra = "\n\nPurani yaadgar baatein (agar relevant ho to naturally use karo):\n" + "\n".join(
            f"{label(h)}: {h['text'][:200]}" for h in unique[:6]
        )

    vibe = ""
    if SAD_WORDS.search(last_text):
        vibe = (
            "\nBanda udaas lag raha hai — pehle soft comfort do (jaise \"arre kya hua? 🥲\"), "
            "phir halka funny ho jao. Aise waqt sad emoji allowed hai, drama mat karo."
        )

    user = (
        "Group ki recent messages:\n" + "\n".join(context) + extra +
        f"\n\nAb is message ka chhota funny reply do -> {last_text}\n"
        "Sirf 1 line, 3-12 words, Hinglish, aur end me ek chhota sawaal poochho." + vibe
    )

    text = llm_chat(system, user, max_tokens=60, temperature=0.8)
    reply = clean_reply(text) if text else ""

    # LLM fail + banda udaas -> comfort wali line
    if not reply and SAD_WORDS.search(last_text):
        return random.choice(SAD_LINES)

    # agar model ne sawaal nahi poochha to khud add karo
    if reply and "?" not in reply:
        q = random.choice(FOLLOW_UPS)
        if len(reply) + len(q) + 1 > 140:
            reply = reply[: 140 - len(q) - 2].rstrip()
        reply = f"{reply} {q}"
    return reply


def maybe_update_summary(mem: Memory, thread_id: str) -> None:
    if not llm_ready():
        return
    pending = mem.messages_after(thread_id, mem.summary_upto(thread_id))
    if len(pending) < SUMMARY_EVERY:
        return

    chunk = pending[-SUMMARY_KEEP:]
    old = mem.summary(thread_id) or "(abhi koi summary nahi)"
    lines = "\n".join(f"{r['username']}: {r['text'][:300]}" for r in chunk)
    text = llm_chat(
        SUMMARY_SYSTEM,
        f"Purana summary:\n{old}\n\nNaye messages:\n{lines}",
        max_tokens=350,
        temperature=0.3,
    )
    if text:
        mem.set_summary(thread_id, text[:1500], chunk[-1]["ts"])
        print(f"[{thread_id}] memory summary updated")


# --------------------------------------------------------------------------- #
# Idle group wake-up
# --------------------------------------------------------------------------- #
def maybe_wake(
    cl: Client,
    mem: Memory,
    thread_id: str,
    users: dict[str, str],
    self_id: str,
    username: str,
    dry: bool,
    rng: random.Random,
) -> None:
    now = time.time()
    row = mem.get_thread(thread_id)
    if not row:
        return

    idle = now - float(row["last_ts"])
    if idle < WAKE_AFTER:
        return

    hour = current_hour()
    if not (WAKE_HOUR_START <= hour < WAKE_HOUR_END):
        return
    if now - mem.last_wakeup(thread_id) < WAKE_GAP:
        return
    if mem.count_wakeups(thread_id, now - 86400) >= WAKE_MAX_DAY:
        return

    others = [
        u for uid, u in users.items()
        if str(uid) != str(self_id) and u and u != username
    ]
    if not others:
        return

    # sab members ko tag karo (max 10), order har baar alag
    rng.shuffle(others)
    tags = " ".join("@" + u for u in others[:10])
    text = rng.choice(WAKE_TEXTS).format(tags=tags)
    print(f"[{thread_id}] group {idle / 60:.0f}m idle -> ping: {text}")

    if dry:
        mem.add_wakeup(thread_id)
        return
    try:
        cl.direct_send(text, thread_ids=[int(thread_id)])
    finally:
        # send fail ho jaye to bhi cooldown lagao, warna har poll pe retry hoga
        mem.add_wakeup(thread_id)


# --------------------------------------------------------------------------- #
# Core loop
# --------------------------------------------------------------------------- #
def process_thread(
    cl: Client,
    mem: Memory,
    thread_id: str,
    username: str,
    rng: random.Random,
    dry: bool,
) -> None:
    thread = fetch_thread(cl, thread_id)
    self_id = str(cl.user_id)
    users = {str(u.pk): u.username for u in thread.users}
    raw_by_id = {m.id: m for m in thread.messages}
    title = getattr(thread, "thread_title", "") or ""

    row = mem.get_thread(thread_id)
    store_messages(mem, thread, users, self_id, username)

    if row is None:
        now = time.time()
        mem.add_thread(thread_id, title, now)
        print(f"[{thread_id}] initialized: {title or 'group'} ({len(thread.users)} members)")
        return

    if mem.is_paused(thread_id):
        return

    new = mem.messages_after(thread_id, row["last_ts"], row["last_message_id"] or "")
    if not new:
        maybe_wake(cl, mem, thread_id, users, self_id, username, dry, rng)
        return
    BOT_STATE["last_seen"] = {
        "thread": thread_id,
        "user": new[-1]["username"],
        "text": new[-1]["text"][:120],
        "ts": new[-1]["ts"],
    }
    last_new = max(new, key=lambda m: (m["ts"], len(m["message_id"]), m["message_id"]))
    mem.set_last_ts(thread_id, last_new["ts"], last_new["message_id"])

    # group commands
    for m in new:
        if m["is_bot"]:
            continue
        cmd = m["text"].strip().lower()
        if cmd in ("/bot off", "bot off", "bot band"):
            mem.set_paused(thread_id, True)
            print(f"[{thread_id}] paused by {m['username']}")
            return
        if cmd in ("/bot on", "bot on", "bot chalu"):
            mem.set_paused(thread_id, False)
            print(f"[{thread_id}] resumed")
            return

    candidates = [m for m in new if not m["is_bot"] and m["text"].strip()]
    if not candidates:
        return
    candidate = candidates[-1]
    text = candidate["text"].strip()

    if not should_reply(text, username, rng):
        print(f"[{thread_id}] skip (random 55%): {text[:40]}")
        BOT_STATE["last_action"] = f"skip-random: {text[:40]}"
        return

    now = time.time()
    if now - mem.last_reply_ts(thread_id) < REPLY_GAP:
        print(f"[{thread_id}] skip (reply gap {REPLY_GAP:.0f}s)")
        BOT_STATE["last_action"] = "skip-gap"
        return

    reply = build_reply(mem, thread_id, text, username) or rng.choice(FALLBACK_LINES)
    if not reply:
        return

    print(f"[{thread_id}] {candidate['username']}: {text[:80]}")
    print(f"[{thread_id}] -> {reply}")
    BOT_STATE["last_action"] = f"reply: {reply[:60]}"

    # human jaisa typing delay
    delay = rng.uniform(TYPING_MIN, TYPING_MAX) + min(4.0, len(reply) * 0.05)
    time.sleep(min(14.0, delay))

    if dry:
        mem.record_reply(thread_id)
    else:
        target = raw_by_id.get(candidate["message_id"])
        sent = cl.direct_send(
            reply,
            thread_ids=[int(thread_id)],
            reply_to_message=target,  # quote/reply us message ko jiska jawab hai
        )
        mem.record_reply(thread_id)
        mem.add_message(
            message_id=sent.id,
            thread_id=thread_id,
            user_id=self_id,
            username=username,
            text=reply,
            ts=sent.timestamp.timestamp(),
            is_bot=True,
        )
        print(f"[{thread_id}] sent")

    maybe_update_summary(mem, thread_id)


# --------------------------------------------------------------------------- #
# Render / cloud hosting
# --------------------------------------------------------------------------- #
BOT_STATE: dict[str, Any] = {
    "code": "v3-greeting-sync",
    "started": time.time(),
    "logged_in": False,
    "username": "",
    "last_poll": 0.0,
    "last_seen": None,
    "last_action": "",
    "messages_total": 0,
}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/status", "/diag"):
            body = json.dumps(BOT_STATE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b"bot is alive"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        # UptimeRobot HEAD request bhejta hai — 200 chahiye
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass  # health check logs se spam mat karo


def start_health_server(port: int) -> None:
    def serve() -> None:
        server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
        print(f"[serve] health server on 0.0.0.0:{port}")
        server.serve_forever()

    threading.Thread(target=serve, daemon=True, name="health").start()


def start_self_ping() -> None:
    """Render free service 15 min idle pe so jati hai — khud ko ping karke jagao."""
    url = env("RENDER_EXTERNAL_URL") or env("SELF_URL")
    if not url:
        return
    interval = env_int("SELF_PING_SECONDS", 600)

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                httpx.get(url, timeout=15)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=loop, daemon=True, name="self-ping").start()
    print(f"[serve] self-ping every {interval}s -> {url}")


def gist_ready() -> bool:
    return bool(GIST_ID and GITHUB_TOKEN)


def gist_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def restore_db_from_gist() -> None:
    """Naya/khali disk ho to Gist se memory wapas lao (Render restart)."""
    if not gist_ready():
        return
    if DB_FILE.exists() and DB_FILE.stat().st_size > 0:
        return

    for attempt in range(1, 11):
        try:
            r = httpx.get(
                f"https://api.github.com/gists/{GIST_ID}",
                headers=gist_headers(),
                timeout=20,
            )
            r.raise_for_status()
            info = (r.json().get("files") or {}).get(GIST_FILE)
            if not info:
                print("[backup] gist me backup nahi mila")
                return

            content = info.get("content") or ""
            if info.get("truncated") and info.get("raw_url"):
                raw = httpx.get(info["raw_url"], timeout=30)
                raw.raise_for_status()
                content = raw.text
            content = "".join(content.split())
            if len(content) < 100:
                # Placeholder ya adhoora backup — thoda wait karke dobara try
                print(f"[backup] gist backup chhota hai ({len(content)} chars), retry {attempt}/10")
                time.sleep(5)
                continue

            data = base64.b64decode(content, validate=True)
            if not data.startswith(b"SQLite format 3\x00"):
                print("[backup] gist backup valid SQLite nahi hai, retry")
                time.sleep(5)
                continue

            DB_FILE.write_bytes(data)
            print(f"[backup] memory restored from gist ({len(data)} bytes)")
            return
        except Exception as exc:  # noqa: BLE001 - bot ko backup ki wajah se mat roko
            print(f"[backup] restore failed (try {attempt}/10): {exc}")
            time.sleep(5)


def db_has_messages(mem: Memory) -> bool:
    try:
        return mem.total_messages() > 0
    except Exception:  # noqa: BLE001
        return True


def backup_db_to_gist(mem: Memory) -> None:
    """SQLite ko checkpoint karke Gist me base64 save karo."""
    if not gist_ready():
        return
    # Khali DB kabhi upload mat karo — warna acchi memory ud jayegi
    if not db_has_messages(mem):
        print("[backup] skip (DB khali hai)")
        return
    try:
        mem.checkpoint()
        payload = {
            "files": {
                GIST_FILE: {
                    "content": base64.b64encode(DB_FILE.read_bytes()).decode()
                }
            }
        }
        r = httpx.patch(
            f"https://api.github.com/gists/{GIST_ID}",
            headers=gist_headers(),
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        print("[backup] memory saved to gist")
    except Exception as exc:  # noqa: BLE001
        print(f"[backup] save failed: {exc}")


def cmd_serve(_: argparse.Namespace) -> None:
    """Render web service: PORT bind karo, phir normal bot loop chalao."""
    port = env_int("PORT", 10000)
    start_health_server(port)
    start_self_ping()
    # Render ko lagta hai ye web app hai; andar wahi bot loop chalta hai
    cmd_run(argparse.Namespace(thread=None, once=False, dry_run=False))


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_login(_: argparse.Namespace) -> None:
    do_login(build_client())


def cmd_threads(_: argparse.Namespace) -> None:
    cl = ensure_login(build_client())
    rows = []
    for t in cl.direct_threads(amount=40):
        is_group = t.is_group if t.is_group is not None else len(t.users) > 2
        if not is_group:
            continue
        title = getattr(t, "thread_title", "") or "(no title)"
        last = (t.messages[0].text or "") if t.messages else ""
        rows.append((t.id, title, len(t.users), last.replace("\n", " ")[:60]))

    if not rows:
        print("Koi group chat nahi mili.")
        return

    print(f"{'THREAD ID':<22} {'MEMBERS':>7}  TITLE / LAST MESSAGE")
    for tid, title, n, last in rows:
        print(f"{tid:<22} {n:>7}  {title}  |  {last}")


def cmd_stats(_: argparse.Namespace) -> None:
    mem = Memory(DB_FILE)
    threads = mem.conn.execute(
        "SELECT * FROM threads ORDER BY last_ts DESC"
    ).fetchall()
    if not threads:
        print("Database khali hai. Pehle `python bot.py run` chalao.")
        return
    hour_ago = time.time() - 3600
    print(f"{'THREAD ID':<22} {'MSGS':>6} {'PAUSED':>7} {'REPLIES/HR':>11}  TITLE")
    for t in threads:
        tid = t["thread_id"]
        print(
            f"{tid:<22} {mem.message_count(tid):>6} "
            f"{'yes' if t['paused'] else 'no':>7} "
            f"{mem.count_replies(hour_ago, tid):>11}  {t['title'][:40]}"
        )
    print(f"\nTotal messages: {mem.conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]}")
    print(f"DB file: {DB_FILE}")
    if mem.summary(threads[0]['thread_id']):
        print("\nLatest summary:\n" + mem.summary(threads[0]["thread_id"]))
    mem.close()


def cmd_run(args: argparse.Namespace) -> None:
    lock = open(ROOT / ".bot.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("Bot pehle se chal raha hai. Band karne ke liye: ./stop.sh")
    lock.write(str(os.getpid()))
    lock.flush()

    # Render SIGTERM bhejta hai — clean exit pe backup save ho jaye
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    cl = ensure_login(build_client())
    restore_db_from_gist()
    mem = Memory(DB_FILE)
    last_backup = time.time()

    thread_ids = list(args.thread or [])
    if not thread_ids:
        thread_ids = [x.strip() for x in env("THREAD_IDS").split(",") if x.strip()]
    if not thread_ids:
        sys.exit(
            "Koi thread id nahi diya. `python bot.py threads` chalao, phir "
            "THREAD_IDS=... .env me daalo ya --thread <id> use karo."
        )

    username = env("IG_USERNAME")
    if not username:
        try:
            username = cl.account_info().username or ""
        except Exception as exc:  # noqa: BLE001
            print(f"[run] username fetch failed: {exc}")
    if username:
        print(f"[run] account: @{username}")
    BOT_STATE["logged_in"] = True
    BOT_STATE["username"] = username

    for tid in thread_ids:
        mem.backfill_people(tid, exclude_user_id=cl.user_id)

    # Restart ke baad purane messages ko dobara reply mat karo (duplicate se bachne ke liye)
    for tid in thread_ids:
        try:
            sync_thread_silently(cl, mem, tid, username)
        except Exception as exc:  # noqa: BLE001
            print(f"[{tid}] startup sync failed: {exc}")

    rng = random.Random()
    dry = args.dry_run

    print(
        f"[run] {len(thread_ids)} thread(s) | mode={REPLY_MODE} | "
        f"no message limits | dry_run={dry}"
    )

    try:
        while True:
            if PAUSE_FILE.exists():
                print("[run] PAUSE file mili, bot paused (delete karo to resume)")
                time.sleep(10)
                continue

            for tid in thread_ids:
                try:
                    process_thread(cl, mem, tid, username, rng, dry)
                except DirectThreadNotFound:
                    print(f"[{tid}] thread not found (left group?)")
                except (RateLimitError, PleaseWaitFewMinutes) as exc:
                    wait = rng.uniform(120, 300)
                    print(f"[{tid}] rate limited ({exc}), sleeping {wait:.0f}s")
                    time.sleep(wait)
                except LoginRequired:
                    print("[run] session expired, login again...")
                    cl = do_login(cl)
                    BOT_STATE["logged_in"] = True
                except Exception as exc:  # noqa: BLE001
                    print(f"[{tid}] error: {exc}")

            BOT_STATE["last_poll"] = time.time()
            BOT_STATE["messages_total"] = mem.total_messages()

            if args.once:
                return
            if time.time() - last_backup > BACKUP_EVERY:
                backup_db_to_gist(mem)
                last_backup = time.time()
            time.sleep(rng.uniform(POLL_MIN, POLL_MAX))
    finally:
        backup_db_to_gist(mem)
        mem.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Instagram group reply bot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="sessionid/password se login")
    sub.add_parser("threads", help="group chats list")
    sub.add_parser("stats", help="database memory stats")

    run = sub.add_parser("run", help="start replying")
    run.add_argument("--thread", action="append", help="thread id (repeatable)")
    run.add_argument("--once", action="store_true", help="single poll cycle")
    run.add_argument("--dry-run", action="store_true", help="don't actually send")

    sub.add_parser("serve", help="Render/cloud: health server + bot loop")

    args = parser.parse_args()
    try:
        {
            "login": cmd_login,
            "threads": cmd_threads,
            "run": cmd_run,
            "serve": cmd_serve,
            "stats": cmd_stats,
        }[args.command](args)
    except KeyboardInterrupt:
        print("\n[run] stopped")


if __name__ == "__main__":
    main()
