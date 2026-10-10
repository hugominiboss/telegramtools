import json, asyncio, random, threading
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient

app = Flask(__name__)
CORS(app)

def load_json(path):
try:
with open(path, 'r', encoding='utf-8-sig') as f:
return json.load(f)
except:
return {}

auth = load_json('auth.json')
config = load_json('data/config.json')
messages_data = load_json('data/messages.json')
messages = messages_data.get('messages', ["Erro ao carregar mensagens"])

system_state = {
"status": "Inativo",
"total_sent": 0,
"last_group": "Nenhum",
"spam_active": False
}

client = TelegramClient('userbot_dashboard', auth.get('api_id'), auth.get('api_hash'))

async def run_spam_loop():
global system_state
while True:
if system_state["spam_active"]:
try:
system_state["status"] = "Enviando..."
target = random.choice(config.get('groups', []))
msg = random.choice(messages)
await client.send_message(target, msg)
system_state["total_sent"] += 1
system_state["last_group"] = target
system_state["status"] = "Aguardando (Sleep)"
await asyncio.sleep(5400 + random.randint(-300, 300))
except Exception as e:
system_state["status"] = f"Erro: {str(e)}"
await asyncio.sleep(300)
else:
system_state["status"] = "Pausado"
await asyncio.sleep(5)

def start_bot_thread():
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(client.start())
loop.create_task(run_spam_loop())
loop.run_forever()

@app.route('/')
def index():
with open('index.html', 'r', encoding='utf-8') as f:
return f.read()

@app.route('/api/stats')
def get_stats():
return jsonify(system_state)

@app.route('/api/action', methods=['POST'])
def action():
data = request.json
cmd = data.get("command")
if cmd == "start": system_state["spam_active"] = True
elif cmd == "stop": system_state["spam_active"] = False
elif cmd == "force":
threading.Thread(target=lambda: asyncio.run(force_send())).start()
return jsonify({"status": "ok"})

async def force_send():
for g in config.get('groups', []):
try:
await client.send_message(g, random.choice(messages))
system_state["total_sent"] += 1
await asyncio.sleep(random.randint(10, 30))
except: pass

if __name__ == "__main__":
threading.Thread(target=start_bot_thread, daemon=True).start()
app.run(port=5000, debug=False)
