import logging
from datetime import datetime, timedelta # Добавил timedelta для статуса
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import BOT_TOKEN, ADMIN_IDS
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
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}  # Словарь для хранения состояний пользователей

    # --- Основная логика планировщика ---
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
            # Преобразуем ID канала в строку для chat_id (это важно для Telegram API)
            await context.bot.send_message(chat_id=str(channel_id), text=message_text, parse_mode='HTML')
            self.db.update_post_status(post_id, 'published')
            logger.info(f"✅ Пост {post_id} успешно опубликован в канал {channel_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка публикации поста {post_id}: {e}")
            self.db.update_post_status(post_id, 'error')

    # --- Обработчики команд ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return

        await update.message.reply_text(
            "<b>🤖 Бот для планирования публикаций</b>\n\n"
            "Используйте команды для управления вашими постами.\n\n"
            "/status - Посмотреть статус бота\n"
            "/add_channel - Добавить новый канал\n"
            "/channels - Список подключенных каналов\n"
            "/add_post - Запланировать новый пост\n"
            "/posts - Посмотреть запланированные посты",
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

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return
        self.user_states[update.effective_user.id] = 'awaiting_channel'
        await update.message.reply_text(
            "<b>Чтобы добавить канал:</b>\n"
            "1. Сделайте этого бота администратором в вашем канале с правом на публикацию сообщений.\n"
            "2. Перешлите сюда любое сообщение из этого канала.",
            parse_mode='HTML'
        )

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("Каналы еще не добавлены. Используйте /add_channel.")
            return
        
        message = "<b>📋 Подключенные каналы:</b>\n\n"
        # Структура channel: id, channel_id (TG ID), title, username, added_date
        for _, tg_id, title, username, _ in channels:
            # Добавил вывод ID для удобства
            message += f"• {title} (ID: <code>{tg_id}</code>, {f'@{username}' if username else 'Без юзернейма'})\n"
        await update.message.reply_text(message, parse_mode='HTML')

    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS: return
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал с помощью /add_channel.")
            return

        # Для простоты постим в первый добавленный канал. 
        # channels[0][0] - это ID канала в БД (primary key), который нужен для add_post
        context.user_data['target_channel_id'] = channels[0][0] 
        context.user_data['target_channel_title'] = channels[0][2] # Сохраняем название
        
        self.user_states[update.effective_user.id] = 'awaiting_post_text'
        await update.message.reply_text(f"Пожалуйста, отправьте текст для нового поста в канал: <b>{channels[0][2]}</b>", parse_mode='HTML')

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

    # --- Обработчик сообщений для многошаговых действий ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        # Проверяем, что это админ и он находится в процессе диалога
        if user_id not in ADMIN_IDS or user_id not in self.user_states: return

        state = self.user_states[user_id]

        if state == 'awaiting_channel':
            channel = None
            
            # 1. СТАНДАРТНЫЙ ПУТЬ: Пересланное сообщение из канала (если нет ограничений)
            if update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
                channel = update.message.forward_from_chat
                logger.info(f"Channel found via forward_from_chat: {channel.id}")
            
            # 2. АЛЬТЕРНАТИВНЫЙ ПУТЬ: Сообщение от имени чата (если оно анонимное/с ограничением)
            elif update.message.sender_chat and update.message.sender_chat.type == 'channel':
                channel = update.message.sender_chat
                logger.warning(f"Channel found via sender_chat (anonymous/restricted): {channel.id}")

            if channel:
                title = channel.title
                username = channel.username if hasattr(channel, 'username') else None
                tg_channel_id = channel.id

                if self.db.add_channel(tg_channel_id, title, username):
                    await update.message.reply_text(
                        f"✅ Канал '<b>{title}</b>' успешно добавлен!\n"
                        f"Telegram ID: <code>{tg_channel_id}</code>", 
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при добавлении канала. Проверьте логи бота (файл database.py).")
                del self.user_states[user_id] # Сбрасываем состояние
            else:
                await update.message.reply_text(
                    "❌ Это не пересланное сообщение из канала или бот не смог определить его ID.\n"
                    "<b>Убедитесь, что:</b>\n"
                    "1. Вы переслали сообщение из КАНАЛА, а не из группы.\n"
                    "2. Бот добавлен в канал как <b>Администратор с правом на публикацию</b> (это критически важно).\n"
                    "3. Временно отключено 'Запретить сохранение контента' (если включено)."
                    , parse_mode='HTML'
                )

        elif state == 'awaiting_post_text':
            context.user_data['post_text'] = update.message.text
            self.user_states[user_id] = 'awaiting_post_time'
            await update.message.reply_text(
                "Отлично. Теперь укажите время публикации (по МСК).\n\n"
                "<b>Формат:</b> <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>\n"
                "<b>Пример:</b> <code>2025-12-31 18:00</code>",
                parse_mode='HTML'
            )

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
                    await update.message.reply_text("❌ Ошибка при планировании поста.")
                
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
    application.add_handler(CommandHandler("add_channel", bot.add_channel))
    application.add_handler(CommandHandler("channels", bot.list_channels))
    application.add_handler(CommandHandler("add_post", bot.add_post))
    application.add_handler(CommandHandler("posts", bot.list_posts))
    
    # Регистрируем обработчик текстовых и пересланных сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_handler(MessageHandler(filters.FORWARDED & ~filters.COMMAND, bot.handle_message))

    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()

