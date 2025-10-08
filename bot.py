import logging
import sqlite3
import asyncio
import datetime
import pytz
import uuid
import httpx
import json
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
)
from aiohttp import web

from config import (
    BOT_TOKEN, ADMIN_IDS,
    WEB_SERVER_PORT, MOSCOW_TZ, WEB_SERVER_BASE_URL,
    CRYPTOPAY_BOT_TOKEN, CRYPTOPAY_WEBHOOK_PATH, CRYPTOPAY_CREATE_INVOICE_URL,
    DB_NAME
)
from database import Database

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SchedulerBot:
    def __init__(self, db_name):
        self.db = Database(db_name)
        self.user_states = {}
        self.post_data = {}
        self.application = None
        self.publisher_task = None
        self.start_time = datetime.datetime.now(MOSCOW_TZ)

    def set_application(self, application):
        self.application = application

    def is_user_admin(self, user_id):
        return user_id in ADMIN_IDS

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not self.is_user_admin(user.id):
            await update.message.reply_text("❌ У вас нет доступа к этому боту")
            return
            
        self.db.add_user(user.id, user.username)
        await update.message.reply_text(
            f"Привет, {user.first_name}!\n"
            "Я бот для отложенного постинга в Telegram-каналах.\n"
            "Используйте /help для списка команд."
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        help_text = (
            "Команды:\n"
            "/add_channel - Добавить канал.\n"
            "/my_channels - Показать мои каналы.\n"
            "/remove_channel - Отвязать канал.\n"
            "/schedule_post - Запланировать пост.\n"
            "/my_posts - Показать мои посты.\n"
            "/cancel_post - Отменить пост.\n"
            "/balance - Проверить баланс.\n"
            "/deposit - Пополнить баланс."
        )
        await update.message.reply_text(help_text)

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        user_info = self.db.get_user(user_id)
        if not user_info:
            await update.message.reply_text("Пожалуйста, сначала используйте /start.")
            return

        current_channels = self.db.get_user_channels(user_id)
        max_channels = user_info[6] if user_info and user_info[6] is not None else 1

        if len(current_channels) >= max_channels:
            await update.message.reply_text(f"❌ Вы достигли лимита каналов ({max_channels}).")
            return

        await update.message.reply_text(
            "1. Добавьте меня как администратора в ваш канал с правом на публикацию сообщений.\n"
            "2. Перешлите мне любое сообщение из этого канала."
        )
        self.user_states[user_id] = {'stage': 'awaiting_channel_forward'}

    async def my_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        channels = self.db.get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("У вас нет привязанных каналов. Используйте /add_channel.")
            return

        response_text = "Ваши каналы:\n"
        for channel_id, channel_name in channels:
            response_text += f"- **{channel_name}** (`{channel_id}`)\n"
        await update.message.reply_text(response_text, parse_mode='Markdown')

    async def remove_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        channels = self.db.get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("У вас нет каналов для удаления.")
            return

        keyboard = [[InlineKeyboardButton(name, callback_data=f"remove_channel_{cid}")] for cid, name in channels]
        await update.message.reply_text("Выберите канал для удаления:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def schedule_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        channels = self.db.get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("Сначала добавьте канал через /add_channel.")
            return

        keyboard = [[InlineKeyboardButton(name, callback_data=f"schedule_channel_{cid}")] for cid, name in channels]
        await update.message.reply_text("Выберите канал для поста:", reply_markup=InlineKeyboardMarkup(keyboard))
        self.user_states[user_id] = {'stage': 'awaiting_post_channel_selection'}

    async def my_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        posts = self.db.get_user_posts(user_id)
        if not posts:
            await update.message.reply_text("У вас нет запланированных постов.")
            return

        response_text = "Ваши запланированные посты:\n"
        for post_id, channel_id, text, publish_time_str, is_published in posts:
            channel_info = self.db.get_channel_info(channel_id)
            channel_name = channel_info[3] if channel_info else f"Канал ID: {channel_id}"
            status = "✅ Опубликован" if is_published else "⏳ В ожидании"

            publish_time_dt = datetime.datetime.fromisoformat(publish_time_str)
            moscow_time_str = publish_time_dt.astimezone(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S')

            response_text += (
                f"\n**ID:** {post_id} | **Канал:** {channel_name}\n"
                f"**Время:** {moscow_time_str} МСК\n"
                f"**Статус:** {status}\n"
                f"**Текст:** {(text or '')[:50]}...\n"
            )
        await update.message.reply_text(response_text, parse_mode='Markdown')

    async def cancel_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        pending_posts = [p for p in self.db.get_user_posts(user_id) if not p[4]]
        if not pending_posts:
            await update.message.reply_text("Нет постов для отмены.")
            return

        keyboard = []
        for post_id, channel_id, text, publish_time_str, is_published in pending_posts:
            publish_time_dt = datetime.datetime.fromisoformat(publish_time_str)
            time_str = publish_time_dt.astimezone(MOSCOW_TZ).strftime('%H:%M')
            keyboard.append([InlineKeyboardButton(f"Отменить пост {post_id} на {time_str}", callback_data=f"cancel_post_{post_id}")])

        await update.message.reply_text("Выберите пост для отмены:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        balance = self.db.get_user_balance(user_id)
        await update.message.reply_text(f"💰 Ваш баланс: **{balance:.2f} USD**", parse_mode='Markdown')
        
    async def deposit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
            
        await update.message.reply_text(
            "💸 Введите сумму в **USD** для пополнения.\n"
            "Минимальная сумма - 1 USD. Оплата через **@CryptoBot**.",
            parse_mode='Markdown'
        )
        self.user_states[update.effective_user.id] = {'stage': 'awaiting_deposit_amount'}

    async def create_cryptopay_invoice(self, user_id, amount_str: str, update: Update):
        try:
            amount = float(amount_str)
            if amount < 1.0:
                await update.message.reply_text("❌ Минимальная сумма - 1 USD.")
                return
        except ValueError:
            await update.message.reply_text("❌ Введите корректное число.")
            return

        order_id = str(uuid.uuid4())

        headers = {'Crypto-Pay-API-Token': CRYPTOPAY_BOT_TOKEN}
        payload = {
            "asset": "USDT",
            "amount": amount,
            "description": f"Пополнение баланса (user_id: {user_id})",
            "external_id": order_id,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(CRYPTOPAY_CREATE_INVOICE_URL, headers=headers, json=payload)
                data = response.json()

                if response.status_code == 201 and data.get('ok'):
                    pay_url = data['result']['pay_url']
                    self.db.add_payment(user_id, amount, order_id, 'pending', pay_url, 'cryptopay')
                    keyboard = [[InlineKeyboardButton("💳 Перейти к оплате", url=pay_url)]]
                    await update.message.reply_text(
                        f"💰 Создан счет на **{amount} USDT**.",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='Markdown'
                    )
                else:
                    logging.error(f"CryptoPay invoice error: {data}")
                    await update.message.reply_text("❌ Не удалось создать счет.")
            except Exception as e:
                logging.error(f"HTTP error during deposit: {e}")
                await update.message.reply_text("❌ Ошибка связи с платежной системой.")

    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает статус бота, время и статистику"""
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        try:
            current_time = datetime.datetime.now(MOSCOW_TZ)
            current_time_str = current_time.strftime('%d.%m.%Y %H:%M:%S')
            
            uptime = current_time - self.start_time
            hours, remainder = divmod(uptime.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{int(hours)}ч {int(minutes)}м {int(seconds)}с"
            
            channels = self.db.get_channels()
            posts = self.db.get_scheduled_posts()
            
            scheduled_count = len(posts)
            
            next_post_time = "Нет запланированных"
            if posts:
                next_post = posts[0]
                next_time_str = next_post[5]
                next_time = datetime.datetime.strptime(next_time_str, '%Y-%m-%d %H:%M:%S')
                next_post_time = MOSCOW_TZ.localize(next_time).strftime('%d.%m.%Y %H:%M')
            
            status_message = (
                f"🤖 **СТАТУС БОТА**\n\n"
                f"⏰ **Текущее время:** {current_time_str} (МСК)\n"
                f"🕐 **Время работы:** {uptime_str}\n"
                f"📊 **Каналов подключено:** {len(channels)}\n"
                f"📅 **Запланировано публикаций:** {scheduled_count}\n"
                f"⏱ **Ближайшая публикация:** {next_post_time}\n"
                f"🟢 **Статус:** Активен\n\n"
                f"_Последнее обновление: {current_time.strftime('%H:%M:%S')}_"
            )
            
            await update.message.reply_text(status_message, parse_mode='Markdown')
            
        except Exception as e:
            logging.error(f"Error in status command: {e}")
            await update.message.reply_text("❌ Ошибка при получении статуса")

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        channels = self.db.get_user_channels(user_id)
        
        if not channels:
            await update.message.reply_text("📭 Каналы не добавлены")
            return
        
        message = "📋 **Список каналов:**\n\n"
        for channel in channels:
            message += f"• {channel[1]} ({channel[0]})\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            return
            
        state = self.user_states.get(user_id, {}).get('stage')

        if state == 'awaiting_channel_forward':
            if update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
                channel_id = update.message.forward_from_chat.id
                channel_name = update.message.forward_from_chat.title
                
                try:
                    member = await context.bot.get_chat_member(channel_id, context.bot.id)
                    if member.status != 'administrator' or not member.can_post_messages:
                        raise Exception("Not an admin with post rights")
                except Exception:
                    await update.message.reply_text("❌ Бот должен быть админом с правом на публикацию.")
                    return

                if self.db.add_channel(user_id, channel_id, channel_name):
                    await update.message.reply_text(f"✅ Канал **{channel_name}** добавлен!", parse_mode='Markdown')
                else:
                    await update.message.reply_text("❌ Ошибка добавления канала.")
                self.user_states.pop(user_id, None)
            else:
                await update.message.reply_text("❌ Пожалуйста, перешлите сообщение из канала.")

        elif state == 'awaiting_post_text':
            self.post_data.setdefault(user_id, {})['text'] = update.message.text
            await update.message.reply_text("Отправьте фото/видео или `-` (дефис), если медиа нет.")
            self.user_states[user_id]['stage'] = 'awaiting_post_media'

        elif state == 'awaiting_post_media' and update.message.text == '-':
            await update.message.reply_text("Введите время публикации (МСК) в формате `ГГГГ-ММ-ДД ЧЧ:ММ`")
            self.user_states[user_id]['stage'] = 'awaiting_post_time'

        elif state == 'awaiting_post_time':
            try:
                moscow_time = MOSCOW_TZ.localize(datetime.datetime.strptime(update.message.text, '%Y-%m-%d %H:%M'))
                utc_time = moscow_time.astimezone(pytz.utc)

                if utc_time <= datetime.datetime.now(pytz.utc):
                    await update.message.reply_text("❌ Время должно быть в будущем.")
                    return

                post_info = self.post_data.get(user_id, {})
                self.db.add_post(user_id, post_info['channel_id'], post_info.get('text'), json.dumps(post_info.get('media_ids', [])), utc_time.isoformat())
                await update.message.reply_text(f"✅ Пост запланирован на **{moscow_time.strftime('%Y-%m-%d %H:%M')}** МСК!", parse_mode='Markdown')
                self.user_states.pop(user_id, None)
                self.post_data.pop(user_id, None)
            except (ValueError, KeyError):
                await update.message.reply_text("❌ Неверный формат времени или ошибка. Попробуйте снова.")

        elif state == 'awaiting_deposit_amount':
            await self.create_cryptopay_invoice(user_id, update.message.text, update)
            self.user_states.pop(user_id, None)
            
    async def handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
            return
            
        if self.user_states.get(user_id, {}).get('stage') == 'awaiting_post_media':
            media_id = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
            self.post_data.setdefault(user_id, {})['media_ids'] = [media_id]
            await update.message.reply_text("Введите время публикации (МСК) в формате `ГГГГ-ММ-ДД ЧЧ:ММ`")
            self.user_states[user_id]['stage'] = 'awaiting_post_time'

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data
        await query.answer()
        
        if not self.is_user_admin(user_id):
            return

        if data.startswith('remove_channel_'):
            self.db.remove_channel(user_id, int(data.split('_')[2]))
            await query.edit_message_text("✅ Канал удален.")
        elif data.startswith('schedule_channel_'):
            self.post_data[user_id] = {'channel_id': int(data.split('_')[2])}
            await query.edit_message_text("Отправьте текст поста.")
            self.user_states[user_id] = {'stage': 'awaiting_post_text'}
        elif data.startswith('cancel_post_'):
            self.db.delete_post(int(data.split('_')[2]))
            await query.edit_message_text("✅ Пост отменен.")

    async def publish_scheduled_posts(self):
        while True:
            await asyncio.sleep(60)
            posts = self.db.get_posts_to_publish()
            for post_id, user_id, channel_id, text, media_ids_str in posts:
                try:
                    media_ids = json.loads(media_ids_str or '[]')
                    message = None
                    if not media_ids:
                        message = await self.application.bot.send_message(channel_id, text, parse_mode='Markdown')
                    else:
                        message = await self.application.bot.send_photo(channel_id, media_ids[0], caption=text, parse_mode='Markdown')

                    if message:
                        self.db.set_post_published(post_id, message.message_id)
                        logging.info(f"Post {post_id} published.")
                except Exception as e:
                    logging.error(f"Error publishing post {post_id}: {traceback.format_exc()}")

async def cryptopay_webhook_handler(request):
    application = request.app['bot_app']
    bot_logic = request.app['bot_logic']
    try:
        data = await request.json()
        logging.info(f"CryptoPay Webhook received: {data}")

        if data.get('update_type') == 'invoice_paid':
            payload = data['payload']
            order_id = payload.get('external_id')
            payment_info = bot_logic.db.get_payment_by_order_id(order_id)

            if payment_info and payment_info[4] == 'pending':
                user_id, amount = payment_info[1], float(payment_info[2])
                bot_logic.db.update_payment_status(order_id, 'success')
                bot_logic.db.add_balance(user_id, amount)
                await application.bot.send_message(user_id, f"✅ Баланс пополнен на **{amount:.2f} USD**.", parse_mode='Markdown')
                logging.info(f"User {user_id} balance updated for order {order_id}")

        return web.json_response({'status': 'ok'})
    except Exception:
        logging.error(f"Error in CryptoPay webhook: {traceback.format_exc()}")
        return web.json_response({'status': 'error'}, status=500)

async def run_bot_and_tasks(application, bot_logic):
    """
    Основная асинхронная функция для запуска всех задач.
    """
    # Запускаем фоновую задачу для публикации постов
    bot_logic.publisher_task = asyncio.create_task(bot_logic.publish_scheduled_posts())
    
    # Создаем и запускаем веб-сервер для вебхуков
    runner = web.AppRunner(web.Application())
    runner.app['bot_app'] = application
    runner.app['bot_logic'] = bot_logic
    runner.app.router.add_post(CRYPTOPAY_WEBHOOK_PATH, cryptopay_webhook_handler)

    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT)
    await site.start()
    logging.info(f"Payment webhook server started on port {WEB_SERVER_PORT}")
    
    # Запускаем polling бота. Эта задача будет работать до завершения.
    await application.run_polling()

def main():
    bot_logic = SchedulerBot(DB_NAME)
    application = Application.builder().token(BOT_TOKEN).build()
    bot_logic.set_application(application)

    commands_to_register = [
        ("start", bot_logic.start),
        ("help", bot_logic.help_command),
        ("status", bot_logic.show_status),
        ("add_channel", bot_logic.add_channel),
        ("my_channels", bot_logic.my_channels),
        ("remove_channel", bot_logic.remove_channel),
        ("schedule_post", bot_logic.schedule_post),
        ("my_posts", bot_logic.my_posts),
        ("cancel_post", bot_logic.cancel_post),
        ("balance", bot_logic.balance),
        ("deposit", bot_logic.deposit)
    ]
    
    for command_name, handler_func in commands_to_register:
        application.add_handler(CommandHandler(command_name, handler_func))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, bot_logic.handle_media))
    application.add_handler(CallbackQueryHandler(bot_logic.handle_callback_query))

    # Запускаем все асинхронные задачи.
    try:
        asyncio.run(run_bot_and_tasks(application, bot_logic))
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == '__main__':
    main()
