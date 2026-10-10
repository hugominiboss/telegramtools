import json, asyncio, random, os
from telethon import TelegramClient, events, Button
from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()

def load_json(path):
    with open(path, 'r', encoding='utf-8-sig') as f: 
        return json.load(f)

try:
    auth = load_json('auth.json')
    config = load_json('data/config.json')
    keywords = load_json('data/keywords.json')
    messages_data = load_json('data/messages.json')
    messages = messages_data['messages']
    console.print("[bold green]✅ Arquivos carregados com sucesso![/bold green]")
except Exception as e:
    console.print(f"[bold red]❌ Erro ao carregar arquivos JSON: {e}[/bold red]")
    exit()

client = TelegramClient('userbot_isca', auth['api_id'], auth['api_hash'])

# Variáveis de controle global
stats = {"count": 0, "status": "Inativo", "last_group": "Nenhum"}
spam_active = True

def generate_dashboard(status, last_group, count):
    table = Table(title="🚀 USERBOT LEAD GEN v5.0 - PAINEL")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="magenta")
    table.add_row("Status", status)
    table.add_row("Último Grupo", str(last_group))
    table.add_row("Total Enviados", str(count))
    return table

async def spam_worker():
    global stats, spam_active
    with Live(generate_dashboard(stats["status"], stats["last_group"], stats["count"]), refresh_per_second=1) as live:
        while True:
            if spam_active:
                try:
                    stats["status"] = "Enviando..."
                    target = random.choice(config['groups'])
                    msg = random.choice(messages)
                    await client.send_message(target, msg)
                    stats["count"] += 1
                    stats["last_group"] = target
                    stats["status"] = "Aguardando (Sleep)"
                    live.update(generate_dashboard(stats["status"], stats["last_group"], stats["count"]))
                    await asyncio.sleep(5400 + random.randint(-300, 300))
                except Exception as e:
                    stats["status"] = f"Erro: {e}"
                    live.update(generate_dashboard(stats["status"], stats["last_group"], stats["count"]))
                    await asyncio.sleep(300)
            else:
                stats["status"] = "Pausado"
                live.update(generate_dashboard(stats["status"], stats["last_group"], stats["count"]))
                await asyncio.sleep(10)

# --- PAINEL DE CONTROLE VIA TELEGRAM ---

@client.on(events.NewMessage(pattern=r'\/painel'))
async def open_panel(event):
    if event.is_private:
        buttons = [
            [Button.inline("🚀 Disparar Agora", b"force_spam"), Button.inline("⏸ Pausar Bot", b"pause_bot")],
            [Button.inline("▶️ Retomar Bot", b"resume_bot"), Button.inline("📊 Status", b"get_stats")],
            [Button.inline("🔄 Reiniciar Worker", b"restart_worker")]
        ]
        await event.reply("🛠 **Painel de Controle Amelia**\nSelecione uma ação abaixo:", buttons=buttons)

@client.on(events.CallbackQuery)
async def callback_handler(event):
    global spam_active
    data = event.data
    
    if data == b"force_spam":
        await event.answer("🚀 Iniciando disparos manuais...", alert=True)
        for g in config['groups']:
            try:
                await client.send_message(g, random.choice(messages))
                stats["count"] += 1
                await asyncio.sleep(random.randint(15, 45))
            except: pass
        await event.edit("✅ Disparos manuais concluídos!")

    elif data == b"pause_bot":
        spam_active = False
        await event.answer("⏸ Bot Pausado!", alert=True)
        await event.edit("⚠️ O bot automático foi pausado.")

    elif data == b"resume_bot":
        spam_active = True
        await event.answer("▶️ Bot Retomado!", alert=True)
        await event.edit("✅ O bot automático voltou a operar.")

    elif data == b"get_stats":
        msg = f"📊 **Estatísticas Atuais:**\n\nTotal Enviados: {stats['count']}\nStatus: {stats['status']}\nÚltimo Grupo: {stats['last_group']}"
        await event.answer("Consultando dados...", alert=False)
        await event.reply(msg)

# --- RESPOSTAS AUTOMÁTICAS (KEYWORDS) ---

@client.on(events.NewMessage(incoming=True))
async def handler_keywords(event):
    if event.is_private: return
    text = event.message.message.lower()
    for key, resp in keywords.items():
        if key.lower() in text:
            await asyncio.sleep(random.randint(10, 30))
            await event.reply(resp)
            break

async def main():
    await client.start()
    console.print("[bold green]Userbot Online! Painel Ativo via /painel[/bold green]")
    asyncio.create_task(spam_worker())
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())