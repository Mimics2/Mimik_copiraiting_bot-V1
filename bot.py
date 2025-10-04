import logging
from datetime import datetime, timedelta
import pytz
import re 
import httpx 
import uuid 
import traceback

from telegram import Update, BotCommand, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import BOT_TOKEN, ADMIN_IDS, CRYPTO_PAY_TOKEN
from database import Database

# --- Настройка логирования и часового пояса ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

class SchedulerBot:
    def __init__(self):
        self.db = Database() 
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

    # --- ФУНКЦИИ ОПЛАТЫ CRYPTOBOT PAY ---
    async def create_crypto_invoice(self, amount: float, asset: str, description: str, payload: str):
        """Генерирует ссылку на оплату через CryptoBot Pay API."""
        try:
            url = "https://pay.crypt.bot/api/createInvoice"
            headers = {
                "Content-Type": "application/json",
                "X-Telegram-Bot-Token": CRYPTO_PAY_TOKEN
            }
            
            data = {
                "asset": asset,
                "amount": amount,
                "description": description,
                "payload": payload,
                "allow_anonymous": False,
                "expires_in": 7200 # Счет действителен 2 часа
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, headers=headers, json=data)
                response.raise_for_status()
                
                result = response.json()
                
                if result.get("ok") and result["result"]:
                    return result["result"]["pay_url"]
                else:
                    logger.error(f"CryptoPay API Error: {result}")
                    return None
        except Exception as e:
            logger.error(f"Ошибка при создании счета CryptoPay: {e}")
            return None


    # --- КОМАНДА /buy ---
    async def buy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username
        
        self.db.get_or_create_user(user_id, username) 

        if self.db.is_user_premium(user_id):
            await update.message.reply_text("✅ У вас уже активен Премиум-доступ! Проверьте срок действия через /status.")
            return

        tariffs = {
            "30_days": {"amount": 1.0, "text": "30 дней ($1.00 USDT)", "days": 30},
            "90_days": {"amount": 2.5, "text": "90 дней ($2.50 USDT)", "days": 90},
            "180_days": {"amount": 4.5, "text": "180 дней ($4.50 USDT)", "days": 180},
        }
        
        message = "👑 <b>ВЫБЕРИТЕ ТАРИФ:</b>\n\n"
        
        for key, info in tariffs.items():
            unique_id = str(uuid.uuid4()).split('-')[0]
            payload = f"user_{user_id}_days_{info['days']}_ref_{unique_id}"
            
            pay_url = await self.create_crypto_invoice(
                amount=info["amount"],
                asset='USDT', 
                description=f"Премиум-доступ к Планировщику ({info['text']})",
                payload=payload
            )
            
            if pay_url:
                message += f"• <b>{info['text']}</b>: <a href='{pay_url}'>Оплатить через CryptoBot</a>\n"
            else:
                message += f"• <b>{info['text']}</b>: ❌ (Ошибка генерации ссылки, попробуйте позже)\n"
        
        self.user_states[user_id] = 'awaiting_payment_proof'
        
        message += "\n\n<b>ПОСЛЕ ОПЛАТЫ:</b>\n"
        message += "Скопируйте <b>Номер счета</b> или <b>ХЭШ ТРАНЗАКЦИИ (TxID)</b> и отправьте его сюда для ручной активации доступа администратором (это вы)."
        
        await update.message.reply_text(message, parse_mode=constants.ParseMode.HTML)


    # --- КОМАНДА /god_mode (НОВАЯ: СЕКРЕТНЫЙ АКТИВАТОР ДЛЯ АДМИНА) ---
    async def god_mode_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id):
             await update.message.reply_text("⛔️ Доступ запрещен. Эта команда только для главного администратора.")
             return
             
        # Активация доступа на 100 лет
        days = 36500 
        new_until = self.db.activate_premium(
            user_id, 
            days, 
            username=update.effective_user.username
        )

        if new_until:
            await update.message.reply_text(
                f"👑 <b>GOD MODE АКТИВИРОВАН!</b>\n"
                f"Ваш Премиум-доступ активирован на <b>{days} дней</b> (до {new_until.strftime('%d.%m.%Y')})."
                , parse_mode=constants.ParseMode.HTML
            )
        else:
            await update.message.reply_text("❌ Ошибка при активации GOD MODE в базе данных.")


    # --- КОМАНДА /activate (ТОЛЬКО ДЛЯ АДМИНОВ) ---
    async def activate_user_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): 
            await update.message.reply_text("❌ Только для администраторов.")
            return

        if not context.args or len(context.args) < 2:
            await update.message.reply_text("❌ Формат: /activate <USER_ID> <ДНИ_ДОСТУПА>")
            return
        
        try:
            target_user_id = int(context.args[0])
            days = int(context.args[1])
            
            new_until = self.db.activate_premium(
                target_user_id, 
                days, 
                username=f"ID:{target_user_id}" 
            )
            
            if new_until:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🥳 Ваш Премиум-доступ активирован на <b>{days} дней</b>! Доступен до: <b>{new_until.strftime('%d.%m.%Y %H:%M')}</b> (МСК).\n"
                         "Теперь вы можете использовать команды планирования (например, /add_post)."
                    , parse_mode=constants.ParseMode.HTML
                )
                
                await update.message.reply_text(
                    f"✅ Доступ для ID <b>{target_user_id}</b> активирован на <b>{days} дней</b> (до {new_until.strftime('%d.%m.%Y %H:%M')}).",
                    parse_mode=constants.ParseMode.HTML
                )
            else:
                await update.message.reply_text("❌ Ошибка при активации доступа в базе данных.")

        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка активации. ID и ДНИ должны быть числами. Ошибка: <code>{e}</code>", parse_mode=constants.ParseMode.HTML)


    # --- КОМАНДЫ СТАТУСА И ИНФОРМАЦИИ ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username
        
        self.db.get_or_create_user(user_id, username)
        
        is_scheduler = self.is_user_scheduler(user_id)
        is_admin = self.is_user_admin(user_id)

        # Вычисляем срок окончания доступа (ИСХОДЯ ИЗ ИСПРАВЛЕНИЯ В database.py)
        premium_until_str = "Нет доступа"
        if is_scheduler:
            user_data = self.db.get_or_create_user(user_id)
            if user_data[3]: # premium_until (дата в формате строки)
                # Безопасно парсим дату, так как db.is_user_premium уже подтвердила ее наличие и корректность
                try:
                    premium_until = datetime.strptime(user_data[3], '%Y-%m-%d %H:%M:%S')
                    premium_until_str = f"до {premium_until.strftime('%d.%m.%Y')}"
                except ValueError:
                    premium_until_str = "Ошибка даты"
            
        
        header = f"🚀 **{constants.DEFAULT_BOT_NAME} - СИСТЕМА АВТОПОСТИНГА** 🚀\n\n"
        
        status_line = (
            f"👤 **Ваш Статус:** {'👑 Администратор' if is_admin else ('✨ Премиум' if is_scheduler else '💼 Обычный')}\n"
            f"🗓 **Доступ активен:** {premium_until_str}\n"
        )
        
        instruction_line = "\n💡 **ИНСТРУКЦИЯ ПО РАБОТЕ:**\n"
        
        if is_scheduler:
             instruction_line += (
                "1. **/add_channel**: Добавьте канал (бота нужно сделать админом там).\n"
                "2. **/add_post**: Выберите канал, укажите текст/медиа и точное время публикации (МСК).\n"
                "3. **/posts**: Отслеживайте запланированные публикации.\n"
             )
        else:
             instruction_line += (
                "Для доступа к планированию нужна активация.\n"
                "Используйте **\u200B/buy** для покупки доступа.\n"
             )
        
        await update.message.reply_text(
            header + status_line + instruction_line,
            parse_mode=constants.ParseMode.MARKDOWN
        )

        # Обновляем список команд (для красивого меню)
        commands = [
            BotCommand("start", "Главное меню и инструкция"),
            BotCommand("status", "Проверить статус доступа и бота"),
            BotCommand("buy", "Купить доступ к планировщику"),
        ]
        if is_scheduler:
             commands.extend([
                BotCommand("add_channel", "Добавить канал"),
                BotCommand("add_post", "Запланировать пост"),
                BotCommand("posts", "Список постов"),
            ])
        if is_admin:
             commands.extend([
                BotCommand("activate", "Активировать доступ пользователю (Admin)"),
                BotCommand("god_mode", "Активация доступа для себя (Admin)"),
            ])
        await context.bot.set_my_commands(commands)


    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        is_scheduler = self.is_user_scheduler(user_id)
        is_admin = self.is_user_admin(user_id)
        
        uptime = datetime.now(MOSCOW_TZ) - self.start_time
        hours, rem = divmod(uptime.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        
        message = (
            f"📊 **СИСТЕМНЫЙ СТАТУС** 📊\n\n"
            f"🕒 **МСК Время:** {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}\n"
        )

        # Статус доступа (ИСПРАВЛЕНО ДЛЯ ОБРАБОТКИ None)
        if is_scheduler:
            user_data = self.db.get_or_create_user(user_id)
            premium_until_str = user_data[3]
            
            if premium_until_str:
                premium_until = datetime.strptime(premium_until_str, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
                message += (
                    f"👤 **Ваш Доступ:** ✅ АКТИВЕН\n"
                    f"🗓 **Срок действия:** до {premium_until} (МСК)\n"
                )
            else:
                 message += f"👤 **Ваш Доступ:** ❌ НЕАКТИВЕН. (Ошибка данных). Используйте /buy для покупки.\n"
        else:
            message += f"👤 **Ваш Доступ:** ❌ НЕАКТИВЕН. Используйте /buy для покупки.\n"

        
        # Данные планировщика
        if is_scheduler:
             channels = self.db.get_channels()
             posts = self.db.get_posts()
             
             next_post_str = "Нет запланированных"
             if posts:
                 next_post_time_naive = datetime.strptime(posts[0][3], '%Y-%m-%d %H:%M:%S')
                 next_post_str = MOSCOW_TZ.localize(next_post_time_naive).strftime('%d.%m.%Y в %H:%M')
                 
             message += (
                 f"\n--- ⚙️ **РАБОЧИЕ ДАННЫЕ** ---\n"
                 f"⏳ **Время работы:** {int(hours)}ч {int(minutes)}м\n"
                 f"🔗 **Каналов:** {len(channels)}\n"
                 f"📝 **Постов в очереди:** {len(posts)}\n"
                 f"🔜 **След. пост:** {next_post_str}"
             )
        
        await update.message.reply_text(message, parse_mode=constants.ParseMode.MARKDOWN)


    # --- Остальные функции (list_channels, add_post, handle_message и т.д.) без изменений ---
    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_scheduler(update.effective_user.id): 
            await update.message.reply_text("❌ Только для Премиум-пользователей. Используйте /buy.")
            return
        
        self.user_states[update.effective_user.id] = 'awaiting_channel_forward'
        await update.message.reply_text(
            "<b>Чтобы добавить канал через пересылку:</b>\n"
            "1. Сделайте этого бота администратором в вашем канале с правом на публикацию сообщений.\n"
            "2. Перешлите сюда любое сообщение из этого канала.\n\n"
            "Или используйте <b>/manual_channel</b> для ручного ввода."
            , parse_mode=constants.ParseMode.HTML
        )
        
    async def manual_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_scheduler(update.effective_user.id): 
            await update.message.reply_text("❌ Только для Премиум-пользователей. Используйте /buy.")
            return
        
        self.user_states[update.effective_user.id] = 'awaiting_channel_manual_id'
        await update.message.reply_text(
            "<b>РЕЖИМ РУЧНОГО ВВОДА:</b>\n"
            "Введите числовой ID канала (например, <code>-1001234567890</code>) и его название через запятую.\n\n"
            "<b>Формат:</b> <code>-ID,Название канала</code>",
            parse_mode=constants.ParseMode.HTML
        )

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_scheduler(update.effective_user.id): 
            await update.message.reply_text("❌ Только для Премиум-пользователей. Используйте /buy.")
            return
        
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("Каналы еще не добавлены. Используйте /add_channel.")
            return
        
        message = "<b>📋 Подключенные каналы:</b>\n\n"
        for db_id, tg_id, title, username, _, default_prompt in channels:
            prompt_status = "✅ Есть промпт" if default_prompt else "❌ Нет промпта"
            message += f"• {title} ({prompt_status})\n"
            message += f"  (ID: <code>{tg_id}</code>, Внутр. ID: <code>{db_id}</code>)\n"
        await update.message.reply_text(message, parse_mode=constants.ParseMode.HTML)


    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_scheduler(update.effective_user.id): 
            await update.message.reply_text("❌ Только для Премиум-пользователей. Используйте /buy.")
            return
        
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал с помощью /add_channel.")
            return

        message = "<b>Выберите канал для публикации:</b>\n\n"
        for db_id, _, title, _, _, _ in channels:
            message += f"• <b>{title}</b> (Внутр. ID: <code>{db_id}</code>)\n"
        
        message += "\nВведите **внутренний ID** канала (число в скобках)."
        
        self.user_states[update.effective_user.id] = 'awaiting_target_channel_id'
        await update.message.reply_text(message, parse_mode=constants.ParseMode.HTML)


    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_scheduler(update.effective_user.id): 
            await update.message.reply_text("❌ Только для Премиум-пользователей. Используйте /buy.")
            return
        
        posts = self.db.get_posts()
        if not posts:
            await update.message.reply_text("📭 Нет запланированных постов.")
            return

        message = "<b>📋 Запланированные посты (по МСК):</b>\n\n"
        for post in posts:
            post_id, _, message_text, scheduled_time_str, _, _, _, media_type, channel_title, _ = post
            post_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
            time_formatted = MOSCOW_TZ.localize(post_time_naive).strftime('%d.%m.%Y %H:%M')
            
            media_info = f" ({media_type.upper()})" if media_type else ""
            
            text_snippet = message_text[:40].replace('\n', ' ') + ('...' if len(message_text) > 40 else '')
            message += f"• <b>{time_formatted}</b>{media_info} в '{channel_title}'\n"
            message += f"  <i>Текст: {text_snippet}</i>\n"
        await update.message.reply_text(message, parse_mode=constants.ParseMode.HTML)
        
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_states: return

        state = self.user_states[user_id]
        
        # 9. ОЖИДАНИЕ ДОКАЗАТЕЛЬСТВА ОПЛАТЫ (/buy)
        if state == 'awaiting_payment_proof':
            proof_text = update.message.text.strip()
            username = update.effective_user.username
            
            for admin_id in self.admin_ids:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"🚨 НОВЫЙ ЗАПРОС НА ОПЛАТУ\n"
                         f"Пользователь: @{username or 'Нет юзернейма'} (ID: <code>{user_id}</code>)\n"
                         f"Предполагаемый ХЭШ/Номер счета: <b>{proof_text}</b>\n"
                         f"<b>--- ДЕЙСТВИЯ АДМИНА ---</b>\n"
                         f"1. Проверьте платеж в интерфейсе CryptoBot Pay (используя токен).\n"
                         f"2. Если оплата подтверждена, активируйте доступ командой:\n"
                         f"<code>/activate {user_id} 30</code> (или 90, 180 дней)",
                    parse_mode=constants.ParseMode.HTML
                )
                
            await update.message.reply_text("✅ Ваша заявка принята! Мы уведомили администратора о необходимости проверить платеж (это займет время).")
            
            del self.user_states[user_id]
            context.user_data.clear()
            return
        
        # Проверка доступа для всех остальных действий, связанных с планированием (1-8)
        if not self.is_user_scheduler(user_id) and state not in ('awaiting_new_admin_id', 'awaiting_prompt_channel_id', 'awaiting_new_prompt_text'):
             await update.message.reply_text("❌ Для этого действия требуется Премиум-доступ. Используйте /buy.")
             del self.user_states[user_id]
             context.user_data.clear()
             return
        
        # ... (логика блоков 1-8: ОСТАВЛЯЕМ БЕЗ ИЗМЕНЕНИЙ) ...
        
        # 1. АВТОМАТИЧЕСКАЯ ПРИВЯЗКА (/add_channel)
        if state == 'awaiting_channel_forward':
            if update.message.forward_from_chat:
                channel_id = update.message.forward_from_chat.id
                title = update.message.forward_from_chat.title or "Без названия"
                username = update.message.forward_from_chat.username

                if self.db.add_channel(channel_id, title, username):
                    await update.message.reply_text(f"✅ Канал <b>'{title}'</b> добавлен!", parse_mode=constants.ParseMode.HTML)
                else:
                    await update.message.reply_text("❌ Канал уже был добавлен или произошла ошибка БД.")
            else:
                await update.message.reply_text("❌ Пожалуйста, перешлите сообщение именно из канала.")
            del self.user_states[user_id]
            
        # 2. РУЧНАЯ ПРИВЯЗКА (/manual_channel)
        elif state == 'awaiting_channel_manual_id':
            try:
                parts = update.message.text.split(',', 1)
                if len(parts) != 2: raise ValueError
                
                channel_id = int(parts[0].strip())
                title = parts[1].strip()
                username = None # Ручной ввод без юзернейма

                if self.db.add_channel(channel_id, title, username):
                    await update.message.reply_text(f"✅ Канал <b>'{title}'</b> добавлен вручную! Убедитесь, что бот является его администратором.", parse_mode=constants.ParseMode.HTML)
                else:
                    await update.message.reply_text("❌ Канал уже был добавлен или произошла ошибка БД.")
            except ValueError:
                await update.message.reply_text("❌ Неверный формат. Используйте: <code>-ID,Название канала</code>", parse_mode=constants.ParseMode.HTML)
            del self.user_states[user_id]
            
        # 3. ДОБАВЛЕНИЕ НОВОГО АДМИНИСТРАТОРА (/add_admin)
        elif state == 'awaiting_new_admin_id':
            if not self.is_user_admin(user_id): return
            try:
                new_admin_id = int(update.message.text.strip())
                if self.db.add_admin(new_admin_id):
                    self.admin_ids = self.db.get_admin_ids() # Обновляем кеш
                    await update.message.reply_text(f"✅ Пользователь с ID <b>{new_admin_id}</b> добавлен как администратор!", parse_mode=constants.ParseMode.HTML)
                else:
                    await update.message.reply_text("❌ Пользователь уже является администратором или произошла ошибка.")
            except ValueError:
                await update.message.reply_text("❌ ID администратора должен быть числом.")
            del self.user_states[user_id]

        # 4. ОЖИДАНИЕ ВЫБОРА КАНАЛА ДЛЯ ПОСТА (/add_post)
        elif state == 'awaiting_target_channel_id':
            try:
                channel_db_id = int(update.message.text.strip())
                channel_info = self.db.get_channel_info_by_db_id(channel_db_id)
                
                if channel_info:
                    context.user_data['target_channel_id'] = channel_db_id
                    context.user_data['target_channel_title'] = channel_info[2]
                    self.user_states[user_id] = 'awaiting_post_text'
                    await update.message.reply_text(f"✅ Выбран канал <b>'{channel_info[2]}'</b>.\n\nТеперь отправьте мне **текст** (и/или **фото/видео**) для публикации.", parse_mode=constants.ParseMode.HTML)
                else:
                    await update.message.reply_text("❌ Канал с таким внутренним ID не найден. Попробуйте снова.")
            except ValueError:
                await update.message.reply_text("❌ Внутренний ID должен быть числом.")
        
        # 5. ДОБАВЛЕНИЕ ТЕКСТА / МЕДИА ПОСТА (Продолжение /add_post)
        elif state == 'awaiting_post_text': 
            text = update.message.caption or update.message.text or ""
            media_file_id = None
            media_type = None

            if update.message.photo:
                media_file_id = update.message.photo[-1].file_id # Берем самое большое фото
                media_type = 'photo'
            elif update.message.video:
                media_file_id = update.message.video.file_id
                media_type = 'video'

            if not text and not media_file_id:
                await update.message.reply_text("❌ Пожалуйста, отправьте текст и/или медиафайл.")
                return

            context.user_data['post_text'] = text
            context.user_data['media_file_id'] = media_file_id
            context.user_data['media_type'] = media_type

            self.user_states[user_id] = 'awaiting_post_time'
            await update.message.reply_text(
                "✅ Текст/Медиа сохранено.\n\n"
                "Теперь введите **время публикации** в формате: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (Время по МСК).", 
                parse_mode=constants.ParseMode.HTML
            )

        # 6. ДОБАВЛЕНИЕ ВРЕМЕНИ ПОСТА
        elif state == 'awaiting_post_time':
            time_str = update.message.text.strip()
            try:
                scheduled_time_naive = datetime.strptime(time_str, '%d.%m.%Y %H:%M')
                scheduled_time_moscow = MOSCOW_TZ.localize(scheduled_time_naive)
                
                if scheduled_time_moscow < datetime.now(MOSCOW_TZ) + timedelta(minutes=1):
                    await update.message.reply_text("❌ Время публикации должно быть в будущем.")
                    return
                
                # Сохраняем пост в БД
                post_id = self.db.add_post(
                    context.user_data['target_channel_id'],
                    context.user_data['post_text'],
                    scheduled_time_moscow.strftime('%Y-%m-%d %H:%M:%S'),
                    context.user_data.get('media_file_id'),
                    context.user_data.get('media_type')
                )

                if post_id:
                    await update.message.reply_text(
                        f"🎉 **ПОСТ УСПЕШНО ЗАПЛАНИРОВАН!**\n\n"
                        f"Канал: <b>{context.user_data['target_channel_title']}</b>\n"
                        f"Время (МСК): <b>{time_str}</b>\n"
                        f"Текст: {context.user_data['post_text'][:50]}...",
                        parse_mode=constants.ParseMode.HTML
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении поста в базу данных.")

            except ValueError:
                await update.message.reply_text("❌ Неверный формат времени. Используйте: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>", parse_mode=constants.ParseMode.HTML)
                return
            
            del self.user_states[user_id]
            context.user_data.clear()
            
        # 7. ОЖИДАНИЕ ID КАНАЛА ДЛЯ УСТАНОВКИ ПРОМПТА
        elif state == 'awaiting_prompt_channel_id':
            if not self.is_user_admin(user_id): return
            try:
                channel_db_id = int(update.message.text.strip())
                channel_info = self.db.get_channel_info_by_db_id(channel_db_id)

                if channel_info:
                    context.user_data['prompt_target_channel_id'] = channel_info[1] # TG ID
                    context.user_data['prompt_target_channel_title'] = channel_info[2]
                    self.user_states[user_id] = 'awaiting_new_prompt_text'
                    await update.message.reply_text(f"✅ Выбран канал <b>'{channel_info[2]}'</b>. Отправьте мне текст промпта.", parse_mode=constants.ParseMode.HTML)
                else:
                    await update.message.reply_text("❌ Канал с таким внутренним ID не найден. Попробуйте снова.")
            except ValueError:
                await update.message.reply_text("❌ Внутренний ID должен быть числом.")
        
        # 8. ОЖИДАНИЕ ТЕКСТА ПРОМПТА
        elif state == 'awaiting_new_prompt_text':
            if not self.is_user_admin(user_id): return
            new_prompt = update.message.text
            tg_channel_id = context.user_data['prompt_target_channel_id']
            channel_title = context.user_data['prompt_target_channel_title']

            if self.db.set_channel_prompt(tg_channel_id, new_prompt):
                await update.message.reply_text(f"✅ Для канала <b>'{channel_title}'</b> установлен новый промпт:\n\n<code>{new_prompt}</code>", parse_mode=constants.ParseMode.HTML)
            else:
                await update.message.reply_text("❌ Ошибка при обновлении промпта в БД.")

            del self.user_states[user_id]
            context.user_data.clear()


    # --- КОМАНДЫ АДМИНА ---
    async def show_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Текущее Московское время: **{datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}**", parse_mode=constants.ParseMode.MARKDOWN)

    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        self.user_states[update.effective_user.id] = 'awaiting_new_admin_id'
        await update.message.reply_text("Введите ID пользователя, которого хотите назначить администратором:")

    async def list_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        admins = self.db.get_admins()
        message = "<b>👑 Список Администраторов:</b>\n\n"
        for _, user_id, username, _ in admins:
            message += f"• <code>{user_id}</code> (@{username or 'Нет имени'})\n"
        await update.message.reply_text(message, parse_mode=constants.ParseMode.HTML)

    async def set_prompt_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал с помощью /add_channel.")
            return

        message = "<b>Выберите канал для установки промпта (для ИИ):</b>\n\n"
        for db_id, _, title, _, _, _ in channels:
            message += f"• <b>{title}</b> (Внутр. ID: <code>{db_id}</code>)\n"
        
        message += "\nВведите **внутренний ID** канала."
        self.user_states[update.effective_user.id] = 'awaiting_prompt_channel_id'
        await update.message.reply_text(message, parse_mode=constants.ParseMode.HTML)

    async def test_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        # Логика для тестового поста...
        await update.message.reply_text("Тестовая публикация запланирована на 5 минут вперед.")
        

    # --- СИСТЕМНЫЕ ЗАДАЧИ ---
    async def check_posts_job(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            posts = self.db.get_posts()
            current_time = datetime.now(MOSCOW_TZ)
            
            for post in posts:
                post_id, _, message_text, scheduled_time_str, _, _, media_file_id, media_type, _, tg_channel_id = post
                
                scheduled_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
                scheduled_time_moscow = MOSCOW_TZ.localize(scheduled_time_naive)
                
                if scheduled_time_moscow <= current_time:
                    try:
                        await self.publish_post(post_id, tg_channel_id, message_text, media_file_id, media_type, context)
                    except Exception as e:
                        logger.error(f"Ошибка публикации поста ID {post_id}: {e}")
                        self.db.update_post_status(post_id, 'failed')
        except Exception as e:
            logger.error(f"Ошибка в задаче проверки постов: {e}")

    async def publish_post(self, post_id, tg_channel_id, message_text, media_file_id, media_type, context: ContextTypes.DEFAULT_TYPE):
        if media_file_id:
            if media_type == 'photo':
                await context.bot.send_photo(
                    chat_id=tg_channel_id, 
                    photo=media_file_id, 
                    caption=message_text,
                    parse_mode=constants.ParseMode.HTML # Используем HTML для форматирования
                )
            elif media_type == 'video':
                await context.bot.send_video(
                    chat_id=tg_channel_id, 
                    video=media_file_id, 
                    caption=message_text,
                    parse_mode=constants.ParseMode.HTML
                )
            else:
                # Если медиафайл есть, но тип неизвестен, отправляем только текст
                await context.bot.send_message(
                    chat_id=tg_channel_id, 
                    text=message_text,
                    parse_mode=constants.ParseMode.HTML
                )
        else:
            await context.bot.send_message(
                chat_id=tg_channel_id, 
                text=message_text,
                parse_mode=constants.ParseMode.HTML
            )
        
        self.db.update_post_status(post_id, 'published')
        logger.info(f"Пост ID {post_id} успешно опубликован в канале {tg_channel_id}.")

# НОВЫЙ ОБРАБОТЧИК ОШИБОК для устранения сообщения в логах
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логирует ошибки, вызванные обработчиками."""
    logger.error("Произошло исключение: %s", context.error)
    # Печатаем трассировку для отладки
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error("Traceback:\n%s", tb_string)

    # Опционально: Отправляем сообщение администратору о критической ошибке
    if update:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        error_message = f"🚨 ВАЖНО: В боте произошла ошибка при обработке команды от пользователя ID `{user_id}`. См. логи для подробностей."
        try:
             await context.bot.send_message(chat_id=chat_id, text="❌ Произошла внутренняя ошибка. Попробуйте снова или обратитесь к администратору.")
        except:
             pass # Не упасть, если не можем отправить сообщение об ошибке


def main():
    """Запуск бота."""
    application = Application.builder().token(BOT_TOKEN).build()
    bot = SchedulerBot()

    job_queue = application.job_queue
    job_queue.run_repeating(bot.check_posts_job, interval=10, first=5)

    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("status", bot.status))
    application.add_handler(CommandHandler("time", bot.show_time))
    
    # НОВЫЕ КОМАНДЫ (Оплата и Активация)
    application.add_handler(CommandHandler("buy", bot.buy_command))
    application.add_handler(CommandHandler("activate", bot.activate_user_command)) 
    application.add_handler(CommandHandler("god_mode", bot.god_mode_command)) # Секретная команда

    
    # Команды, доступные для Premium/Admin
    application.add_handler(CommandHandler("add_channel", bot.add_channel))
    application.add_handler(CommandHandler("manual_channel", bot.manual_channel))
    application.add_handler(CommandHandler("set_prompt", bot.set_prompt_command))
    application.add_handler(CommandHandler("channels", bot.list_channels))
    application.add_handler(CommandHandler("add_post", bot.add_post))
    application.add_handler(CommandHandler("posts", bot.list_posts))
    
    # Команды только для Admin
    application.add_handler(CommandHandler("add_admin", bot.add_admin_command))
    application.add_handler(CommandHandler("admins", bot.list_admins))
    application.add_handler(CommandHandler("test_post", bot.test_post))

    
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, bot.handle_message))

    # РЕГИСТРАЦИЯ ОБРАБОТЧИКА ОШИБОК
    application.add_error_handler(error_handler)

    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()
