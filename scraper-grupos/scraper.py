#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, json, os, random, sys
from datetime import datetime as dt
from telethon import TelegramClient
from telethon.tl.functions.messages import SearchGlobalRequest
from telethon.tl.types import InputMessagesFilterEmpty
from telethon.errors import FloodWaitError

SESSION_FILE = "5551998186023.session"
API_ID = 30113282
API_HASH = "fb4da52a13e42b721842995a5ab13212"
KEYWORDS_FILE = "keywords.txt"
OUTPUT_FILE = "grupos_vivos.txt"
SEEN_FILE = "seen.json"

BLUE  = "\033[36m"; GREEN = "\033[32m"; RED = "\033[31m"; YELLOW = "\033[33m"; RESET = "\033[0m"
os.makedirs(os.path.dirname(SEEN_FILE) or ".", exist_ok=True)

def log(msg): print(f"{dt.now().strftime('%H:%M:%S')} {msg}")

def load_keywords():
    with open(KEYWORDS_FILE, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip()]

def load_seen():
    if os.path.exists(SEEN_FILE):
        return set(json.load(open(SEEN_FILE, encoding="utf-8")))
    return set()

async def save_seen(seen):
    json.dump(list(seen), open(SEEN_FILE, "w", encoding="utf-8"), ensure_ascii=False)

async def scrap_kw(kw, client, seen):
    try:
        log(f"{BLUE}[BUSCA]{RESET} {kw}")
        # NOVO formato (sem offset_date)
        res = await client(SearchGlobalRequest(
            q=kw, offset_id=0, limit=300,
            filter=InputMessagesFilterEmpty()
        ))
        new = 0
        for m in res.messages:
            c = m.chat
            if c and not c.broadcast and getattr(c, "username", None):
                link = f"https://t.me/{c.username}"
                if link not in seen:
                    new += 1
                    seen.add(link)
        if new:
            log(f"{GREEN}[+{new}]{RESET} novos de {kw}")
        else:
            log(f"{YELLOW}[0]{RESET} sem novos para {kw}")
        await asyncio.sleep(random.uniform(.5, 1.2))
    except FloodWaitError as e:
        log(f"{RED}[FLOOD]{RESET} dormindo {e.seconds}s")
        await asyncio.sleep(e.seconds + 5)
    except Exception as e:
        log(f"{RED}[ERRO]{RESET} {e}")

async def main():
    kws = load_keywords()
    seen = load_seen()
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    log(f"[INF] sessão OK – {len(kws)} palavras")
    for i, kw in enumerate(kws, 1):
        await scrap_kw(kw, client, seen)
        if i % 10 == 0:
            await save_seen(seen)
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(sorted(seen)) + "\n")
            log(f"{GREEN}[SAVE]{RESET} {(i/len(kws)*100):.0f}% – {len(seen)} links")
    # fim
    await save_seen(seen)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(seen)) + "\n")
    log(f"{GREEN}[FIM]{RESET} {len(seen)} únicos → {OUTPUT_FILE}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log(f"{RED}[INT]{RESET} salvo – {len(load_seen())} links em {OUTPUT_FILE}")
