#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, re, json, os, sys, random
from datetime import datetime as dt
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import TypeInputPeer

SESSION_FILE = "5551998186023.session"
API_ID = 30113282
API_HASH = "fb4da52a13e42b721842995a5ab13212"
REGISTRADOS_FILE = "registrados.json"
SAIDAS_LINKS = "grupos_vivos.txt"
MIN_PARA_ENTRAR = 3   # entra nos N primeiros que tiver @username
POR_MSG = 5000        # quantas mensagens vasculhar por grupo

LINK_RE = re.compile(r"(https?://)?(t\.me/)(joinchat/)?([a-zA-Z0-9_-]+)")

def log(msg):
    print(f"{dt.now().strftime('%H:%M:%S')} {msg}")

def carrega_links():
    if os.path.exists(REGISTRADOS_FILE):
        return set(json.load(open(REGISTRADOS_FILE, encoding="utf-8")))
    return set()

def salva_links(s):
    json.dump(list(s), open(REGISTRADOS_FILE, "w", encoding="utf-8"), ensure_ascii=False)

def embaralha(seq):
    random.shuffle(seq)
    return seq

async def main():
    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
    await client.start()
    log(f"[INF] sessão {SESSION_FILE} autenticada")

    links_ja = carrega_links()

    # lista diálogos pra saber onde já está dentro
    dialogs = await client.get_dialogs()
    grupos_pub = []
    for d in dialogs:
        # só grupos, sem canal
        if d.is_group and hasattr(d.entity, "username") and d.entity.username:
            grupos_pub.append(d.entity)
    log(f"[INF] já está em {len(grupos_pub)} grupos públicos")

    # pede para entrar em mais se for < MIN_PARA_ENTRAR
    if len(grupos_pub) < MIN_PARA_ENTRAR:
        log(f"[AVISO] entre em pelo menos {MIN_PARA_ENTRAR} grupos públicos, pause (Ctrl-C) e rode de novo")
    else:
        # escolhe 3 grupos aleatórios pra vasculhar
        grupo_pool = embaralha(grupos_pub)[:MIN_PARA_ENTRAR]

        for grp in grupo_pool:
            log(f"[BUSCA] vasculhando @{(grp.username or grp.id)}")
            try:
                # opção rápida: vê últimas 5k mensagens
                async for msg in client.iter_messages(grp, limit=POR_MSG):
                    if not msg or not msg.text:
                        continue
                    # regex captura links t.me com ou sem https://
                    for a, b, c, user in LINK_RE.findall(msg.text):
                        link = f"https://t.me/{user}"
                        if link not in links_ja:
                            links_ja.add(link)
                            log(f"[+] {link}")
            except FloodWaitError as e:
                log(f"[FLOOD] aguardando {e.seconds}s…")
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                log(f"[ERRO] {e}")

    # salva tudo
    with open(SAIDAS_LINKS, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(links_ja)) + "\n")
    salva_links(links_ja)
    log(f"[FIM] {len(links_ja)} únicos → {SAIDAS_LINKS}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("[INT] interrompido – progresso salvo")
