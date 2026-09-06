import os
import re
import sqlite3
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand
from aiogram.exceptions import TelegramAPIError

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_CHAT_LINK = "https://t.me/+q1ipzXuMbYczMDBi"
PORT = int(os.getenv("PORT", 8080))
DB_FILE = "p2p_orders.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                tag TEXT DEFAULT 'Пользователь',
                total_volume REAL DEFAULT 0.0,
                orders_count INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY DEFAULT 1,
                total_orders INTEGER DEFAULT 0,
                total_volume REAL DEFAULT 0.0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                chat_id INTEGER,
                message_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO stats (id) VALUES (1)")
        conn.commit()

def calculate_tag(current_volume: float, existing_tag: str) -> str:
    if current_volume >= 10000:
        standard_tags = ['Пользователь', 'Buyer', 'Big buyer', 'Premium buyer', 'Elite buyer', 'Vip buyer', 'Legend buyer']
        if existing_tag in standard_tags:
            return 'Legend buyer'
        return existing_tag
    elif current_volume >= 5000:
        return 'Legend buyer'
    elif current_volume >= 3000:
        return 'Vip buyer'
    elif current_volume >= 1000:
        return 'Elite buyer'
    elif current_volume >= 500:
        return 'Premium buyer'
    elif current_volume >= 250:
        return 'Big buyer'
    elif current_volume >= 100:
        return 'Buyer'
    else:
        return 'Пользователь'

# Функция автоматической выдачи админки и изменения серого префикса
async def update_user_admin_title(chat_id: int, user_id: int, tag: str):
    if not user_id:
        return
    try:
        await bot.promote_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_messages=False,
            can_edit_messages=False,
            can_pin_messages=False
        )
        await bot.set_chat_administrator_custom_title(
            chat_id=chat_id,
            user_id=user_id,
            custom_title=tag[:16]
        )
    except Exception as e:
        logging.error(f"Не удалось выдать префикс админа: {e}")

def register_or_update_user(username: str, user_id: int = None, amount: float = 0.0):
    clean_username = username.replace("@", "").lower()
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, total_volume, orders_count, tag FROM users WHERE username = ?", (clean_username,))
        user = cur.fetchone()
        
        if not user:
            new_volume = amount
            new_tag = calculate_tag(new_volume, 'Пользователь')
            cur.execute("INSERT INTO users (user_id, username, total_volume, orders_count, tag) VALUES (?, ?, ?, ?, ?)",
                        (user_id, clean_username, new_volume, 1 if amount > 0 else 0, new_tag))
        else:
            new_volume = user[2] + amount
            new_orders_count = user[3] + (1 if amount > 0 else 0)
            new_tag = calculate_tag(new_volume, user[4])
            
            cur.execute("UPDATE users SET total_volume = ?, orders_count = ?, tag = ? WHERE username = ?",
                        (new_volume, new_orders_count, new_tag, clean_username))
            
            if user_id and not user[0]:
                cur.execute("UPDATE users SET user_id = ? WHERE username = ?", (user_id, clean_username))
                
        conn.commit()

def get_user(username: str):
    clean_username = username.replace("@", "").lower()
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, total_volume, orders_count, tag FROM users WHERE username = ?", (clean_username,))
        return cur.fetchone()

# === КОМАНДЫ МЕНЮ ===
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="me", description="Мой профиль"),
        BotCommand(command="info", description="Профиль пользователя"),
        BotCommand(command="stats", description="Статистика проекта"),
        BotCommand(command="top", description="Рейтинг участников"),
        BotCommand(command="tag", description="Установить свой тег (от 10k$)"),
        BotCommand(command="help", description="Все команды")
    ]
    await bot.set_my_commands(commands)

# === ОБРАБОТКА ОРДЕРА ===
ORDER_REGEX = re.compile(r"^\+\s*(\d+(?:\.\d+)?)\$?\s*@([a-zA-Z0-9_]+)")

@dp.message(F.text.regexp(ORDER_REGEX))
async def process_order(message: types.Message):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO processed_messages (chat_id, message_id) VALUES (?, ?)", (message.chat.id, message.message_id))
            conn.commit()
        except sqlite3.IntegrityError:
            return

    match = ORDER_REGEX.match(message.text)
    amount = float(match.group(1))
    buyer_username = match.group(2).lower()
    seller_username = message.from_user.username.lower() if message.from_user.username else f"id{message.from_user.id}"

    buyer_old_data = get_user(buyer_username)
    buyer_old_orders = buyer_old_data[3] if buyer_old_data else 0
    buyer_old_tag = buyer_old_data[4] if buyer_old_data else 'Пользователь'

    register_or_update_user(seller_username, message.from_user.id, amount)
    register_or_update_user(buyer_username, None, amount)

    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("UPDATE stats SET total_orders = total_orders + 1, total_volume = total_volume + ? WHERE id = 1", (amount,))
        cur.execute("SELECT total_orders FROM stats WHERE id = 1")
        global_order_id = cur.fetchone()[0]
        conn.commit()

    buyer_data = get_user(buyer_username)

    receipt_text = (
        f"<b>[PAY] <u>ОРДЕР ЗАКРЫТ</u></b>\n\n"
        f"📝 Ордер · <b>#{global_order_id + 5000}</b>\n"
        f"👤 Скуп · @{buyer_username}\n"
        f"💸 Сумма · <b>{amount}$</b>\n"
        f"📈 Итого скупа · <b>{buyer_data[3]}</b> · <b>{buyer_data[2]}$</b>"
    )
    await message.answer(receipt_text, parse_mode="HTML")

    if buyer_data[0]:
        if buyer_old_tag != buyer_data[4] or buyer_old_orders == 0:
            await update_user_admin_title(message.chat.id, buyer_data[0], buyer_data[4])

    if buyer_old_orders < 3 and buyer_data[3] >= 3 and buyer_data[0]:
        try:
            welcome_text = (
                "🟢<b>Добро пожаловать UNION ORDERS!</b>🟢\n\n"
                "Вы успешно закрыли 3 ордера и теперь получаете доступ в основной чат:\n"
                f"{MAIN_CHAT_LINK}\n"
                "Хороших профитов!"
            )
            await bot.send_message(chat_id=buyer_data[0], text=welcome_text, parse_mode="HTML")
        except TelegramAPIError:
            pass

# === КОМАНДЫ ===
@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT username, total_volume, orders_count, tag FROM users ORDER BY total_volume DESC LIMIT 10")
        leaders = cur.fetchall()

    if not leaders:
        return await message.answer("🏆 Рейтинг пока пуст.")

    text = "🏆 <b>Топ участников по обороту:</b>\n\n"
    for i, u in enumerate(leaders, 1):
        text += f"{i}. @{u[0]} [{u[3]}] — <b>{u[1]}$</b> ({u[2]} орд.)\n"

    await message.answer(text, parse_mode="HTML")

@dp.message(Command("tag"))
async def cmd_tag(message: types.Message, command: CommandObject):
    username = message.from_user.username.lower() if message.from_user.username else f"id{message.from_user.id}"
    user = get_user(username)
    
    if not user:
        return await message.answer("❌ У вас пока нет профиля в базе. Совершите или закройте хотя бы один ордер.")
    
    if user[2] < 10000:
        return await message.answer(f"🔒 Кастомный тег доступен при общем обороте от <b>10,000$</b>.\nВаш текущий оборот: <b>{user[2]}$</b>", parse_mode="HTML")
    
    if not command.args:
        return await message.answer("Укажите ваш новый тег. Пример:\n<code>/tag МойТег</code>", parse_mode="HTML")
    
    new_tag = command.args.strip()
    if len(new_tag) > 16:
        return await message.answer("❌ Префикс слишком длинный. Максимум 16 символов для Telegram.")
    
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE users SET tag = ? WHERE username = ?", (new_tag, username))
        conn.commit()

    if user[0]:
        await update_user_admin_title(message.chat.id, user[0], new_tag)

    await message.answer(f"✅ Ваш персональный префикс успешно изменен на: <b>{new_tag}</b>", parse_mode="HTML")

@dp.message(Command("me"))
async def cmd_me(message: types.Message):
    username = message.from_user.username.lower() if message.from_user.username else f"id{message.from_user.id}"
    user = get_user(username)
    if not user:
        register_or_update_user(username, message.from_user.id, 0)
        user = get_user(username)

    text = (
        f"💼 <b>Твой профиль:</b>\n"
        f"👤 Участник: @{user[1]}\n"
        f"🏷 Тег: <b>{user[4]}</b>\n"
        f"📊 Закрыто ордеров: <b>{user[3]}</b>\n"
        f"💸 Общий оборот: <b>{user[2]}$</b>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("info"))
async def cmd_info(message: types.Message, command: CommandObject):
    if not command.args:
        return await message.answer("Пример: <code>/info @username</code>", parse_mode="HTML")
    
    target_username = command.args.replace("@", "").lower()
    user = get_user(target_username)
    
    if user:
        text = (
            f"🔍 <b>Профиль пользователя:</b>\n"
            f"👤 Участник: @{user[1]}\n"
            f"🏷 Тег: <b>{user[4]}</b>\n"
            f"📊 Закрыто ордеров: <b>{user[3]}</b>\n"
            f"💸 Общий оборот: <b>{user[2]}$</b>"
        )
    else:
        text = "🤷‍♂️ Пользователь не найден."
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT total_orders, total_volume FROM stats WHERE id = 1")
        st = cur.fetchone()
        
    text = f"🌐 <b>Статистика проекта:</b>\n📝 Всего закрыто ордеров: <b>{st[0]}</b>\n💰 Общий оборот: <b>{st[1]}$</b>"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "<b>📋 Уровни тегов по обороту:</b>\n"
        "• Buyer — от 100$\n"
        "• Big buyer — от 250$\n"
        "• Premium buyer — от 500$\n"
        "• Elite buyer — от 1k$\n"
        "• Vip buyer — от 3k$\n"
        "• Legend buyer — от 5k$\n"
        "• <b>от 10k$ — свой собственный тег через команду /tag</b>\n\n"
        "<b>Команды:</b>\n"
        "/top — Топ лидеров\n"
        "/me — Личный профиль\n"
        "/info @username — Посмотреть профиль\n"
        "/tag <тег> — Указать свой тег (доступно от 10k$)\n"
        "/stats — Общий оборот проекта\n\n"
        "Формат закрытия ордера: <code>+10$ @username</code>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message()
async def track_all_users(message: types.Message):
    if message.from_user and message.from_user.username:
        register_or_update_user(message.from_user.username, message.from_user.id, 0)

# === SERVER & START ===
async def handle_ping(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await set_bot_commands(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    asyncio.run(main())
