import asyncio, csv, random, time
from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, ChatForbidden

api_id   = 30113282
api_hash = 'fb4da52a13e42b721842995a5ab13212'
phone    = '+55 51 99818 6023'
group_username = 'madaraflexx'
BATCH  = 200
PAUSE  = (2, 5)

def human_pause():
    time.sleep(random.uniform(*PAUSE))

async def safe_dump():
    async with TelegramClient(phone, api_id, api_hash,
                               device_model="Samsung-SM-G973F",
                               system_version="SDK 31",
                               app_version="10.5.3") as client:

        await client.start(phone)

        dialogs = await client(GetDialogsRequest(
            offset_date=None, offset_id=0,
            offset_peer=InputPeerEmpty(), limit=200, hash=0))

        target = None
        for d in dialogs.chats:
            if isinstance(d, ChatForbidden):
                continue
            if (getattr(d, 'username', None) and d.username.lower() == group_username) or str(d.id) == group_username:
                target = d
                break
        if not target:
            print('Grupo não encontrado ou você NÃO está dentro dele.')
            return

        print('Raspando…')
        all_parts = []
        async for user in client.iter_participants(target, limit=BATCH):
            all_parts.append(user)
            if len(all_parts) % BATCH == 0:
                print(f'Já coletados: {len(all_parts)}')

        with open('ids_final.csv','w',newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['id'])
            for u in all_parts:
                writer.writerow([u.id])

        print(f'Total: {len(all_parts)}  → ids_final.csv')

if __name__ == '__main__':
    asyncio.run(safe_dump())
