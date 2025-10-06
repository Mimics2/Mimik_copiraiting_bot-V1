# bot (1) (4).py

import logging
from datetime import datetime, timedelta
import pytz
import re 
import httpx # Для асинхронных HTTP-запросов
import uuid 
import traceback
import os
import json
import asyncio

from telegram import Update, BotCommand, constants, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
from aiohttp import web 

from config import (BOT_TOKEN, ADMIN_IDS, CRYPTO_CLOUD_API_KEY, 
                    CRYPTO_CLOUD_CREATE_URL, CRYPTO_CLOUD_WEBHOOK_SECRET, 
                    WEB_SERVER_PORT, WEBHOOK_PATH, MOSCOW_TZ, WEB_SERVER_BASE_URL)
from database import Database

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Основной класс Бота ---

class SchedulerBot:
    def __init__(self):
        self.db = Database() 
        # Добавляем ADMIN_IDS из конфига в базу данных при старте
        for admin_id in ADMIN_IDS:
            self.db.add_admin(admin_id, username="Initial_Config_Admin")
            
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}
        self.admin_ids = self.db.get_admin_ids()

    # --- ПРОВЕРКИ ДОСТУПА ---

    def is_user_admin(self, user_id):
        self.admin_ids = self.db.get_admin_ids()
        return user_id in self.admin_ids

    def is_user_scheduler(self, user_id):
        """Проверяет, имеет ли пользователь право на планирование (Админ ИЛИ Премиум)."""
        return self.is_user_admin(user_id) or self.db.is_user_premium(user_id)

    # --- СЕРВИСНЫЕ КОМАНДЫ ---

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.is_user_scheduler(user_id):
            await update.message.reply_text("❌ У вас нет доступа к планированию постов. Используйте /buy для получения доступа.")
            return
        
        # Установка команд
        commands = [
            BotCommand("start", "Запуск бота"),
            BotCommand("status", "Статус бота и время"),
            BotCommand("buy", "Купить Premium-доступ"),
            BotCommand("add_channel", "Добавить канал"),
            BotCommand("channels", "Список каналов"),
            BotCommand("add_post", "Добавить публикацию"),
            BotCommand("posts", "Список публикаций"),
        ]
        
        await context.bot.set_my_commands(commands)
        
        current_time_str = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        
        await update.message.reply_text(
            f"🤖 Бот запущен.\n"
            f"Текущее время (МСК): {current_time_str}\n"
            f"Используйте команды для управления планировщиком." 
        )
        
    # --- ЗАГЛУШКИ ДЛЯ КОМАНД ПЛАНИРОВЩИКА (добавь здесь свою логику) ---
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Функция /status пока в разработке.")

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Функция /add_channel пока в разработке.")

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Функция /channels пока в разработке.")

    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Функция /add_post пока в разработке.")

    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Функция /posts пока в разработке.")
        
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Здесь должна быть основная логика обработки сообщений/состояний
        pass
    
    # --- КОМАНДА: Добавление администратора ---
    
    async def add_admin_secret_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Секретная команда для добавления администратора по ID. Использование: /addadmin 123456"""
        user_id = update.effective_user.id

        if not self.is_user_admin(user_id):
            await update.message.reply_text("⛔️ У вас нет доступа к этой команде.")
            return

        try:
            # Получаем ID нового администратора из аргументов команды
            new_admin_id = int(context.args[0])
            
            # Получаем username
            new_admin_username = context.args[1] if len(context.args) > 1 else (update.message.reply_to_message.from_user.username if update.message.reply_to_message and update.message.reply_to_message.from_user else "Unknown")

            if self.db.add_admin(new_admin_id, new_admin_username):
                self.admin_ids = self.db.get_admin_ids()
                
                await update.message.reply_text(
                    f"✅ Пользователь с ID **{new_admin_id}** ({new_admin_username}) успешно добавлен в администраторы.", 
                    parse_mode=constants.ParseMode.MARKDOWN
                )
                
                await context.bot.send_message(new_admin_id, 
                                               "🎉 Вы были назначены администратором бота!")
            else:
                await update.message.reply_text("⚠️ Ошибка базы данных при добавлении администратора.")

        except (IndexError, ValueError):
            await update.message.reply_text(
                "❌ Неверный формат. Используйте: `/addadmin [ID пользователя] [Username (опц.)]`",
                parse_mode=constants.ParseMode.MARKDOWN
            )

    # --- ЛОГИКА CRYPTOCLOUD ---
    
    def create_payment_invoice(self, amount: float, user_id: int):
        """
        Отправляет запрос на CryptoCloud для создания счета.
        """
        order_id = str(uuid.uuid4())
        
        headers = {
            "Authorization": f"Token {CRYPTO_CLOUD_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": amount, 
            "currency": "USD", 
            "order_id": order_id, 
            "metadata": {"telegram_user_id": user_id}, 
            "success_url": f"{WEB_SERVER_BASE_URL}" 
        }
        
        try:
            with httpx.Client() as client:
                response = client.post(CRYPTO_CLOUD_CREATE_URL, headers=headers, json=payload)
                response.raise_for_status() 
            
            result = response.json()
            
            if result.get('status') == 'success':
                self.db.add_order(order_id, user_id, amount)
                return result['result']['url'], order_id
            else:
                logger.error(f"CryptoCloud API error: {result.get('message')}")
                return None, None
                
        except httpx.RequestError as e:
            logger.error(f"Request error to CryptoCloud: {e}")
            return None, None

    async def buy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        price = 5.00 # $5 за Premium
        
        link, order_id = self.create_payment_invoice(price, user_id)
        
        if link:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(f"Оплатить ${price:.2f} (30 дней)", url=link)]])
            await update.message.answer(
                f"💰 Создан счет №{order_id}.\nPremium-доступ на 30 дней. Нажмите 'Оплатить' для активации доступа.",
                reply_markup=keyboard,
                parse_mode=constants.ParseMode.MARKDOWN
            )
        else:
            await update.message.answer("Извините, произошла ошибка при создании счета. Попробуйте позже.")


# --- WebHook ОБРАБОТЧИК ДЛЯ CRYPTOCLOUD (HTTP-сервер) ---

async def cryptocloud_webhook_handler(request):
    """
    Обработчик POST-запросов от CryptoCloud.
    """
    application = request.app['bot_app']
    db_instance = application.bot_data['db']
    bot_instance = application.bot

    try:
        data = await request.json()
    except json.JSONDecodeError:
        return web.json_response({'status': 'error', 'message': 'Invalid JSON'}, status=400)

    # 1. ПРОВЕРКА БЕЗОПАСНОСТИ
    security_key = data.get('security')
    if security_key != CRYPTO_CLOUD_WEBHOOK_SECRET:
        logger.warning(f"Ошибка безопасности WebHook. Получен ключ: {security_key}")
        return web.json_response({'status': 'error', 'message': 'Invalid security key'}, status=403)
    
    # Получаем данные
    status = data.get('status')
    order_id = data.get('invoice_id')
    user_id_str = data.get('metadata', {}).get('telegram_user_id')
    
    if status == 'paid' and user_id_str:
        user_id = int(user_id_str)
        
        # 1. Обновляем статус заказа в БД и активируем подписку
        db_instance.update_order_status(order_id, 'paid')
        end_date = db_instance.add_or_update_premium_user(user_id, days=30)
        
        logging.info(f"✅ Оплата успешна. Order ID: {order_id}, User ID: {user_id}. PREMIUM АКТИВИРОВАН.")

        # 2. Отправляем пользователю сообщение об успехе
        if user_id and end_date:
            try:
                end_date_str = end_date.strftime('%d.%m.%Y')
                await bot_instance.send_message(user_id, 
                                                f"🎉 Ваша Premium-подписка активирована до **{end_date_str}**! Спасибо за оплату.",
                                                parse_mode=constants.ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
        
        return web.json_response({'status': 'ok'})
        
    elif status in ['fail', 'error']:
        logger.warning(f"❌ Платеж не прошел или ошибка. Order ID: {order_id}")
        return web.json_response({'status': 'ok'}) 
        
    return web.json_response({'status': 'ok'})


# --- ЗАПУСК БОТА НА RAILWAY ЧЕРЕЗ AIOHTTP ---

def main():
    # Создаем экземпляры
    bot_logic = SchedulerBot()
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Сохраняем базу данных в контексте приложения
    application.bot_data['db'] = bot_logic.db 
    application.bot_data['config'] = __import__('config') 
    
    # --- Регистрация команд ---
    application.add_handler(CommandHandler("start", bot_logic.start))
    application.add_handler(CommandHandler("buy", bot_logic.buy_command))
    application.add_handler(CommandHandler("addadmin", bot_logic.add_admin_secret_command)) 
    
    # Регистрация команд планировщика
    application.add_handler(CommandHandler("status", bot_logic.show_status)) 
    application.add_handler(CommandHandler("add_channel", bot_logic.add_channel))
    application.add_handler(CommandHandler("channels", bot_logic.list_channels))
    application.add_handler(CommandHandler("add_post", bot_logic.add_post))
    application.add_handler(CommandHandler("posts", bot_logic.list_posts))
    
    # Обработчик обычных сообщений
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, bot_logic.handle_message))


    # Асинхронная функция для запуска сервера
    async def start_webhook_server():
        # Устанавливаем WebHook для Telegram
        await application.bot.set_webhook(url=f"{WEB_SERVER_BASE_URL}/{BOT_TOKEN}")
        
        # Создаем AIOHTTP приложение
        app = web.Application()
        app['bot_app'] = application
        
        # 1. WebHook для Telegram (стандартный путь)
        app.router.add_post(f"/{BOT_TOKEN}", application.update_queue.put) 
        
        # 2. WebHook для CryptoCloud (кастомный путь)
        app.router.add_post(WEBHOOK_PATH, cryptocloud_webhook_handler)

        # Запускаем Telegram Application, чтобы он обрабатывал обновления из очереди
        await application.initialize()
        await application.start()
        
        # Запуск AIOHTTP сервера
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT)
        
        logger.info(f"🚀 Запуск WebHook-сервера на порту {WEB_SERVER_PORT}")
        await site.start()
        
        # Ждем остановки
        await application.run_until_shutdown()

    try:
        # Запускаем сервер и бота в главном цикле
        asyncio.run(start_webhook_server())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
        

if __name__ == '__main__':
    main()
