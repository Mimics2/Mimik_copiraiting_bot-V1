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
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext, CallbackQueryHandler
from aiohttp import web 

from config import (BOT_TOKEN, ADMIN_IDS, CRYPTO_CLOUD_API_KEY, 
                    CRYPTO_CLOUD_CREATE_URL, CRYPTO_CLOUD_WEBHOOK_SECRET, 
                    WEB_SERVER_PORT, WEBHOOK_PATH, MOSCOW_TZ, WEB_SERVER_BASE_URL, WEBHOOK_URL)
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
        for admin_id in ADMIN_IDS:
            self.db.add_admin(admin_id, username="Initial_Config_Admin")
            
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}
        # Загрузка admin_ids из базы на старте
        self.admin_ids = self.db.get_admin_ids() 

    # --- ПРОВЕРКИ ДОСТУПА ---

    def is_user_admin(self, user_id):
        # Обновляем список админов на случай изменений
        self.admin_ids = self.db.get_admin_ids() 
        return user_id in self.admin_ids

    # --- СТАРТ / СТАТУС ---

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа к этому боту")
            return
        
        commands = [
            BotCommand("start", "Запуск бота"),
            BotCommand("status", "Статус бота и время"),
            BotCommand("add_channel", "Добавить канал"),
            BotCommand("channels", "Список каналов"),
            BotCommand("add_post", "Добавить публикацию"),
            BotCommand("posts", "Список публикаций"),
            BotCommand("deposit", "Пополнить баланс"),
            BotCommand("balance", "Посмотреть баланс"), # Добавил команду balance
        ]
        
        await context.bot.set_my_commands(commands)
        
        balance = self.db.get_user_balance(user_id)
        
        message = (
            f"✅ Бот-планировщик запущен!\n"
            f"👤 Вы вошли как **Администратор**.\n"
            f"💰 Ваш баланс: **{balance:.2f} USD**.\n"
            f"⚙️ Используйте команды ниже, чтобы управлять публикациями."
        )
        await update.message.reply_text(message, parse_mode='Markdown')

    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return
        
        uptime = datetime.now(MOSCOW_TZ) - self.start_time
        
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        status_message = (
            f"🤖 Статус: **РАБОТАЕТ**\n"
            f"🕰️ Запущен: {self.start_time.strftime('%d.%m.%Y %H:%M:%S')} МСК\n"
            f"⏱️ Время работы: {uptime.days} дн., {hours} ч., {minutes} мин."
        )
        await update.message.reply_text(status_message, parse_mode='Markdown')

    async def show_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return
        
        user_id = update.effective_user.id
        balance = self.db.get_user_balance(user_id)
        
        await update.message.reply_text(
            f"💰 Ваш текущий баланс: **{balance:.2f} USD**.",
            parse_mode='Markdown'
        )

    # --- УПРАВЛЕНИЕ КАНАЛАМИ ---

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return

        await update.message.reply_text(
            "Напишите @username или ID (цифрами) канала, куда нужно постить, "
            "и **предварительно добавьте меня туда администратором**."
        )
        self.user_states[update.effective_user.id] = 'awaiting_channel'

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return
        
        channels = self.db.get_all_channels()
        
        if not channels:
            await update.message.reply_text("Нет добавленных каналов.")
            return

        message = "📋 **Добавленные каналы:**\n\n"
        for i, channel in enumerate(channels, 1):
            channel_id, title, username = channel[1], channel[2], channel[3]
            
            # Если title не указан (напр., для ID), используем username/ID
            name = title if title else (f"@{username}" if username else str(channel_id))
            
            message += f"{i}. **{name}** (ID: `{channel_id}`)\n"
            
        await update.message.reply_text(message, parse_mode='Markdown')

    # --- ЗАПЛАНИРОВАННЫЕ ПУБЛИКАЦИИ ---

    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return

        channels = self.db.get_all_channels()
        
        if not channels:
            await update.message.reply_text(
                "❌ Сначала добавьте канал командой /add_channel."
            )
            return

        buttons = []
        for channel in channels:
            channel_id, title, username = channel[1], channel[2], channel[3]
            name = title if title else (f"@{username}" if username else str(channel_id))
            buttons.append([InlineKeyboardButton(name, callback_data=f"select_channel_{channel_id}")])

        reply_markup = InlineKeyboardMarkup(buttons)
        await update.message.reply_text("Выберите канал для публикации:", reply_markup=reply_markup)

        self.user_states[update.effective_user.id] = {'stage': 'awaiting_channel_for_post', 'data': {}}


    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return

        posts = self.db.get_scheduled_posts()
        
        if not posts:
            await update.message.reply_text("Нет запланированных публикаций.")
            return
        
        # Получаем структуру: (post_id, channel_id, message_text, scheduled_time_str, status, created_date, media_file_id, media_type, channel_title, tg_channel_id)
        current_time = datetime.now(MOSCOW_TZ)
        message = f"📋 **Запланированные публикации** (МСК):\\n\\n"
        
        for post in posts:
            post_id, db_channel_id, message_text, scheduled_time_str, status, created_date, media_file_id, media_type, channel_title, tg_channel_id = post
            
            # Конвертируем время в объект datetime и локализуем его
            try:
                post_time_utc = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc)
            except ValueError:
                # Если формат времени в базе неверен, пропускаем
                continue 
            
            moscow_time = post_time_utc.astimezone(MOSCOW_TZ)
            time_str = moscow_time.strftime('%d.%m.%Y %H:%M')
            
            channel_name = channel_title if channel_title else str(tg_channel_id)
            
            # Обрезаем текст для превью
            text_preview = message_text.split('\n')[0][:50] + "..." if message_text and len(message_text) > 50 else (message_text or " [Текст отсутствует] ")
            media_info = f" ({media_type.upper()})" if media_type else ""
            
            message += f"• `{post_id}`: **{time_str}** в **{channel_name}**{media_info} - {text_preview}\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')

    # --- КРИПТОВАЛЮТНЫЕ ПЛАТЕЖИ (DEPOSIT) ---
    
    async def deposit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            return
        
        await update.message.reply_text(
            "💸 Введите сумму в **USD**, на которую хотите пополнить баланс. "
            "Минимальная сумма - 1 USD."
        )
        self.user_states[update.effective_user.id] = {'stage': 'awaiting_deposit_amount'}

    async def process_deposit_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if self.user_states.get(user_id, {}).get('stage') != 'awaiting_deposit_amount':
            return
        
        try:
            amount = float(update.message.text)
            if amount < 1.0:
                await update.message.reply_text("❌ Сумма должна быть не меньше 1 USD. Попробуйте снова.")
                return
        except ValueError:
            await update.message.reply_text("❌ Введите корректную сумму числом. Попробуйте снова.")
            return

        order_id = str(uuid.uuid4())
        
        headers = {
            "Authorization": f"Token {CRYPTO_CLOUD_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": amount,
            "currency": "USD",
            "order_id": order_id,
            "shop_id": "0",
            "period": 10,
            "webhook_url": WEBHOOK_URL,
            "success_url": WEB_SERVER_BASE_URL
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(CRYPTO_CLOUD_CREATE_URL, headers=headers, json=payload)
                data = response.json()
                
                if response.status_code == 200 and data.get('status') == 'success':
                    pay_url = data['result']['pay_url']
                    
                    self.db.add_payment(user_id, amount, order_id, 'pending', pay_url)
                    
                    keyboard = [[InlineKeyboardButton("💳 Перейти к оплате", url=pay_url)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await update.message.reply_text(
                        f"💰 Создан счет на пополнение на **{amount} USD**.\n"
                        f"🔗 Перейдите по ссылке ниже для оплаты:",
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                else:
                    logger.error(f"CryptoCloud error: {data}")
                    await update.message.reply_text(f"❌ Не удалось создать счет для оплаты. Ошибка: {data.get('message', response.text)}")
                    
            except Exception as e:
                logger.error(f"HTTP error during deposit: {e}")
                await update.message.reply_text("❌ Произошла ошибка при связи с платежной системой.")

        self.user_states.pop(user_id, None)

    # --- Обработка ВСЕХ сообщений ---

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if not self.is_user_admin(user_id):
            return

        state = self.user_states.get(user_id, {}).get('stage')
        state_data = self.user_states.get(user_id, {}).get('data', {})

        if state == 'awaiting_channel':
            # --- Обработка добавления канала ---
            text = update.message.text.strip()
            channel_identifier = text.replace('@', '')
            
            try:
                chat = await context.bot.get_chat(channel_identifier)
                
                if chat.type not in [constants.ChatType.CHANNEL, constants.ChatType.SUPERGROUP]:
                    await update.message.reply_text("❌ Это не похоже на канал. Убедитесь, что это публичный канал или ваш бот добавлен в него как администратор.")
                    return
                
                # Проверка, что бот является администратором (Упрощенная проверка)
                try:
                    me = await context.bot.get_me()
                    chat_member = await context.bot.get_chat_member(chat.id, me.id)
                    if chat_member.status not in [constants.ChatMember.ADMINISTRATOR, constants.ChatMember.CREATOR]:
                        await update.message.reply_text("❌ Сначала сделайте бота администратором в этом канале.")
                        return
                except Exception:
                    await update.message.reply_text("⚠️ Не удалось проверить права бота. Убедитесь, что бот является администратором, иначе постинг не сработает.")

                
                self.db.add_channel(
                    channel_id=chat.id, 
                    title=chat.title, 
                    username=chat.username
                )
                
                await update.message.reply_text(f"✅ Канал **{chat.title}** (ID: `{chat.id}`) успешно добавлен!", parse_mode='Markdown')
                self.user_states.pop(user_id) 
                
            except Exception as e:
                logger.error(f"Error adding channel: {traceback.format_exc()}")
                await update.message.reply_text(
                    "❌ Не удалось получить информацию о канале. "
                    "Убедитесь, что:\n"
                    "1. Вы ввели верный @username или ID.\n"
                    "2. Бот добавлен в этот канал как администратор."
                )
        
        elif state == 'awaiting_deposit_amount':
            await self.process_deposit_amount(update, context)

        elif state == 'awaiting_post_text':
            # --- Обработка текста поста ---
            
            state_data['text'] = update.message.text
            self.user_states[user_id]['stage'] = 'awaiting_post_time'
            
            await update.message.reply_text(
                "📅 Введите **дату и время** публикации в формате `ГГГГ-ММ-ДД ЧЧ:ММ` (например, `2025-10-10 14:30`) "
                "по Московскому времени (МСК)."
            )

        elif state == 'awaiting_post_time':
            # --- Обработка времени поста ---
            
            time_str = update.message.text.strip()
            
            try:
                # Парсим время
                post_datetime = datetime.strptime(time_str, '%Y-%m-%d %H:%M')
                # Добавляем часовой пояс МСК
                post_datetime_moscow = MOSCOW_TZ.localize(post_datetime)
                
                # Конвертируем в UTC для хранения в базе
                post_datetime_utc = post_datetime_moscow.astimezone(pytz.utc)
                
                # Проверка, что время не в прошлом
                if post_datetime_moscow <= datetime.now(MOSCOW_TZ) + timedelta(minutes=1):
                    await update.message.reply_text(
                        "❌ Время публикации должно быть хотя бы на 1 минуту в будущем. Попробуйте снова."
                    )
                    return
                
                # Финализируем пост
                channel_id_tg = state_data['channel_id_tg']
                
                channel_data = self.db.get_channel_by_tg_id(channel_id_tg)
                if not channel_data:
                    await update.message.reply_text("❌ Ошибка: Выбранный канал не найден в базе. Начните сначала.")
                    self.user_states.pop(user_id)
                    return
                
                db_channel_id = channel_data[0] # Внутренний ID канала в БД
                
                post_id = self.db.add_post(
                    channel_id=db_channel_id, 
                    message_text=state_data.get('text', ''), 
                    scheduled_time=post_datetime_utc,
                )
                
                if post_id:
                    await update.message.reply_text(
                        f"✅ Публикация **#{post_id}** успешно **запланирована**!\n"
                        f"Канал: **{channel_data[2]}**\n"
                        f"Время (МСК): **{post_datetime_moscow.strftime('%d.%m.%Y %H:%M')}**\n\n"
                        "Текст:\n"
                        f"```\\n{state_data.get('text', '')[:200]}...\\n```"
                    , parse_mode='Markdown')
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении публикации в базу.")
                
                self.user_states.pop(user_id) # Очищаем состояние
                
            except ValueError:
                await update.message.reply_text(
                    "❌ Неверный формат даты/времени. Пожалуйста, используйте `ГГГГ-ММ-ДД ЧЧ:ММ`."
                )

    # --- Обработка НАЖАТИЙ КНОПОК ---
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        
        await query.answer()

        if not self.is_user_admin(user_id):
            return

        data = query.data
        
        if data.startswith('select_channel_'):
            # --- Выбор канала для постинга ---
            try:
                channel_id_tg = int(data.split('_')[-1])
            except ValueError:
                await query.edit_message_text("❌ Ошибка при выборе канала. Попробуйте снова.")
                return
            
            # Сохраняем ID канала и переходим к ожиданию текста
            self.user_states[user_id]['stage'] = 'awaiting_post_text'
            self.user_states[user_id]['data']['channel_id_tg'] = channel_id_tg
            
            await query.edit_message_text(
                "💬 Канал выбран. Теперь отправьте **текст** сообщения для публикации. "
                "(Пока без фото/видео)"
            )

# --- WebHook / CryptoCloud Handlers ---

async def cryptocloud_webhook_handler(request):
    """
    Обработчик для WebHook уведомлений от CryptoCloud.
    """
    try:
        data = await request.json()
        order_id = data.get('order_id')
        status = data.get('status') # 'success', 'fail'
        amount = data.get('amount')
        
        logger.info(f"CryptoCloud Webhook received: Order {order_id}, Status: {status}")
        
        if order_id and status:
            application = request.app['bot_app']
            bot_logic = application.bot_logic
            
            # Получаем Application, чтобы отправить сообщение пользователю
            
            payment_info = bot_logic.db.get_payment_by_order_id(order_id)
            if payment_info:
                db_id, user_id, payment_amount = payment_info[0], payment_info[1], payment_info[2]
                
                # Обновляем статус платежа
                bot_logic.db.update_payment_status(order_id, status)
                
                # Уведомляем пользователя
                if status == 'success':
                    bot_logic.db.add_balance(user_id, payment_amount)
                    message = f"✅ Баланс пополнен! **{payment_amount} USD** зачислены на ваш счет."
                elif status == 'fail':
                    message = f"❌ Платеж по заказу `{order_id}` не удался. Попробуйте снова."
                else:
                    message = f"ℹ️ Статус платежа `{order_id}`: {status}."
                    
                await application.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
                
            else:
                logger.warning(f"CryptoCloud Webhook: Payment with order_id {order_id} not found in DB.")
                
            return web.json_response({'status': 'ok'})
        
        return web.json_response({'status': 'error', 'message': 'Invalid data'}, status=400)
        
    except Exception as e:
        logger.error(f"Error in CryptoCloud webhook handler: {traceback.format_exc()}")
        return web.json_response({'status': 'error', 'message': 'Internal server error'}, status=500)


def main():
    # --- Инициализация ---
    bot_logic = SchedulerBot()
    
    # Инициализируем Telegram Application
    # ВАЖНО: PTB 21+ требует, чтобы CallbackQueryHandler был импортирован
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.bot_logic = bot_logic
    
    # --- Добавление обработчиков ---
    
    application.add_handler(CommandHandler("start", bot_logic.start))
    application.add_handler(CommandHandler("status", bot_logic.show_status))
    application.add_handler(CommandHandler("balance", bot_logic.show_balance)) # Добавлен
    application.add_handler(CommandHandler("add_channel", bot_logic.add_channel))
    application.add_handler(CommandHandler("channels", bot_logic.list_channels))
    application.add_handler(CommandHandler("add_post", bot_logic.add_post))
    application.add_handler(CommandHandler("posts", bot_logic.list_posts))
    application.add_handler(CommandHandler("deposit", bot_logic.deposit))

    # Обработчик Inline-кнопок
    # Используем правильный импорт CallbackQueryHandler
    application.add_handler(CallbackQueryHandler(bot_logic.handle_callback_query, pattern='^select_channel_'))

    # Обработчик сообщений
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

        # 3. Запуск AIOHTTP сервера (захват порта 8080)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT)
        
        logger.info(f"🚀 Запуск WebHook-сервера на порту {WEB_SERVER_PORT}")
        await site.start()
        
        # 4. Запускаем Telegram Application в фоновом режиме для обработки очереди
        await application.initialize()
        await application.start()
        
        # --- ИСПРАВЛЕНИЕ ОШИБКИ ATTRIBUTEERROR ---
        # Вместо удаленного application.run_until_shutdown()
        # используем бесконечный цикл, чтобы процесс не завершился
        while True:
            # Ждем долго, чтобы процесс не завершился. Railway будет поддерживать его.
            await asyncio.sleep(3600) 
            
        # application.stop() не будет вызван, но это нормально для контейнеров (Railway)

    try:
        # Запускаем сервер и бота в главном цикле
        asyncio.run(start_webhook_server())
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
    except Exception:
        logger.error(f"Глобальная ошибка при запуске: {traceback.format_exc()}")

if __name__ == '__main__':
    main()
