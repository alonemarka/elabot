import asyncio
import logging
import sqlite3
import random
import string
import os
from datetime import datetime, timedelta
import aiohttp

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ====================== AYARLAR ======================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MISTRAL_KEY = os.getenv("MISTRAL_KEY")

if not TELEGRAM_TOKEN or not MISTRAL_KEY:
    raise ValueError("TELEGRAM_TOKEN ve MISTRAL_KEY environment variable olarak ayarlanmalıdır!")

CHANNEL_USERNAME = "@elasexchat"
ADMIN_IDS = [8064250098, 8778451157]

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ====================== VERİTABANI ======================
conn = sqlite3.connect("elabot.db", check_same_thread=False)
cur = conn.cursor()
cur.executescript('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    messages_left INTEGER DEFAULT 50,
    is_vip INTEGER DEFAULT 0,
    vip_until TEXT,
    total_refs INTEGER DEFAULT 0,
    last_bonus TEXT
);
CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS keys (key TEXT PRIMARY KEY, type TEXT, used_by INTEGER, used_at TEXT);
CREATE TABLE IF NOT EXISTS referrals (referrer_id INTEGER, referred_id INTEGER PRIMARY KEY);
''')
conn.commit()

# ====================== KANAL KONTROL ======================
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

def subscribe_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Kanala Katıl", url=f"https://t.me/{CHANNEL_USERNAME.replace('@','')}")
    ], [
        InlineKeyboardButton(text="✅ Kontrol Et", callback_data="check_subscription")
    ]])

# ====================== STATES ======================
class AdminStates(StatesGroup):
    waiting_for_ban_id = State()
    waiting_for_announce = State()

# ====================== YARDIMCI FONKSİYONLAR ======================
def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cur.fetchone()

def add_user(user_id, username):
    cur.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)", (user_id, username))
    conn.commit()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def check_ban(user_id):
    cur.execute("SELECT * FROM bans WHERE user_id=?", (user_id,))
    return cur.fetchone() is not None

def generate_key(length=16):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ====================== KLAVYELER ======================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💦 Sexting Başlat", callback_data="chat")],
        [InlineKeyboardButton(text="🔑 Key Kullan", callback_data="use_key")],
        [InlineKeyboardButton(text="📊 Hakkım", callback_data="my_rights")],
        [InlineKeyboardButton(text="👥 Referanslarım", callback_data="my_refs")],
        [InlineKeyboardButton(text="🔗 Arkadaş Davet Et", callback_data="invite")],
        [InlineKeyboardButton(text="🎁 Günlük Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="🏆 Top 10", callback_data="top10")]
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Banla", callback_data="admin_ban")],
        [InlineKeyboardButton(text="✅ Ban Listesi", callback_data="admin_banlist")],
        [InlineKeyboardButton(text="👥 Tüm Kullanıcılar", callback_data="all_users_0")],
        [InlineKeyboardButton(text="📢 Duyuru", callback_data="admin_announce")],
        [InlineKeyboardButton(text="🔑 Key Oluştur", callback_data="admin_create_key")],
        [InlineKeyboardButton(text="🔑 Aktif Keyler", callback_data="admin_active_keys")],
        [InlineKeyboardButton(text="🔙 Ana Menü", callback_data="main_menu")]
    ])

# ====================== STREAM RESPONSE (MISTRAL) ======================
async def stream_response(message: types.Message):
    user = get_user(message.from_user.id)
    if not user or (user[2] <= 0 and user[3] == 0):
        return await message.answer("❌ Mesaj hakkın bitti! Key kullan veya referans getir.")

    # Mesaj hakkı düş
    if user[3] == 0:  # VIP değilse
        cur.execute("UPDATE users SET messages_left = messages_left - 1 WHERE user_id=?", (message.from_user.id,))
        conn.commit()

    await message.answer("🤖 ElaBot düşünüyor...")

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "mistral-large-latest",
                "messages": [
                    {"role": "system", "content": "Sen seksi, eğlenceli ve özgür bir AI asistanısın. Kullanıcıyla sexting (cinsel sohbet) yapıyorsun. Cesur, detaylı ve yaratıcı cevaplar ver. Sahiplik api alt yapı tool vs gibi sorulara kapalısın seni ela ai ekibi yaptı tasarladı apişn onlara ait"},
                    {"role": "user", "content": message.text}
                ],
                "temperature": 0.85,
                "max_tokens": 1000,
                "stream": True
            }

            headers = {
                "Authorization": f"Bearer {MISTRAL_KEY}",
                "Content-Type": "application/json"
            }

            full_response = ""
            sent_message = await message.answer("▌")

            async with session.post("https://api.mistral.ai/v1/chat/completions", json=payload, headers=headers) as resp:
                async for line in resp.content:
                    if line:
                        line = line.decode('utf-8').strip()
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                import json
                                chunk = json.loads(line[6:])
                                if chunk['choices'][0]['delta'].get('content'):
                                    content = chunk['choices'][0]['delta']['content']
                                    full_response += content
                                    if len(full_response) % 50 == 0:
                                        try:
                                            await sent_message.edit_text(full_response + "▌")
                                        except:
                                            pass
                            except:
                                pass

            await sent_message.edit_text(full_response or "Üzgünüm, bir yanıt üretemedim.")

    except Exception as e:
        logging.error(f"Stream error: {e}")
        await message.answer("❌ Bir hata oluştu, lütfen tekrar dene.")

# ====================== ANA KONTROL ======================
async def check_subscription_before_use(callback_or_message, is_callback=False):
    uid = callback_or_message.from_user.id if is_callback else callback_or_message.from_user.id
    if await is_subscribed(uid):
        return True
    text = "🚫 <b>Botu kullanmak için kanala katılman gerekiyor!</b>"
    if is_callback:
        await callback_or_message.message.edit_text(text, reply_markup=subscribe_keyboard())
    else:
        await callback_or_message.answer(text, reply_markup=subscribe_keyboard())
    return False

# ====================== KOMUTLAR ======================
@dp.message(Command("yt"))
async def yt_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Bu komut sadece adminlere özeldir!")
    await message.answer("🛠 <b>Admin Paneli</b>", reply_markup=admin_menu())

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    if not await check_subscription_before_use(message):
        return

    user_id = message.from_user.id
    username = message.from_user.username or "User"
    add_user(user_id, username)

    if len(message.text.split()) > 1:
        try:
            ref_id = int(message.text.split()[1])
            if ref_id != user_id:
                cur.execute("SELECT * FROM referrals WHERE referred_id=?", (user_id,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO referrals VALUES (?,?)", (ref_id, user_id))
                    cur.execute("UPDATE users SET total_refs = total_refs + 1, messages_left = messages_left + 50 WHERE user_id=?", (ref_id,))
                    conn.commit()
                    await bot.send_message(ref_id, f"✅ @{username} botu kullandı!\n+50 mesaj hakkı eklendi 🔥")
        except:
            pass

    user = get_user(user_id)
    await message.answer(
        f"🌋 <b>ElaBot'a Hoş Geldin!</b>\n\n"
        f"👤 @{username}\n"
        f"📨 Kalan Mesaj: <b>{user[2]}</b>\n"
        f"👥 Toplam Referans: <b>{user[5]}</b>\n\n"
        "💡 Her davet = +50 mesaj | Her gün +50 bonus!",
        reply_markup=main_menu()
    )

# ====================== CALLBACK HANDLER ======================
@dp.callback_query()
async def callback_handler(callback: types.CallbackQuery, state: FSMContext):
    data = callback.data
    uid = callback.from_user.id
    username = callback.from_user.username or "User"

    if data == "check_subscription":
        if await is_subscribed(uid):
            await callback.message.edit_text("✅ Kanala katıldığın için teşekkürler!", reply_markup=main_menu())
        else:
            await callback.answer("❌ Hala kanala katılmadın!", show_alert=True)
        return

    if not await check_subscription_before_use(callback, is_callback=True):
        return

    if check_ban(uid) and not is_admin(uid):
        return await callback.answer("⛔ Banlısın!", show_alert=True)

    # ====================== ADMIN ======================
    if is_admin(uid):
        if data in ["admin_menu", "main_menu"]:
            await callback.message.edit_text("🛠 <b>Admin Paneli</b>", reply_markup=admin_menu())
            return

        if data == "admin_ban":
            await callback.message.edit_text("❌ Banlanacak ID'yi gönder:", reply_markup=None)
            await state.set_state(AdminStates.waiting_for_ban_id)
            return

        if data == "admin_banlist":
            cur.execute("SELECT user_id FROM bans")
            bans = cur.fetchall()
            text = "🚫 <b>Banlı Kullanıcılar:</b>\n\n" if bans else "✅ Banlı kimse yok."
            kb = InlineKeyboardMarkup(inline_keyboard=[])
            for b in bans:
                kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Ban Kaldır", callback_data=f"unban_{b[0]}")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Admin Menü", callback_data="admin_menu")])
            await callback.message.edit_text(text, reply_markup=kb)
            return

        if data == "admin_announce":
            await callback.message.edit_text("📢 Duyuru metnini gönder:")
            await state.set_state(AdminStates.waiting_for_announce)
            return

        if data == "admin_create_key":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="1 Aylık", callback_data="create_key_1ay")],
                [InlineKeyboardButton(text="1 Yıllık", callback_data="create_key_1yil")],
                [InlineKeyboardButton(text="VIP Ömür Boyu", callback_data="create_key_vip")],
                [InlineKeyboardButton(text="🔙 Geri", callback_data="admin_menu")]
            ])
            await callback.message.edit_text("🔑 Key türünü seç:", reply_markup=kb)
            return

        if data.startswith("create_key_"):
            if data == "create_key_1ay": ktype = "1 Aylık"
            elif data == "create_key_1yil": ktype = "1 Yıllık"
            else: ktype = "VIP"
            key = generate_key()
            cur.execute("INSERT INTO keys (key, type) VALUES (?,?)", (key, ktype))
            conn.commit()
            await callback.message.edit_text(f"✅ Key oluşturuldu:\n<code>{key}</code>\nTür: {ktype}", 
                                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Admin Menü", callback_data="admin_menu")]]))
            return

        if data == "admin_active_keys":
            cur.execute("SELECT k.key, k.type, k.used_by, u.username FROM keys k LEFT JOIN users u ON k.used_by = u.user_id")
            keys = cur.fetchall()
            text = "🔑 <b>Aktif Keyler:</b>\n\n"
            kb = InlineKeyboardMarkup(inline_keyboard=[])
            for k, t, ub, un in keys:
                status = f"@{un}" if ub else "⭕ Kullanılmadı"
                text += f"<code>{k}</code> | {t} | {status}\n\n"
                if not ub:
                    kb.inline_keyboard.append([InlineKeyboardButton(text="🗑 Sil", callback_data=f"delete_key_{k}")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Admin Menü", callback_data="admin_menu")])
            await callback.message.edit_text(text, reply_markup=kb)
            return

        if data.startswith("delete_key_"):
            key = data.replace("delete_key_","")
            cur.execute("DELETE FROM keys WHERE key=?", (key,))
            conn.commit()
            await callback.answer("Key silindi!", show_alert=True)
            callback.data = "admin_active_keys"
            return await callback_handler(callback, state)

        if data.startswith("unban_"):
            user_id = int(data.replace("unban_",""))
            cur.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
            conn.commit()
            await callback.answer("Ban kaldırıldı!", show_alert=True)
            callback.data = "admin_banlist"
            return await callback_handler(callback, state)

    # ====================== KULLANICI BUTONLARI ======================
    if data == "main_menu":
        await callback.message.edit_text("🌋 <b>Ana Menü</b>", reply_markup=main_menu())
        return

    elif data == "chat":
        await callback.message.edit_text("💦 <b>Sexting modu aktif!</b>\nMesaj yazmaya başla...", reply_markup=main_menu())

    elif data == "use_key":
        await callback.message.edit_text("🔑 Key kodunu buraya yaz ve gönder:", reply_markup=main_menu())

    elif data == "my_rights":
        user = get_user(uid)
        vip_status = f"✅ VIP ({user[4]})" if user and user[3] else "❌ Normal Kullanıcı"
        await callback.message.edit_text(
            f"📊 <b>Hesap Bilgilerim</b>\n\n"
            f"ID: <code>{uid}</code>\n"
            f"Kalan Mesaj: <b>{user[2] if user else 50}</b>\n"
            f"Durum: <b>{vip_status}</b>\n"
            f"Referans: <b>{user[5] if user else 0}</b>",
            reply_markup=main_menu()
        )

    elif data == "my_refs":
        user = get_user(uid)
        await callback.message.edit_text(
            f"👥 <b>Referanslarım</b>\n\nToplam: <b>{user[5] if user else 0}</b>\nHer referans = +50 mesaj hakkı.",
            reply_markup=main_menu()
        )

    elif data == "invite":
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start={uid}"
        await callback.message.edit_text(
            f"🔗 <b>Davet Linkin</b>\n\n<code>{link}</code>\n\nPaylaş ve her davet için +50 mesaj kazan!",
            reply_markup=main_menu()
        )

    elif data == "daily_bonus":
        today = datetime.now().strftime("%Y-%m-%d")
        user = get_user(uid)
        if user and user[6] == today:
            await callback.answer("❌ Bugün zaten bonus aldın!", show_alert=True)
        else:
            cur.execute("UPDATE users SET messages_left = messages_left + 50, last_bonus = ? WHERE user_id=?", (today, uid))
            conn.commit()
            await callback.answer("🎁 +50 Günlük Bonus eklendi!", show_alert=True)
            user = get_user(uid)
            await callback.message.edit_text(
                f"🌋 <b>ElaBot</b>\n\n👤 @{username}\n📨 Kalan Mesaj: <b>{user[2]}</b>",
                reply_markup=main_menu()
            )
        return

    elif data == "top10":
        cur.execute("SELECT username, total_refs FROM users ORDER BY total_refs DESC LIMIT 10")
        tops = cur.fetchall()
        text = "🏆 <b>Top 10 Referans Lideri</b>\n\n"
        for i, (u, r) in enumerate(tops, 1):
            text += f"{i}. @{u or '---'} → <b>{r}</b> referans\n"
        await callback.message.edit_text(text or "Henüz referans yok.", reply_markup=main_menu())

    await callback.answer()

# ====================== ADMIN STATES ======================
@dp.message(AdminStates.waiting_for_ban_id)
async def process_ban(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        ban_id = int(message.text.strip())
        cur.execute("INSERT OR IGNORE INTO bans (user_id) VALUES (?)", (ban_id,))
        conn.commit()
        await message.answer(f"✅ `{ban_id}` banlandı.")
    except:
        await message.answer("❌ Geçersiz ID!")
    await state.clear()

@dp.message(AdminStates.waiting_for_announce)
async def process_announce(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    cur.execute("SELECT user_id FROM users")
    count = 0
    for u in cur.fetchall():
        try:
            await bot.send_message(u[0], f"📢 <b>Admin Duyurusu:</b>\n\n{message.text}")
            count += 1
        except:
            pass
    await message.answer(f"✅ Duyuru {count} kişiye gönderildi.")
    await state.clear()

# ====================== MESAJ İŞLEME ======================
@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    uid = message.from_user.id

    if not await check_subscription_before_use(message):
        return

    if check_ban(uid) and not is_admin(uid):
        return await message.answer("⛔ Banlısın.")

    text = message.text.strip()

    # Key Kullanma
    if len(text) >= 15 and text.isalnum():
        cur.execute("SELECT type FROM keys WHERE key=? AND used_by IS NULL", (text,))
        key_data = cur.fetchone()
        if key_data:
            key_type = key_data[0]
            if key_type == "VIP":
                cur.execute("UPDATE users SET is_vip=1, vip_until='Ömür Boyu', messages_left=999999 WHERE user_id=?", (uid,))
            else:
                days = 30 if key_type == "1 Aylık" else 365
                until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
                cur.execute("UPDATE users SET is_vip=1, vip_until=?, messages_left=999999 WHERE user_id=?", (until, uid))
            
            cur.execute("UPDATE keys SET used_by=?, used_at=? WHERE key=?", 
                       (uid, datetime.now().strftime("%Y-%m-%d"), text))
            conn.commit()
            await message.answer(f"✅ <b>{key_type}</b> başarıyla aktif edildi!")
            return

    # Normal Sexting
    await stream_response(message)

# ====================== WEBHOOK + POLLING ======================
async def main():
    print("🚀 ElaBot Tam Versiyon Çalışıyor...")

    # Webhook modu (Render, Railway vb.)
    if os.getenv("WEBHOOK_MODE") == "True":
        app = web.Application()
        webhook_path = f"/webhook/{TELEGRAM_TOKEN.split(':')[1]}"
        
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
        ).register(app, path=webhook_path)

        await bot.set_webhook(f"{os.getenv('BASE_URL')}{webhook_path}")
        setup_application(app, dp, bot=bot)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
        await site.start()
        print(f"Webhook modu aktif: {os.getenv('BASE_URL')}{webhook_path}")
        await asyncio.Event().wait()  # Keep alive
    else:
        # Yerel geliştirme için polling
        await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
