import json, asyncio, random, os
from telethon import TelegramClient, events
from rich.console import Console
from rich.table import Table
from rich.live import Live

console = Console()

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f: return json.load(f)

try:
    auth = load_json('auth.json')
    config = load_json('data/config.json')
    keywords = load_json('data/keywords.json')
    messages_data = load_json('data/messages.json')
    messages = messages_data['messages']
except Exception as e:
    print(f"Erro ao carregar arquivos JSON: {e}")
    exit()

client = TelegramClient('userbot_isca', auth['api_id'], auth['api_hash'])

def generate_dashboard(status, last_group, count):
    table = Table(title="🚀 USERBOT LEAD GEN v4.5")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="magenta")
    table.add_row("Status", status)
    table.add_row("Último Grupo", str(last_group))
    table.add_row("Total Enviados", str(count))
    return table

async def spam_worker():
    count = 0
    last_group = "Nenhum"
    with Live(generate_dashboard("Iniciando...", last_group, count), refresh_per_second=1) as live:
        while True:
            try:
                target = random.choice(config['groups'])
                msg = random.choice(messages)
                await client.send_message(target, msg)
                count += 1
                last_group = target
                live.update(generate_dashboard("✅ Ativo", last_group, count))
                # Intervalo humanizado: aprox 1h30min com variação
                await asyncio.sleep(5400 + random.randint(-300, 300))
            except Exception as e:
                live.update(generate_dashboard(f"❌ Erro: {e}", last_group, count))
                await asyncio.sleep(300)

@client.on(events.NewMessage(incoming=True))
async def handler_keywords(event):
    text = event.message.message.lower()
    for key, resp in keywords.items():
        if key.lower() in text:
            await asyncio.sleep(random.randint(5, 15))
            await event.reply(resp)
            break

@client.on(events.NewMessage(pattern=r'\/disparar'))
async def manual_spam(event):
    if event.is_private:
        await event.reply("🚀 Iniciando disparos manuais...")
        for g in config['groups']:
            await client.send_message(g, random.choice(messages))
            await asyncio.sleep(random.randint(10, 30))
        await event.reply("✅ Concluído!")

async def main():
    await client.start()
    console.print("[bold green]Userbot Online! Dashboard Ativo...[/bold green]")
    asyncio.create_task(spam_worker())
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())