#!/usr/bin/env python3
"""Session ID ko safely .env me save karta hai (screen par echo nahi hota)."""

import getpass
import re
import sys
from pathlib import Path

ENV_FILE = Path(__file__).resolve().with_name(".env")


def set_value(key: str, value: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8")
    pattern = rf"(?m)^{re.escape(key)}=.*$"
    if re.search(pattern, text):
        text = re.sub(pattern, f"{key}={value}", text, count=1)
    else:
        text += f"\n{key}={value}\n"
    ENV_FILE.write_text(text, encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def main() -> None:
    if not ENV_FILE.exists():
        sys.exit(f"{ENV_FILE} nahi mila")

    # `python set_session.py --llm` => sirf LLM key set karo
    if "--llm" in sys.argv:
        key = getpass.getpass("LLM API key paste karo: ").strip()
        if not key:
            sys.exit("Kuch paste nahi hua.")
        set_value("LLM_API_KEY", key)
        print(f"[ok] LLM_API_KEY save ho gayi -> {ENV_FILE}")
        return

    sid = getpass.getpass("Session ID paste karo (typing dikhegi nahi): ").strip()
    if not sid:
        sys.exit("Kuch paste nahi hua, dobara try karo.")

    # safety: sessionid me space ya newline nahi hona chahiye
    sid = sid.replace(" ", "").replace("\n", "")
    if len(sid) < 30:
        sys.exit("Ye sessionid bahut chhoti lag rahi hai — poora value copy karo.")

    set_value("IG_SESSIONID", sid)
    print(f"[ok] IG_SESSIONID save ho gayi ({len(sid)} chars) -> {ENV_FILE}")

    if input("LLM API key bhi abhi daalni hai? (y/N): ").strip().lower() == "y":
        key = getpass.getpass("LLM API key paste karo: ").strip()
        if key:
            set_value("LLM_API_KEY", key)
            print("[ok] LLM_API_KEY save ho gayi")


if __name__ == "__main__":
    main()
