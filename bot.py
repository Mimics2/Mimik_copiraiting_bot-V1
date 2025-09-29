import logging
from datetime import datetime
import pytz
import re 
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- ПРЕДПОЛАГАЕМ, ЧТО ЭТИ ФАЙЛЫ (config, database) СУЩЕСТВУЮТ ---
# from config import BOT_TOKEN, ADMIN_IDS
# from database import Database

# Заглушки для корректной работы кода без фактических файлов:
BOT_TOKEN = "YOUR_BOT_TOKEN"
ADMIN_IDS = [123456789] # Замените на ваш ID
class Database:
    def __init__(self):
        # Структура: id, tg_channel_id, title, username, added_date
        self.channels = {
            1: (1, -1001234567890, "Копирайтинг PRO", "copy_pro", datetime.now()),
            2: (2, -1001987654321, "Личный Блог", "my_personal_blog", datetime.now())
        }
        # Структура: post_id, channel_db_id, message_text, scheduled_time_str, status, created_date, channel_title, tg_channel_id
        self.posts = []

    def get_channels(self):
        return list(self.channels.values())
    
    def get_channel_by_db_id(self, db_id):
        return self.channels.get(db_id)

    def add_channel(self, tg_channel_id, title, username):
        if not any(c[1] == tg_channel_id for c in self.channels.values()):
            new_id = max(self.channels.keys()) + 1 if self.channels else 1
            self.channels[new_id] = (new_id, tg_channel_id, title, username, datetime.now())
            return True
        return False

    def get_posts(self):
        # В реальном коде тут должна быть выборка из БД
        return [p for p in self.posts if p[4] != 'published']

    def add_post(self, channel_db_id, message_text, scheduled_time_str):
        post_id = len(self.posts) + 1
        channel = self.get_channel_by_db_id(channel_db_id)
        if not channel: return False
        
        _, tg_channel_id, channel_title, _, _ = channel
        
        self.posts.append((
            post_id, 
            channel_db_id, 
            message_text, 
            scheduled_time_str, 
            'pending', 
            datetime.now(), 
            channel_title, 
            tg_channel_id
        ))
        # Сортируем посты по времени для get_posts
        self.posts.sort(key=lambda x: datetime.strptime(x[3], '%Y-%m-%d %H:%M:%S'))
        return True

    def update_post_status(self, post_id, status):
        # Обновление статуса в реальной БД
        pass 
# -------------------------------------------------------------------


# --- Настройка логирования и часового пояса ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# Используем Москву как эталон для планирования
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

class SchedulerBot:
    def __init__(self):
        # Используем заглушку, замените на self.db = Database()
        self.db = Database() 
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}  # Словарь для хранения состояний пользователей

    # --- ФУНКЦИИ ПЛАНИРОВАНИЯ ---
    async def check_posts_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Периодически проверяет базу данных на наличие постов для публикации."""
        try:
            posts = self.db.get_posts()
            current_time = datetime.now(MOSCOW_TZ)

            for post in posts:
                # Структура: post_id, channel_db_id, message_text, scheduled_time_str, status, created_date, channel_title, tg_channel_id
                post_id, _, message_text, scheduled_time_str, _, _, _, tg_channel_id = post
                
                # Преобразуем время из строки в объект datetime
                post_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
                post_time_aware = MOSCOW_TZ.localize(post_time_naive)

                # Если время публикации настало, отправляем пост
                if post_time_aware <= current_time:
                    logger.info(f"Публикую пост {post_id} в канал {tg_channel_id}")
                    await self.publish_post(post_id, tg_channel_id, message_text, context)
        except Exception as e:
            logger.error(f"Ошибка в задаче проверки постов: {e}")

    async def publish_post(self, post_id, channel_id, message_text, context: ContextTypes.DEFAULT_TYPE):
        """Отправляет сообщение в канал и обновляет статус поста."""
        try:
            # Преобразуем ID канала в строку для chat_id (это важно для API)
            await context.bot.send_message(chat_id=str(channel_id), text=message_text, parse_mode='HTML')
            self.db.update_post_status(post_id, 'published')
            logger.info(f"✅ Пост {post_id} успешно опубликован в канал {channel_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка публикации поста {post_id} в канал {channel_id}: {e}")
            self.db.update_post_status(post_id, 'error')


    # --- ФУНКЦИИ ВРЕМЕНИ И СТАТУСА ---
    async def show_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает текущее время в Москве (МСК)."""
        if update.effective_user.id not in ADMIN_IDS: return
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        await update.message.reply_text(
            f"Текущее время в Москве (МСК): \n<b>{current_time}</b>",
            parse_mode='HTML'
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return

        uptime = datetime.now(MOSCOW_TZ) - self.start_time
        hours, rem = divmod(uptime.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        
        channels = self.db.get_channels()
        posts = self.db.get_posts()
        
        next_post_str = "Нет запланированных постов"
        if posts:
            next_post_time_naive = datetime.strptime(posts[0][3], '%Y-%m-%d %H:%M:%S')
            next_post_str = MOSCOW_TZ.localize(next_post_time_naive).strftime('%d.%m.%Y в %H:%M')

        message = (
            f"<b>🤖 СТАТУС БОТА</b>\n\n"
            f"<b>Время работы:</b> {int(hours)}ч {int(minutes)}м\n"
            f"<b>Подключено каналов:</b> {len(channels)}\n"
            f"<b>Запланировано постов:</b> {len(posts)}\n"
            f"<b>Следующий пост:</b> {next_post_str}\n"
            f"<b>Московское время:</b> {datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')}"
        )
        await update.message.reply_text(message, parse_mode='HTML')


    # --- ОБРАБОТЧИКИ КОМАНД ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return

        commands = [
            BotCommand("status", "Посмотреть статус бота"),
            BotCommand("time", "Показать текущее время (МСК)"),
            BotCommand("add_channel", "Добавить новый канал"),
            BotCommand("channels", "Список подключенных каналов"),
            BotCommand("add_post", "Запланировать новый пост"),
            BotCommand("posts", "Список запланированных постов"),
            BotCommand("test_post", "Проверить публикацию в первом канале"),
            BotCommand("manual_channel", "Ручной ввод ID канала")
        ]
        await context.bot.set_my_commands(commands)


        await update.message.reply_text(
            "<b>🤖 Бот для планирования публикаций</b>\n\n"
            "Используйте команды для управления вашими постами.\n\n"
            "/add_channel - Добавить новый канал (через пересылку)\n"
            "/manual_channel - Добавить канал вручную по ID\n"
            "/test_post - Проверить, работают ли права администратора",
            parse_mode='HTML'
        )

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return
        self.user_states[update.effective_user.id] = 'awaiting_channel_forward'
        await update.message.reply_text(
            "<b>Чтобы добавить канал через пересылку:</b>\n"
            "1. Сделайте этого бота администратором в вашем канале с правом на публикацию сообщений.\n"
            "2. Перешлите сюда любое сообщение из этого канала.\n\n"
            "Если пересылка не сработает (из-за ограничений), используйте <b>/manual_channel</b>."
            , parse_mode='HTML'
        )
    
    async def manual_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для ручного ввода ID канала."""
        if update.effective_user.id not in ADMIN_IDS: return
        self.user_states[update.effective_user.id] = 'awaiting_channel_manual_id'
        await update.message.reply_text(
            "<b>РЕЖИМ РУЧНОГО ВВОДА:</b>\n"
            "Введите числовой ID канала (например, <code>-1001234567890</code>) и его название через запятую.\n\n"
            "<b>Формат:</b> <code>-ID,Название канала</code>\n"
            "<i>(Используйте веб-версию Telegram, чтобы получить ID, если другие методы не работают)</i>",
            parse_mode='HTML'
        )

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("Каналы еще не добавлены. Используйте /add_channel или /manual_channel.")
            return
        
        message = "<b>📋 Подключенные каналы:</b>\n\n"
        # Структура channel: id, channel_id (TG ID), title, username, added_date
        for _, tg_id, title, username, _ in channels:
            message += f"• {title}\n  (ID: <code>{tg_id}</code>, {f'@{username}' if username else 'Без юзернейма'})\n"
        await update.message.reply_text(message, parse_mode='HTML')

    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Позволяет выбрать канал из списка перед вводом текста."""
        if update.effective_user.id not in ADMIN_IDS: return
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал с помощью /add_channel или /manual_channel.")
            return

        # --- ИЗМЕНЕНИЕ: Выводим нумерованный список каналов ---
        message = "<b>Выберите канал для публикации, отправив его номер:</b>\n\n"
        
        # Сохраняем словарь для быстрого поиска по номеру
        context.user_data['available_channels'] = {} 
        
        for i, channel in enumerate(channels, 1):
            db_id, _, title, _, _ = channel
            message += f"<b>{i}.</b> {title}\n"
            # Сохраняем ID канала в БД
            context.user_data['available_channels'][str(i)] = db_id 
        
        self.user_states[update.effective_user.id] = 'awaiting_channel_choice'
        await update.message.reply_text(message, parse_mode='HTML')

    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return
        posts = self.db.get_posts()
        if not posts:
            await update.message.reply_text("📭 Нет запланированных постов.")
            return

        message = "<b>📋 Запланированные посты (по МСК):</b>\n\n"
        for post in posts:
            _, _, message_text, scheduled_time_str, _, _, channel_title, _ = post
            post_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
            time_formatted = MOSCOW_TZ.localize(post_time_naive).strftime('%d.%m.%Y %H:%M')
            text_snippet = message_text[:40].replace('\n', ' ') + ('...' if len(message_text) > 40 else '')
            message += f"• <b>{time_formatted}</b> в '{channel_title}'\n"
            message += f"  <i>Текст: {text_snippet}</i>\n"
        await update.message.reply_text(message, parse_mode='HTML')

    async def test_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверяет, имеет ли бот права на публикацию в первом канале."""
        if update.effective_user.id not in ADMIN_IDS: return

        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Нет подключенных каналов для теста. Добавьте канал первым.")
            return
            
        tg_channel_id = channels[0][1] # TG ID канала
        channel_title = channels[0][2]
        test_message = f"✅ Тестовая публикация от планировщика! Время: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')}"

        await update.message.reply_text(f"Попытка отправить тестовый пост в <b>{channel_title}</b> ({tg_channel_id})...", parse_mode='HTML')
        
        try:
            await context.bot.send_message(chat_id=str(tg_channel_id), text=test_message)
            await update.message.reply_text(f"✅ **УСПЕХ!** Тестовый пост успешно отправлен.", parse_mode='HTML')
        except Exception as e:
            await update.message.reply_text(f"❌ **ОШИБКА!** Не удалось отправить тестовый пост.\n\nКод ошибки: <code>{e}</code>\n\n"
                                          "<b>Вероятная причина:</b> Бот не является Администратором или у него нет права 'Публикация сообщений'.", 
                                          parse_mode='HTML')
                                          
    # --- ОБРАБОТЧИК СООБЩЕНИЙ ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        # Проверяем, что это админ и он находится в процессе диалога
        if user_id not in ADMIN_IDS or user_id not in self.user_states: return

        state = self.user_states[user_id]
        text = update.message.text.strip() # Используем text.strip() для ручного ввода
        
        # 0. ВЫБОР КАНАЛА (НОВЫЙ БЛОК)
        if state == 'awaiting_channel_choice':
            if text in context.user_data.get('available_channels', {}):
                # Получаем ID канала в БД по выбранному номеру
                channel_db_id = context.user_data['available_channels'][text]
                
                # Получаем полное название канала из БД по ID
                channels = self.db.get_channels()
                target_channel = next((c for c in channels if c[0] == channel_db_id), None)
                
                if target_channel:
                    context.user_data['target_channel_id'] = channel_db_id
                    context.user_data['target_channel_title'] = target_channel[2] # Название канала
                    
                    self.user_states[user_id] = 'awaiting_post_text'
                    await update.message.reply_text(
                        f"Отлично! Выбран канал: <b>{target_channel[2]}</b>. "
                        "Пожалуйста, отправьте текст для нового поста.", 
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка: Выбранный канал не найден в базе.")
                del context.user_data['available_channels'] # Очистка
            else:
                await update.message.reply_text("❌ Неверный номер. Пожалуйста, отправьте только номер канала из списка.", parse_mode='HTML')
            return


        # 1. АВТОМАТИЧЕСКАЯ ПРИВЯЗКА (через /add_channel)
        if state == 'awaiting_channel_forward':
            channel = None
            
            # 1. СТАНДАРТНЫЙ ПУТЬ: Пересланное сообщение
            if update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
                channel = update.message.forward_from_chat
                logger.info(f"Channel found via forward_from_chat: {channel.id}")
            
            # 2. АЛЬТЕРНАТИВНЫЙ ПУТЬ: Сообщение от имени чата (для анонимных постов)
            elif update.message.sender_chat and update.message.sender_chat.type == 'channel':
                channel = update.message.sender_chat
                logger.warning(f"Channel found via sender_chat (anonymous/restricted): {channel.id}")

            if channel:
                title = channel.title
                username = channel.username if hasattr(channel, 'username') else None
                tg_channel_id = channel.id

                if self.db.add_channel(tg_channel_id, title, username):
                    await update.message.reply_text(
                        f"✅ Канал '<b>{title}</b>' успешно добавлен через пересылку!\n"
                        f"Telegram ID: <code>{tg_channel_id}</code>", 
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при добавлении канала (БД).")
                del self.user_states[user_id] # Сбрасываем состояние
            else:
                await update.message.reply_text(
                    "❌ Не удалось получить ID через пересылку. Это может быть связано с ограничениями Telegram.\n\n"
                    "Попробуйте ручной ввод: <b>/manual_channel</b>"
                    , parse_mode='HTML'
                )

        # 2. РУЧНАЯ ПРИВЯЗКА (через /manual_channel)
        elif state == 'awaiting_channel_manual_id':
            # Ожидаем формат: -ID,Название канала
            match = re.match(r'^(-?\d+),(.*)$', text)
            
            if match:
                tg_channel_id = int(match.group(1))
                title = match.group(2).strip()
                username = None
                
                # Попытка получить юзернейм и название через API для верификации (опционально)
                try:
                    chat_info = await context.bot.get_chat(chat_id=tg_channel_id)
                    title = chat_info.title
                    username = chat_info.username
                    
                    # Проверка, что это действительно канал (используем только, если chat_info доступно)
                    if chat_info.type not in ['channel', 'supergroup']:
                        await update.message.reply_text("❌ Введенный ID не является ID канала или супергруппы.")
                        return

                except Exception as e:
                    logger.warning(f"Failed to get chat info manually for ID {tg_channel_id}: {e}")
                    # Используем то, что ввел пользователь, если API недоступно

                if self.db.add_channel(tg_channel_id, title, username):
                    await update.message.reply_text(
                        f"✅ Канал '<b>{title}</b>' успешно добавлен вручную!\n"
                        f"Telegram ID: <code>{tg_channel_id}</code>", 
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при добавлении канала (БД).")
                del self.user_states[user_id]
            else:
                await update.message.reply_text(
                    "❌ Неверный формат. Используйте: <code>-ID,Название канала</code>", 
                    parse_mode='HTML'
                )

        # 3. ДОБАВЛЕНИЕ ТЕКСТА ПОСТА
        elif state == 'awaiting_post_text':
            context.user_data['post_text'] = update.message.text
            self.user_states[user_id] = 'awaiting_post_time'
            await update.message.reply_text(
                "Отлично. Теперь укажите время публикации (по МСК).\n\n"
                "<b>Формат:</b> <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>\n"
                "<b>Пример:</b> <code>2025-12-31 18:00</code>",
                parse_mode='HTML'
            )

        # 4. ДОБАВЛЕНИЕ ВРЕМЕНИ ПОСТА
        elif state == 'awaiting_post_time':
            try:
                naive_time = datetime.strptime(update.message.text, '%Y-%m-%d %H:%M')
                aware_time = MOSCOW_TZ.localize(naive_time)

                if aware_time <= datetime.now(MOSCOW_TZ):
                    await update.message.reply_text("❌ Это время уже прошло. Попробуйте снова.")
                    return

                channel_db_id = context.user_data['target_channel_id']
                post_text = context.user_data['post_text']
                
                if self.db.add_post(channel_db_id, post_text, aware_time.strftime('%Y-%m-%d %H:%M:%S')):
                    channel_title = context.user_data['target_channel_title']
                    await update.message.reply_text(
                        f"✅ Пост запланирован в канал <b>{channel_title}</b> на <b>{aware_time.strftime('%d.%m.%Y %H:%M')}</b>.", 
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при планировании поста (БД).")
                
                del self.user_states[user_id]
                context.user_data.clear()

            except (ValueError, TypeError):
                await update.message.reply_text("❌ Неверный формат. Используйте <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>.", parse_mode='HTML')

def main():
    """Запуск бота."""
    application = Application.builder().token(BOT_TOKEN).build()
    bot = SchedulerBot()

    # Добавляем повторяющуюся задачу для проверки постов каждые 10 секунд
    job_queue = application.job_queue
    job_queue.run_repeating(bot.check_posts_job, interval=10, first=5)

    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("status", bot.status))
    application.add_handler(CommandHandler("time", bot.show_time)) # Новая команда
    application.add_handler(CommandHandler("add_channel", bot.add_channel))
    application.add_handler(CommandHandler("manual_channel", bot.manual_channel))
    application.add_handler(CommandHandler("channels", bot.list_channels))
    application.add_handler(CommandHandler("add_post", bot.add_post))
    application.add_handler(CommandHandler("posts", bot.list_posts))
    application.add_handler(CommandHandler("test_post", bot.test_post))
    
    # Регистрируем обработчик текстовых и пересланных сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_handler(MessageHandler(filters.FORWARDED & ~filters.COMMAND, bot.handle_message))

    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()
