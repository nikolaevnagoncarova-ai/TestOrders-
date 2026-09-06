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
MAIN_CHAT_LINK = os.getenv("MAIN_CHAT_LINK", "https://t.me/твоя_ссылка")
PORT = int(os.getenv("PORT", 8080))
DB_FILE = "p2p_orders.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# === БАЗА ДАННЫХ ===
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Таблица пользователей
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                total_volume REAL DEFAULT 0.0,
                orders_count INTEGER DEFAULT 0
            )
        """)
        # Таблица глобальной статистики
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY DEFAULT 1,
                total_orders INTEGER DEFAULT 0,
                total_volume REAL DEFAULT 0.0
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO stats (id) VALUES (1)")
        conn.commit()

# Хелперы для БД
def get_or_create_user(username: str, user_id: int = None):
    clean_username = username.replace("@", "").lower()
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, username, total_volume, orders_count FROM users WHERE username = ?", (clean_username,))
        user = cur.fetchone()
        
        if not user and user_id:
            cur.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, clean_username))
            conn.commit()
            return (user_id, clean_username, 0.0, 0)
        return user

def update_order_stats(username: str, amount: float):
    clean_username = username.replace("@", "").lower()
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        # Обновляем юзера
        cur.execute("UPDATE users SET total_volume = total_volume + ?, orders_count = orders_count + 1 WHERE username = ?", (amount, clean_username))
        # Обновляем глобальную стату
        cur.execute("UPDATE stats SET total_orders = total_orders + 1, total_volume = total_volume + ? WHERE id = 1", (amount,))
        
        cur.execute("SELECT total_volume, orders_count, user_id FROM users WHERE username = ?", (clean_username,))
        user_stats = cur.fetchone()
        
        cur.execute("SELECT total_orders FROM stats WHERE id = 1")
        global_order_id = cur.fetchone()[0]
        conn.commit()
        
        return user_stats, global_order_id

# === УСТАНОВКА КОМАНД МЕНЮ ===
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="me", description="Мой профиль"),
        BotCommand(command="info", description="Профиль пользователя"),
        BotCommand(command="stats", description="Статистика проекта"),
        BotCommand(command="help", description="Все команды")
    ]
    await bot.set_my_commands(commands)

# === ЛОГИКА ЗАКРЫТИЯ ОРДЕРА ===
# Регулярное выражение ловит: +10$ @username, + 32$ @username, +11.5 @username
ORDER_REGEX = re.compile(r"^\+\s*(\d+(?:\.\d+)?)\$?\s*@([a-zA-Z0-9_]+)")

@dp.message(F.text.regexp(ORDER_REGEX))
async def process_order(message: types.Message):
    match = ORDER_REGEX.match(message.text)
    amount = float(match.group(1))
    buyer_username = match.group(2).lower()
    
    # Пытаемся найти покупателя в базе
    user_data = get_or_create_user(buyer_username)
    if not user_data:
        # Если юзер ни разу не писал в чат и его нет в базе, создаем заглушку
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT INTO users (username) VALUES (?)", (buyer_username,))
            conn.commit()
    
    # Обновляем цифры
    user_stats, global_order_id = update_order_stats(buyer_username, amount)
    new_volume, orders_count, buyer_user_id = user_stats
    
    # Формируем красивый чек
    receipt_text = (
        f"<b>[PAY] <u>ОРДЕР ЗАКРЫТ</u></b>\n\n"
        f"📝 Ордер · <b>#{global_order_id + 5000}</b>\n" # +5000 для красоты старта
        f"👤 Скуп · @{buyer_username}\n"
        f"💸 Сумма · <b>{amount}$</b>\n"
        f"📈 Итого · <b>{orders_count}</b> · <b>{new_volume}$</b>"
    )
    
    await message.answer(receipt_text, parse_mode="HTML")
    
    # Логика приглашения в основной чат (если это 3-й ордер)
    if orders_count == 3 and buyer_user_id:
        invite_text = (
            f"🎉 Поздравляем! Вы успешно закрыли 3 ордера.\n"
            f"Добро пожаловать в наш основной чат: {MAIN_CHAT_LINK}"
        )
        try:
            await bot.send_message(chat_id=buyer_user_id, text=invite_text)
        except TelegramAPIError:
            logging.info(f"Не удалось отправить ЛС пользователю @{buyer_username}. Возможно, он не запускал бота.")

# === КОМАНДЫ ===
@dp.message(Command("me"))
async def cmd_me(message: types.Message):
    user_data = get_or_create_user(message.from_user.username, message.from_user.id)
    text = (
        f"💼 <b>Твой профиль:</b>\n"
        f"👤 Участник: @{message.from_user.username}\n"
        f"📊 Закрыто ордеров: <b>{user_data[3]}</b>\n"
        f"💸 Общий оборот: <b>{user_data[2]}$</b>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("info"))
async def cmd_info(message: types.Message, command: CommandObject):
    if not command.args:
        return await message.answer("Укажите юзернейм. Пример: <code>/info @username</code>", parse_mode="HTML")
    
    target_username = command.args.replace("@", "").lower()
    user_data = get_or_create_user(target_username)
    
    if user_data:
        text = (
            f"🔍 <b>Профиль пользователя:</b>\n"
            f"👤 Участник: @{target_username}\n"
            f"📊 Закрыто ордеров: <b>{user_data[3]}</b>\n"
            f"💸 Общий оборот: <b>{user_data[2]}$</b>"
        )
    else:
        text = "🤷‍♂️ Пользователь не найден в базе."
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT total_orders, total_volume FROM stats WHERE id = 1")
        global_stats = cur.fetchone()
        
    text = (
        f"🌐 <b>Статистика проекта:</b>\n"
        f"📝 Всего закрыто ордеров: <b>{global_stats[0]}</b>\n"
        f"💰 Общий оборот: <b>{global_stats[1]}$</b>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "<b>Доступные команды:</b>\n"
        "/me - Посмотреть свой профиль\n"
        "/info @username - Профиль другого участника\n"
        "/stats - Общая статистика проекта\n\n"
        "<i>Для закрытия ордера отправьте:</i>\n"
        "<code>+10$ @username</code>"
    )
    await message.answer(text, parse_mode="HTML")

# === ФОНОВЫЙ ТРЕКИНГ ПОЛЬЗОВАТЕЛЕЙ ===
# Сохраняем user_id всех, кто пишет в чат, чтобы бот мог потом написать им в ЛС
@dp.message()
async def track_all_users(message: types.Message):
    if message.from_user and message.from_user.username:
        get_or_create_user(message.from_user.username, message.from_user.id)

# === WEB-SERVER (ДЛЯ RENDER) ===
async def handle_ping(request):
    return web.Response(text="P2P Bot is running.")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# === ЗАПУСК ===
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await set_bot_commands(bot)
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запускаем веб-сервер и пуллинг одновременно
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot, handle_as_tasks=True)
    )

if __name__ == "__main__":
    asyncio.run(main())
