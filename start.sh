#!/usr/bin/env bash
# Bot start karo (background me)
cd "$(dirname "$0")" || exit 1

# bot.pid se check
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
  echo "Bot pehle se chal raha hai (pid $(cat bot.pid))"
  exit 0
fi

# manually start hua process lock file me pid rakhta hai
if [ -f .bot.lock ]; then
  lpid=$(cat .bot.lock 2>/dev/null || true)
  if [ -n "$lpid" ] && kill -0 "$lpid" 2>/dev/null; then
    echo "Bot pehle se chal raha hai (pid $lpid)"
    exit 0
  fi
fi

if [ ! -d .venv ]; then
  echo ".venv nahi mila. Pehle: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

setsid nohup .venv/bin/python -u bot.py run >> bot.log 2>&1 < /dev/null &
echo $! > bot.pid
sleep 3

if kill -0 "$(cat bot.pid)" 2>/dev/null; then
  echo "Bot ON (pid $(cat bot.pid))"
  echo "Log: $(pwd)/bot.log"
  tail -n 5 bot.log
else
  echo "Bot start nahi hua. Log dekho:"
  tail -n 20 bot.log
  rm -f bot.pid
  exit 1
fi
