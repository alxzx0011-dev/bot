# Instagram Group Reply Bot

Ek chhota Python bot jo aapke Instagram **group chat** me reply karta hai — ek
funny ladki "Riya" ki tarah, Hinglish me.

- Human jaisa behaviour — 45 sec gap, 3-10 sec typing delay, har message pe nahi
- Session-id login (password ki zaroorat nahi)
- SQLite database jo **poori chat aur logon ke naam/intro yaad rakhta hai**
- Ek LLM model, config se koi bhi OpenAI-compatible provider
- Group idle ho to members ko `@` karke ping

> **Warning:** Instagram ki official API me group chats ka access nahi hai. Ye bot
> `instagrapi` (unofficial) use karta hai, jo Instagram Terms of Service ke against hai.
> Account ban ho sakta hai. **Throwaway account** hi use karo.

## Features

- Group chats me reply (1:1 DMs skip)
- `smart` mode: question + `@mention` pe pakka, normal messages pe ~45% (human jaisa)
- **Human pacing**: reply ke beech gap + typing delay, poora din chat me nahi bolta
- **Idle ping**: group 45 min chup ho to sab members ko `@` karke bulata hai
- LLM reply (Gemini/OpenAI/Groq/Ollama) — key na ho to built-in funny lines
- **Memory database**: har message DB me, recent context + purani baaton ka auto-summary
- **People memory**: group members ke username/naam yaad rakhta hai
- "yaad hai?" type sawaalon pe purane messages DB se dhoond ke reply
- `sessionid` login + `session.json` (dobara login nahi)
- `PAUSE` file ya group me `/bot off` se pause
- `--dry-run` se bina bheje test
- `stats` command se DB memory dekhna

## Setup

```bash
cd instagram-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

### 1. Session ID nikaalo (recommended)

1. Browser me **instagram.com** login karo
2. `F12` → **Application** tab → **Cookies** → `https://www.instagram.com`
3. `sessionid` naam ki cookie ka value copy karo (bahut lamba hota hai)
4. `.env` me `IG_SESSIONID=...` paste karo

`sessionid` = poora account access. Ise password ki tarah treat karo, kabhi share mat karo.

Password se login karna ho to `IG_USERNAME` + `IG_PASSWORD` bhar do — bot pehle sessionid
try karega, warna password.

### 2. Login

```bash
python bot.py login
```

### 3. Group chat ka thread id dhundo

```bash
python bot.py threads
```

`THREAD ID` column copy karo aur `.env` me daalo:

```
THREAD_IDS=12345678901234567
```

Ya direct: `python bot.py run --thread 12345678901234567`

### 4. Dry-run, phir asli run

```bash
python bot.py run --dry-run
python bot.py run
```

## Commands

```bash
python bot.py login                 # sessionid/password se login
python bot.py threads               # group chats list
python bot.py stats                 # DB memory + replies/hour
python bot.py run                   # start
python bot.py run --once            # ek poll cycle (testing)
python bot.py run --dry-run         # print only, no send
python bot.py run --thread 123 --thread 456
python bot.py serve                 # Render/cloud: health server + bot loop
```

Group me `/bot off` likho to wo group pause, `/bot on` se resume.
Root me `PAUSE` file banao to poora bot ruk jayega.

## Memory (database)

`memory.db` SQLite file me:

| Table | Kya hai |
|---|---|
| `messages` | Har text message — user, text, timestamp |
| `threads` | Har group, last seen time, paused, summary |
| `replies` | Bot ke replies (stats ke liye) |

- LLM ko last `HISTORY_LIMIT=25` messages context me milte hain
- Har `SUMMARY_EVERY=60` naye messages ke baad purani baaton ka summary ban jaata hai
- Summary + recent messages dono se bot ko "sab yaad" rehta hai
- Purani baat poochhi jaye ("yaad hai kal kya hua tha?") to DB me keyword search hoti hai

DB delete karna ho to: `rm memory.db` (bot dobara bana lega, purani yaadein chali jayengi).

## LLM options (`.env`)

| Provider | `LLM_BASE_URL` | `LLM_MODEL` |
|---|---|---|
| Gemini (free tier) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Groq | `https://api.groq.com/openai/v1` | `qwen/qwen3.8-27b` |
| Ollama (local, free) | `http://localhost:11434/v1` | `llama3.1` |

## Persona badalna

`persona.txt` edit karo — wahi "Riya" ki personality hai (18 saal, Pune, BBA first
year, chai/memes, thoda attitude). Usme hi common sawaalon ke fixed jawab bhi hain
taaki naam/umar/city kabhi galat na bole.

## Background me chalana

```bash
nohup python bot.py run > bot.log 2>&1 &

# ya tmux (recommended)
tmux new -s igbot
python bot.py run
# Ctrl+B phir D se detach
```

Logs: `tail -f bot.log` ya `tmux attach -t igbot`.

## Render par host karna (free)

Bot ek **web service** ki tarah chalta hai — andar bot loop chalta hai, bahar `/`
health endpoint hota hai (Render ko web app chahiye hota hai).

### 1. GitHub repo banao

Ye folder ek git repo nahi hai. GitHub par naya **private** repo banao (naam jaise
`bot`), phir:

```bash
cd instagram-bot
git init
git add .
git commit -m "Riya bot"
git branch -M main
git remote add origin https://github.com/<your-username>/bot.git
git push -u origin main
```

`.env`, `session.json`, `memory.db`, `bot.log` already gitignored hain — secrets
push nahi honge.

### 2. Render par deploy

1. [render.com](https://render.com) → **New +** → **Blueprint**
2. Apna GitHub repo connect karo
3. Render `render.yaml` padh lega → **Apply**
4. Dashboard → service → **Environment** me ye 2 secrets bharo:
   - `IG_SESSIONID` — browser cookie
   - `LLM_API_KEY` — Groq key
5. Deploy hone ke baad URL kholo — `bot is alive` dikhna chahiye

Start command: `python bot.py serve` (health server + bot loop).
Region Singapore, plan Free. Free plan ~750 hours/month deta hai.

### 3. Free instance ko jagta rakho

Render free service **15 min bina traffic** ke so jati hai. [UptimeRobot](https://uptimerobot.com)
ya [cron-job.org](https://cron-job.org) se apne Render URL par har **5-10 min**
HTTP ping lagao. Warna bot bhi so jayega.

### 4. Memory ka dhyan rakho

Render free plan ka disk **ephemeral** hai — restart/redeploy/spin-down par
`session.json` aur `memory.db` delete ho jate hain.

- **Simple**: kuch mat karo — bot phir bhi kaam karega, par purani yaadein
  (summary/people) restart par chali jayengi
- **Free + permanent memory**: GitHub Gist auto-backup use karo (neeche)
- **Paid**: Render disk ($7/mo) lagao, mount path `/var/data`, aur
  `DB_PATH=/var/data/memory.db` set karo

#### GitHub Gist backup (`GIST_ID` + `GITHUB_TOKEN`)

1. GitHub → **Gists** → new **secret** gist, file name `memory.db.b64`,
   content me kuch bhi likho (`x`)
2. Gist URL se ID copy karo: `gist.github.com/<username>/<GIST_ID>`
3. GitHub → Settings → Developer settings → **Tokens (classic)** → `gist` scope
   wala token banao
4. Render env me daalo:
   - `GIST_ID` = gist id
   - `GITHUB_TOKEN` = token
   - `BACKUP_INTERVAL_SECONDS` = `300` (5 min)
5. Bot start pe Gist se DB restore karega, har 5 min aur shutdown pe save karega

Note: chat history ek secret Gist me jayegi — us GitHub account ko private rakho.

### 5. Session expire ho jaye to

Render → Environment → `IG_SESSIONID` update karo → **Manual Deploy** → Deploy latest.
Browser se naya cookie copy karna hoga (phone se `It was me` approve karna pad sakta hai).

> **Render tip:** Instagram ko datacenter IP pasand nahi aate. Login/challenge aaye
> to Render env me `PROXY=http://user:pass@host:port` set kar sakte ho. Fir bhi
> unofficial API hai — ban risk rehta hai.

## Ban ho gaya to?

1. `session.json` delete karo
2. Naya throwaway account, `.env` me naya `IG_SESSIONID`
3. Pehle kuch din `REPLY_MODE=mention` aur `ACTIVE_CHANCE=0.05` rakho

## Files

```
bot.py          main bot
memory.py       SQLite memory (messages/summary/replies)
persona.txt     personality / reply style
.env            credentials (gitignored)
session.json    login session (gitignored — secret!)
memory.db       chat memory (gitignored)
PAUSE           create this file to pause the bot
```
