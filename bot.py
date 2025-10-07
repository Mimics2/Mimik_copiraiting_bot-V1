import logging
import sqlite3
import asyncio
import datetime
import uuid
import httpx
import json
import traceback # Для более подробных логов ошибок в Webhook

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
)
from aiohttp import web
from pytz import timezone

from config import (BOT_TOKEN, ADMIN_IDS, 
                    WEB_SERVER_PORT, MOSCOW_TZ, WEB_SERVER_BASE_URL,
                    CRYPTOPAY_BOT_TOKEN, CRYPTOPAY_WEBHOOK_PATH, CRYPTOPAY_CREATE_INVOICE_URL,
                    DB_NAME) # DB_NAME тоже добавил в импорт, если он используется ниже

from database import Database

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SchedulerBot:
    def __init__(self, db_name):
        self.db = Database(db_name)
        self.user_states = {} # Для хранения состояний пользователей
        self.post_data = {} # Для хранения данных поста во время создания
        self.application = None # Будет установлено в main()
        self.publisher_task = None # Для задачи публикации

    def set_application(self, application):
        self.application = application

    # --- Хелперы ---
    def is_user_admin(self, user_id):
        return user_id in ADMIN_IDS

    def get_moscow_time(self):
        return datetime.datetime.now(MOSCOW_TZ)

    # --- Команды ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.add_user(user.id, user.username) # Добавляем пользователя, если его нет
        await update.message.reply_text(
            f"Привет, {user.first_name}!\n"
            "Я бот для отложенного постинга в Telegram-каналах.\n"
            "Используйте /help для списка команд."
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "Вот команды, которые я понимаю:\n"
            "/add_channel - Добавить канал для постинга.\n"
            "/my_channels - Показать мои привязанные каналы.\n"
            "/remove_channel - Отвязать канал.\n"
            "/schedule_post - Запланировать новый пост.\n"
            "/my_posts - Показать мои запланированные посты.\n"
            "/cancel_post - Отменить запланированный пост.\n"
            "/balance - Проверить баланс.\n"
            "/deposit - Пополнить баланс.\n"
            # "/buy_tariff - Купить тариф." # <-- Если у вас есть эта команда
        )
        await update.message.reply_text(help_text)

    # --- Управление каналами ---
    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_info = self.db.get_user(user_id)
        if not user_info:
            await update.message.reply_text("Пожалуйста, сначала используйте /start.")
            return

        current_channels = self.db.get_user_channels(user_id)
        # Получаем max_channels из user_info (индекс 6)
        max_channels = user_info[6] if user_info and user_info[6] is not None else 1 

        if len(current_channels) >= max_channels:
            await update.message.reply_text(f"❌ Вы достигли лимита каналов для вашего тарифа ({max_channels}).")
            return

        await update.message.reply_text(
            "Чтобы добавить канал, сначала добавьте меня как администратора в ваш канал с правами на публикацию сообщений.\n"
            "Затем перешлите мне любое сообщение из этого канала."
        )
        self.user_states[user_id] = {'stage': 'awaiting_channel_forward'}

    async def my_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        channels = self.db.get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("У вас пока нет привязанных каналов. Используйте /add_channel.")
            return

        response_text = "Ваши привязанные каналы:\n"
        for channel_id, channel_name in channels:
            response_text += f"- **{channel_name}** (`{channel_id}`)\n"
        await update.message.reply_text(response_text, parse_mode='Markdown')

    async def remove_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        channels = self.db.get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("У вас нет привязанных каналов для удаления.")
            return

        keyboard = []
        for channel_id, channel_name in channels:
            keyboard.append([InlineKeyboardButton(channel_name, callback_data=f"remove_channel_{channel_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("Выберите канал для удаления:", reply_markup=reply_markup)
        self.user_states[user_id] = {'stage': 'awaiting_channel_for_removal'}

    # --- Планирование постов ---
    async def schedule_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        channels = self.db.get_user_channels(user_id)
        if not channels:
            await update.message.reply_text("У вас нет привязанных каналов. Сначала используйте /add_channel.")
            return

        # Проверка лимита постов
        user_info = self.db.get_user(user_id)
        if not user_info:
            await update.message.reply_text("Ошибка: Пользователь не найден. Пожалуйста, начните с /start.")
            return
        
        # Получаем количество опубликованных постов за сегодня
        today_posts_count = 0 # В реальном приложении здесь нужна функция db.get_user_posts_today(user_id)
        max_posts_per_day = user_info[7] if user_info and user_info[7] is not None else 2 # Индекс 7 - max_posts_per_day

        if today_posts_count >= max_posts_per_day:
            await update.message.reply_text(f"❌ Вы достигли лимита постов на сегодня ({max_posts_per_day}) для вашего тарифа.")
            return

        keyboard = []
        for channel_id, channel_name in channels:
            keyboard.append([InlineKeyboardButton(channel_name, callback_data=f"schedule_channel_{channel_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("Выберите канал, в который вы хотите запланировать пост:", reply_markup=reply_markup)
        self.user_states[user_id] = {'stage': 'awaiting_post_channel_selection'}

    async def my_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        posts = self.db.get_user_posts(user_id)
        if not posts:
            await update.message.reply_text("У вас нет запланированных постов.")
            return

        response_text = "Ваши запланированные посты:\n"
        keyboard = []
        for post_id, channel_id, text, publish_time, is_published in posts:
            channel_info = self.db.get_channel_info(channel_id)
            channel_name = channel_info[3] if channel_info else f"Канал ID: {channel_id}"
            status = "✅ Опубликован" if is_published else "⏳ В ожидании"
            response_text += (
                f"\n**ID:** {post_id}\n"
                f"**Канал:** {channel_name}\n"
                f"**Время:** {publish_time.astimezone(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"**Статус:** {status}\n"
                f"**Текст:** {text[:50]}...\n"
            )
            if not is_published:
                keyboard.append([InlineKeyboardButton(f"Отменить пост {post_id}", callback_data=f"cancel_post_{post_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=reply_markup)

    async def cancel_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        posts = self.db.get_user_posts(user_id)
        
        pending_posts = [p for p in posts if not p[4]] # p[4] это is_published
        
        if not pending_posts:
            await update.message.reply_text("У вас нет запланированных постов для отмены.")
            return

        keyboard = []
        for post_id, channel_id, text, publish_time, is_published in pending_posts:
            channel_info = self.db.get_channel_info(channel_id)
            channel_name = channel_info[3] if channel_info else f"Канал ID: {channel_id}"
            keyboard.append([InlineKeyboardButton(f"Отменить пост {post_id} ({channel_name} на {publish_time.astimezone(MOSCOW_TZ).strftime('%H:%M')})", callback_data=f"cancel_post_{post_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Выберите пост для отмены:", reply_markup=reply_markup)


    # --- Баланс и Пополнение ---
    async def show_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        balance = self.db.get_user_balance(user_id)
        await update.message.reply_text(f"💰 Ваш текущий баланс: **{balance:.2f} USD**", parse_mode='Markdown')

    async def deposit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id): # Можно убрать is_user_admin для обычных пользователей
            await update.message.reply_text("Для пополнения баланса обратитесь к администратору.")
            return
        
        await update.message.reply_text(
            "💸 Введите сумму в **USD**, на которую хотите пополнить баланс. "
            "Минимальная сумма - 1 USD. Оплата будет производиться через **CryptoPay Bot (USDT)**.",
            parse_mode='Markdown'
        )
        self.user_states[user_id] = {'stage': 'awaiting_deposit_amount_cryptopay'}

    # --- Обработчик текстовых сообщений ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        message_text = update.message.text
        
        current_state = self.user_states.get(user_id, {}).get('stage')

        if current_state == 'awaiting_channel_forward':
            if update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
                channel_id = update.message.forward_from_chat.id
                channel_name = update.message.forward_from_chat.title

                # Проверить, что бот является админом в этом канале
                try:
                    chat_member = await context.bot.get_chat_member(channel_id, self.application.bot.id)
                    if not chat_member.can_post_messages:
                        await update.message.reply_text(
                            "❌ Бот должен быть администратором канала с правом на публикацию сообщений."
                        )
                        self.user_states.pop(user_id, None)
                        return
                except Exception as e:
                    logging.error(f"Error checking bot admin status in channel {channel_id}: {e}")
                    await update.message.reply_text(
                        "❌ Не удалось проверить права бота в канале. Убедитесь, что бот добавлен как администратор."
                    )
                    self.user_states.pop(user_id, None)
                    return

                if self.db.add_channel(user_id, channel_id, channel_name):
                    await update.message.reply_text(f"✅ Канал **{channel_name}** (`{channel_id}`) успешно добавлен!", parse_mode='Markdown')
                else:
                    await update.message.reply_text("❌ Канал уже был добавлен или произошла ошибка.")
                self.user_states.pop(user_id, None)
            else:
                await update.message.reply_text("❌ Пожалуйста, перешлите сообщение именно из канала.")
        
        elif current_state == 'awaiting_post_text':
            self.post_data[user_id]['text'] = message_text
            await update.message.reply_text(
                "Отлично! Теперь отправьте мне медиафайл (фото или видео), который хотите прикрепить к посту.\n"
                "Если пост без медиа, просто отправьте `-` (дефис)."
            )
            self.user_states[user_id] = {'stage': 'awaiting_post_media'}

        elif current_state == 'awaiting_post_time':
            try:
                # Ожидаем время в формате ГГГГ-ММ-ДД ЧЧ:ММ (МСК)
                publish_time_str = message_text
                publish_time_msk = MOSCOW_TZ.localize(datetime.datetime.strptime(publish_time_str, '%Y-%m-%d %H:%M'))
                publish_time_utc = publish_time_msk.astimezone(pytz.utc)

                if publish_time_utc <= datetime.datetime.now(pytz.utc):
                    await update.message.reply_text("❌ Время публикации должно быть в будущем. Попробуйте снова.")
                    return

                channel_id = self.post_data[user_id]['channel_id']
                text = self.post_data[user_id]['text']
                media_ids = json.dumps(self.post_data[user_id].get('media_ids', []))

                self.db.add_post(user_id, channel_id, text, media_ids, publish_time_utc)
                await update.message.reply_text(
                    f"✅ Пост успешно запланирован в канал `{channel_id}` на "
                    f"**{publish_time_msk.strftime('%Y-%m-%d %H:%M:%S')} МСК**!",
                    parse_mode='Markdown'
                )
                self.user_states.pop(user_id, None)
                self.post_data.pop(user_id, None)

            except ValueError:
                await update.message.reply_text(
                    "❌ Неверный формат времени. Пожалуйста, введите в формате ГГГГ-ММ-ДД ЧЧ:ММ (например, 2023-12-31 15:30)."
                )
            except Exception as e:
                logging.error(f"Error scheduling post: {e}")
                await update.message.reply_text("❌ Произошла ошибка при планировании поста.")

        elif current_state == 'awaiting_deposit_amount_cryptopay':
            await self.process_deposit_amount(update, context)

    # --- Обработчик медиа ---
    async def handle_media(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        current_state = self.user_states.get(user_id, {}).get('stage')

        if current_state == 'awaiting_post_media':
            media_ids = []
            if update.message.photo:
                # Telegram отправляет несколько размеров, берем последний (самый большой)
                media_ids.append(update.message.photo[-1].file_id)
            elif update.message.video:
                media_ids.append(update.message.video.file_id)
            elif update.message.text and update.message.text == '-': # Если пользователь отправил '-', значит без медиа
                pass # media_ids останется пустым
            else:
                await update.message.reply_text("❌ Пожалуйста, отправьте фото, видео или '-' для поста без медиа.")
                return
            
            self.post_data[user_id]['media_ids'] = media_ids
            await update.message.reply_text(
                "Теперь введите дату и время публикации поста (МСК) в формате ГГГГ-ММ-ДД ЧЧ:ММ "
                "(например, 2023-12-31 15:30):"
            )
            self.user_states[user_id] = {'stage': 'awaiting_post_time'}
        else:
            await update.message.reply_text("Я не знаю, что делать с этим медиафайлом сейчас. Возможно, вы не в процессе создания поста.")


    # --- Обработчик callback-запросов ---
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data

        await query.answer() # Всегда отвечаем на callback-запрос

        if data.startswith('remove_channel_'):
            channel_id = int(data.split('_')[2])
            self.db.remove_channel(user_id, channel_id)
            await query.edit_message_text(f"✅ Канал `{channel_id}` успешно удален.")
            self.user_states.pop(user_id, None)

        elif data.startswith('schedule_channel_'):
            channel_id = int(data.split('_')[2])
            self.post_data[user_id] = {'channel_id': channel_id}
            await query.edit_message_text("Отлично! Теперь отправьте текст вашего поста.")
            self.user_states[user_id] = {'stage': 'awaiting_post_text'}

        elif data.startswith('cancel_post_'):
            post_id = int(data.split('_')[2])
            post_info = self.db.get_post_info(post_id)
            if post_info and post_info[1] == user_id: # Проверяем, что пост принадлежит пользователю
                self.db.delete_post(post_id)
                await query.edit_message_text(f"✅ Пост с ID `{post_id}` успешно отменен.")
            else:
                await query.edit_message_text("❌ Пост не найден или у вас нет прав на его отмену.")


    # --- Логика публикации по расписанию ---
    async def publish_scheduled_posts(self):
        while True:
            await asyncio.sleep(60) # Проверять каждую минуту
            
            posts_to_publish = self.db.get_posts_to_publish()
            for post_id, user_id, channel_id, text, media_ids_str in posts_to_publish:
                try:
                    media_ids = json.loads(media_ids_str) if media_ids_str else []
                    
                    if media_ids:
                        media_group = []
                        if len(media_ids) == 1: # Один медиафайл
                            file_id = media_ids[0]
                            if text:
                                # Если есть текст, отправляем фото/видео с подписью
                                if len(file_id) > 20: # Простая проверка на file_id (обычно они длинные)
                                    try:
                                        if await self.is_file_video(file_id): # Нужна функция для определения типа медиа
                                            message = await self.application.bot.send_video(
                                                chat_id=channel_id, video=file_id, caption=text, parse_mode='Markdown'
                                            )
                                        else:
                                            message = await self.application.bot.send_photo(
                                                chat_id=channel_id, photo=file_id, caption=text, parse_mode='Markdown'
                                            )
                                    except Exception as e:
                                        logging.error(f"Error determining media type or sending single media: {e}")
                                        message = await self.application.bot.send_message(chat_id=channel_id, text=text, parse_mode='Markdown') # Отправляем только текст
                                else:
                                    message = await self.application.bot.send_message(chat_id=channel_id, text=text, parse_mode='Markdown') # Если file_id короткий, это скорее всего текст
                            else: # Один медиафайл без текста
                                if len(file_id) > 20:
                                    if await self.is_file_video(file_id):
                                        message = await self.application.bot.send_video(chat_id=channel_id, video=file_id)
                                    else:
                                        message = await self.application.bot.send_photo(chat_id=channel_id, photo=file_id)
                                else:
                                    message = await self.application.bot.send_message(chat_id=channel_id, text=text if text else "Пост без текста") # На всякий случай
                        else: # Несколько медиафайлов (media_group)
                            # Первый медиафайл с текстом
                            if len(media_ids[0]) > 20:
                                if await self.is_file_video(media_ids[0]):
                                    media_group.append(InputMediaVideo(media=media_ids[0], caption=text, parse_mode='Markdown'))
                                else:
                                    media_group.append(InputMediaPhoto(media=media_ids[0], caption=text, parse_mode='Markdown'))
                            else: # Если file_id короткий, это ошибка или не файл
                                media_group.append(InputMediaPhoto(media=media_ids[0])) # Без текста, если ошибка

                            # Остальные медиафайлы без текста
                            for mid in media_ids[1:]:
                                if len(mid) > 20:
                                    if await self.is_file_video(mid):
                                        media_group.append(InputMediaVideo(media=mid))
                                    else:
                                        media_group.append(InputMediaPhoto(media=mid))
                            
                            messages = await self.application.bot.send_media_group(chat_id=channel_id, media=media_group)
                            message = messages[0] if messages else None # Берем первое сообщение из группы
                    else: # Пост без медиа
                        message = await self.application.bot.send_message(chat_id=channel_id, text=text, parse_mode='Markdown')

                    if message:
                        self.db.set_post_published(post_id, message.message_id)
                        logging.info(f"Post {post_id} published to {channel_id}.")
                    else:
                        logging.error(f"Failed to get message_id for post {post_id}.")

                except Exception as e:
                    logging.error(f"Error publishing post {post_id} to {channel_id}: {traceback.format_exc()}")
                    # Можно добавить уведомление пользователю об ошибке публикации

    # Простая заглушка для определения типа медиа. В идеале нужно делать запрос к Telegram API.
    # Для большинства случаев, просто по file_id или extension не определить, нужно getFile.
    async def is_file_video(self, file_id: str) -> bool:
        """
        Пытается определить, является ли file_id видео.
        Это очень упрощенная логика, в идеале нужно получить информацию о файле через Telegram API.
        """
        # Обычно file_id видео начинаются с 'BAAD' или имеют другие отличия.
        # Это лишь предположение, которое может быть неточным.
        # Лучший способ: context.bot.get_file(file_id) и проверить file.mime_type
        try:
            file_info = await self.application.bot.get_file(file_id)
            return 'video' in file_info.mime_type
        except Exception:
            return False


    # --- НОВЫЙ ФУНКЦИОНАЛ ДЛЯ CRYPTOPAY BOT ---
    async def create_cryptopay_invoice(self, user_id, amount, update: Update):
        order_id = str(uuid.uuid4())
        
        auth = httpx.BasicAuth(username='', password=CRYPTOPAY_BOT_TOKEN) 
        
        payload = {
            "asset": "USDT", 
            "amount": amount,
            "description": f"Пополнение баланса KolesContent (ID: {user_id})",
            "external_id": order_id, 
            "return_url": WEB_SERVER_BASE_URL
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(CRYPTOPAY_CREATE_INVOICE_URL, json=payload, auth=auth)
                data = response.json()
                
                if response.status_code == 200 and data.get('ok') and data['result'].get('pay_url'):
                    pay_url = data['result']['pay_url']
                    
                    self.db.add_payment(user_id, amount, order_id, 'pending', pay_url, 'cryptopay') 
                    
                    keyboard = [[InlineKeyboardButton("💳 Перейти к оплате", url=pay_url)]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await update.message.reply_text(
                        f"💰 Создан счет на пополнение на **{amount} USDT**.\n"
                        f"🔗 Перейдите по ссылке ниже для оплаты:",
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                else:
                    logging.error(f"CryptoPay Bot invoice creation error: {data}")
                    await update.message.reply_text(f"❌ Не удалось создать счет для оплаты. Ошибка: {data.get('error', response.text)}")
                    
            except Exception as e:
                logging.error(f"HTTP error during deposit with CryptoPay Bot: {e}")
                await update.message.reply_text("❌ Произошла ошибка при связи с платежной системой.")


# --- Webhook handler для CryptoPay Bot (ВНЕ КЛАССА SchedulerBot) ---
async def cryptopay_webhook_handler(request):
    application = request.app['bot_app']
    bot_logic = application.bot_logic 
    
    try:
        data = await request.json()
        logging.info(f"CryptoPay Webhook received: {json.dumps(data)}")

        if data.get('status') == 'paid': 
            external_id = data.get('external_id') 
            amount_paid = float(data.get('amount')) 
            asset = data.get('asset')

            if not external_id:
                logging.warning("CryptoPay Webhook: Missing external_id in paid status.")
                return web.json_response({'status': 'error', 'message': 'Missing external_id'}, status=400)

            payment_info = bot_logic.db.get_payment_by_order_id(external_id)
            
            if payment_info and payment_info[4] == 'pending': 
                user_id = payment_info[1]
                
                bot_logic.db.update_payment_status(external_id, 'success')
                bot_logic.db.add_balance(user_id, amount_paid) 
                
                await application.bot.send_message(
                    chat_id=user_id, 
                    text=f"✅ Баланс пополнен! **{amount_paid:.2f} {asset}** зачислены на ваш счет.", 
                    parse_mode='Markdown'
                )
                logging.info(f"User {user_id} balance updated by {amount_paid} via CryptoPay. Order: {external_id}")
            else:
                logging.warning(f"CryptoPay Webhook: Payment with external_id {external_id} not found or already processed. Status: {payment_info[4] if payment_info else 'not found'}")
            
            return web.json_response({'status': 'ok'}) 
        elif data.get('status') in ['expired', 'cancelled', 'failed']:
            external_id = data.get('external_id')
            if external_id:
                bot_logic.db.update_payment_status(external_id, data.get('status'))
                logging.info(f"CryptoPay payment {external_id} status updated to {data.get('status')}")
            return web.json_response({'status': 'ok'})
        
        return web.json_response({'status': 'ok'}) 
        
    except Exception as e:
        logging.error(f"Error in CryptoPay webhook handler: {traceback.format_exc()}")
        return web.json_response({'status': 'error', 'message': 'Internal server error'}, status=500)


def main():
    bot_logic = SchedulerBot(DB_NAME)
    application = Application.builder().token(BOT_TOKEN).build()
    bot_logic.set_application(application) # Передаем application в bot_logic

    # --- Обработчики команд ---
    application.add_handler(CommandHandler("start", bot_logic.start))
    application.add_handler(CommandHandler("help", bot_logic.help_command))
    application.add_handler(CommandHandler("add_channel", bot_logic.add_channel))
    application.add_handler(CommandHandler("my_channels", bot_logic.my_channels))
    application.add_handler(CommandHandler("remove_channel", bot_logic.remove_channel))
    application.add_handler(CommandHandler("schedule_post", bot_logic.schedule_post))
    application.add_handler(CommandHandler("my_posts", bot_logic.my_posts))
    application.add_handler(CommandHandler("cancel_post", bot_logic.cancel_post))
    application.add_handler(CommandHandler("balance", bot_logic.show_balance))
    application.add_handler(CommandHandler("deposit", bot_logic.deposit)) 

    # --- Обработчик текстовых сообщений (для ввода сумм, текста постов и т.д.) ---
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_message))
    
    # --- Обработчик медиа сообщений (для фото и видео) ---
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, bot_logic.handle_media)) # <-- ДОБАВЛЕНО
    
    # --- Обработчик callback-запросов от inline-кнопок ---
    application.add_handler(CallbackQueryHandler(bot_logic.handle_callback_query))

    # --- Запуск Webhook сервера ---
    async def start_webhook_server():
        app = web.Application()
        app['bot_app'] = application 
        app['bot_logic'] = bot_logic # Также можно передать bot_logic напрямую

        # 1. WebHook для Telegram (основной)
        app.router.add_post(f"/{BOT_TOKEN}", application.update_queue.put) 
        
        # 2. WebHook для CryptoPay Bot
        app.router.add_post(CRYPTOPAY_WEBHOOK_PATH, cryptopay_webhook_handler) 

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT) # Используем '0.0.0.0' для Railway
        await site.start()
        logging.info(f"Webhook server started on 0.0.0.0:{WEB_SERVER_PORT}")
        logging.info(f"Telegram webhook set to: {WEB_SERVER_BASE_URL}/{BOT_TOKEN}")
        logging.info(f"CryptoPay webhook set to: {WEB_SERVER_BASE_URL}{CRYPTOPAY_WEBHOOK_PATH}") 
        
        # Запуск фоновой задачи публикации
        bot_logic.publisher_task = asyncio.create_task(bot_logic.publish_scheduled_posts())
        logging.info("Publisher task started.")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_webhook_server())

    # Установка Telegram Webhook
    loop.run_until_complete(application.bot.set_webhook(url=f"{WEB_SERVER_BASE_URL}/{BOT_TOKEN}"))
    
    # Важно: Webhook для CryptoPay Bot устанавливается ОДИН раз через его API.
    # Вы это уже сделали, указав ссылку на Railway.
    # Проверка, что он установлен: https://pay.crypt.bot/api/getWebhookInfo?token=ВАШ_CRYPTOPAY_БОТ_ТОКЕН

    loop.run_forever()

if __name__ == '__main__':
    main()
