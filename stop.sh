#!/usr/bin/env bash
# Bot band karo
cd "$(dirname "$0")" || exit 1

is_bot_pid() {
  # sirf tab kill karo jab process really bot ho (pid recycle se bachne ke liye)
  [ -r "/proc/$1/cmdline" ] && tr '\0' ' ' < "/proc/$1/cmdline" | grep -q "bot.py run"
}

pid=""
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null && is_bot_pid "$(cat bot.pid)"; then
  pid=$(cat bot.pid)
elif [ -f .bot.lock ]; then
  lpid=$(cat .bot.lock 2>/dev/null || true)
  if [ -n "$lpid" ] && kill -0 "$lpid" 2>/dev/null && is_bot_pid "$lpid"; then
    pid=$lpid
  fi
fi

if [ -z "$pid" ]; then
  echo "Bot chal nahi raha tha"
  rm -f bot.pid
  exit 0
fi

kill "$pid" 2>/dev/null
# process band hone ka wait karo, warna turant start karne pe race ho sakti hai
for _ in $(seq 1 30); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.1
done
kill -9 "$pid" 2>/dev/null
rm -f bot.pid
echo "Bot OFF"
