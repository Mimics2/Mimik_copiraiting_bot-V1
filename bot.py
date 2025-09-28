import logging
from datetime import datetime
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
# Используем таймзону из config, если она там есть, иначе по умолчанию (Москва)
try:
    from config import MOSCOW_TZ
except ImportError:
    MOSCOW_TZ = pytz.timezone('Europe/Moscow')

class SchedulerBot:
    def __init__(self):
        # Используем путь к БД из database.py, если он там есть, иначе bot.db
        try:
            self.db = Database('scheduler.db') 
        except Exception:
            self.db = Database('bot.db')

        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}  # Словарь для хранения состояний пользователей

    # --- Основная логика планировщика (пропущена для краткости) ---
    async def check_posts_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Периодически проверяет базу данных на наличие постов для публикации."""
        try:
            posts = self.db.get_posts()
            current_time = datetime.now(MOSCOW_TZ)

            for post in posts:
                post_id, _, message_text, scheduled_time_str, _, _, _, tg_channel_id = post
                
                # Преобразуем время из строки в объект datetime
                post_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
                post_time_aware = MOSCOW_TZ.localize(post_time_naive)

                # Если время пришло
                if post_time_aware <= current_time:
                    try:
                        # Отправка поста
                        await context.bot.send_message(
                            chat_id=tg_channel_id, 
                            text=message_text,
                            parse_mode='HTML' # Предполагаем, что используется HTML-разметка
                        )
                        self.db.update_post_status(post_id, 'published')
                        logger.info(f"Пост {post_id} опубликован в {tg_channel_id}")
                    except Exception as e:
                        logger.error(f"Не удалось опубликовать пост {post_id} в {tg_channel_id}: {e}")
                        self.db.update_post_status(post_id, 'error')

        except Exception as e:
            logger.error(f"Ошибка в задаче check_posts_job: {e}")


    # --- Обработчики команд ---

    async def _check_admin(self, update: Update) -> bool:
        """Проверка прав администратора."""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа к этой команде.")
            return False
        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_admin(update): return
        
        message = (
            "🤖 **Бот-планировщик запущен.**\n\n"
            "Доступные команды:\n"
            "• `/add_channel` - Привязать канал.\n"
            "• `/add_post` - Запланировать пост.\n"
            "• `/channels` - Список привязанных каналов.\n"
            "• `/posts` - Список запланированных постов.\n"
            "• `/status` - Текущий статус бота."
        )
        await update.message.reply_text(message, parse_mode='Markdown')

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_admin(update): return

        uptime = datetime.now(MOSCOW_TZ) - self.start_time
        channels_count = len(self.db.get_channels())
        posts_count = len(self.db.get_posts())

        message = (
            "✅ **Статус бота:** Работает\n"
            f"🕰️ **Время запуска (МСК):** {self.start_time.strftime('%d.%m.%Y %H:%M:%S')}\n"
            f"⏳ **Время работы:** {str(timedelta(seconds=int(uptime.total_seconds())))}\n"
            f"📊 **Статистика БД:**\n"
            f"  • Каналов привязано: {channels_count}\n"
            f"  • Постов в очереди: {posts_count}"
        )
        await update.message.reply_text(message, parse_mode='Markdown')

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_admin(update): return
        
        user_id = update.effective_user.id
        self.user_states[user_id] = 'awaiting_channel_forward'
        await update.message.reply_text("Отлично, теперь **перешлите любое сообщение** из канала, который хотите привязать.")

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_admin(update): return

        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("🤷‍♂️ Пока нет привязанных каналов.")
            return

        message = "📋 **Привязанные каналы:**\n\n"
        for idx, channel in enumerate(channels, 1):
            title = channel[2]
            username = channel[3] or "Нет юзернейма"
            tg_id = channel[1] # telegram_channel_id
            
            message += f"**{idx}. {title}**\n"
            message += f"ID: `{tg_id}`\n"
            if channel[3]:
                message += f"@{username}\n"
            message += "—\n"

        await update.message.reply_text(message, parse_mode='Markdown')

    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_admin(update): return
        
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Сначала привяжите канал командой `/add_channel`.")
            return

        user_id = update.effective_user.id
        self.user_states[user_id] = 'awaiting_post_details'
        context.user_data['channels'] = channels
        
        # Шаг 1: Выбор канала
        channel_list = "\n".join([
            f"**{i}.** {c[2]} (`{c[1]}`)" for i, c in enumerate(channels, 1)
        ])
        
        message = (
            "📝 **Шаг 1/3: Выберите канал**\n\n"
            "Введите **номер** канала из списка, куда нужно опубликовать пост:\n\n"
            f"{channel_list}"
        )
        await update.message.reply_text(message, parse_mode='Markdown')

    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._check_admin(update): return
        
        posts = self.db.get_posts() # Получает только scheduled
        if not posts:
            await update.message.reply_text("🤷‍♂️ В очереди нет запланированных постов.")
            return
        
        message = f"📋 **Запланированные публикации** (МСК):\n\n"
        
        for post in posts:
            post_time = datetime.strptime(post[3], '%Y-%m-%d %H:%M:%S')
            moscow_time = MOSCOW_TZ.localize(post_time)
            time_str = moscow_time.strftime('%d.%m.%Y %H:%M')
            channel_title = post[6]
            
            # Укорачиваем текст для списка
            text_snippet = post[2][:50].replace('\n', ' ') + ('...' if len(post[2]) > 50 else '')
            
            message += f"• **{time_str}** в **{channel_title}**\n"
            message += f"  Текст: _{text_snippet}_\n"
            message += "—\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')

    # --- Обработчик сообщений ---

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user_state = self.user_states.get(user_id)
        
        # Логика привязки канала
        if user_state == 'awaiting_channel_forward':
            # --- ЛОГИКА ДИАГНОСТИКИ И ИСПРАВЛЕНИЯ ПРОБЛЕМЫ ---
            
            # Проверяем, переслано ли сообщение
            is_forwarded = bool(update.message.forward_from_chat)
            
            # Проверяем, не является ли сообщение от имени чата (для постов в группах)
            is_from_sender_chat = bool(update.message.sender_chat)

            logger.info(f"Forwarded check: is_forwarded={is_forwarded}")

            if is_forwarded:
                # 1. Основной путь: пересланное сообщение из канала
                channel = update.message.forward_from_chat
                logger.info(f"Forwarded chat info: type={channel.type}, title={channel.title}, id={channel.id}")

                if channel.type == 'channel':
                    title = channel.title
                    username = channel.username if channel.username else None
                    tg_channel_id = channel.id
                    
                    if self.db.add_channel(tg_channel_id, title, username):
                        await update.message.reply_text(
                            f"✅ Канал **{title}** привязан успешно!\n"
                            f"ID: `{tg_channel_id}`",
                            parse_mode='Markdown'
                        )
                    else:
                        await update.message.reply_text("❌ **Ошибка при добавлении канала.** Проверьте логи бота.")
                    
                    del self.user_states[user_id]
                    return
                else:
                    await update.message.reply_text(
                        "❌ Пересланное сообщение не из **канала** (тип: `{}`). Убедитесь, что вы пересылаете пост из самого канала, а не из связанной группы обсуждения.".format(channel.type),
                        parse_mode='Markdown'
                    )
                    return
            
            elif is_from_sender_chat and update.message.sender_chat.type in ('channel', 'supergroup'):
                # 2. Альтернативный путь: если сообщение пришло от имени чата (например, анонимный админ), но не как форвард
                channel = update.message.sender_chat
                logger.warning(f"Using sender_chat: type={channel.type}, title={channel.title}, id={channel.id}")

                await update.message.reply_text(
                    "❌ Не удалось получить ID через этот тип сообщения. **Пожалуйста, перешлите** сообщение из канала, предварительно **отключив в канале** настройку 'Запретить сохранение контента'."
                )
                # Оставляем состояние, чтобы пользователь мог попробовать еще раз
                return


            await update.message.reply_text(
                "❌ **Это не пересланное сообщение из канала.** Попробуйте еще раз.\n\n"
                "**Возможные причины:**\n"
                "1. В канале включено 'Запретить сохранение контента'. **Отключите** ее временно.\n"
                "2. Вы не отправили команду `/add_channel` перед пересылкой.\n"
                "3. Вы переслали сообщение из *группы*, а не из *канала*."
            )
            return

        # Логика добавления поста (Шаг 1: Выбор канала)
        if user_state == 'awaiting_post_details':
            try:
                channel_number = int(update.message.text.strip())
                channels = context.user_data.get('channels', [])
                
                if 1 <= channel_number <= len(channels):
                    selected_channel = channels[channel_number - 1]
                    context.user_data['selected_channel'] = selected_channel
                    self.user_states[user_id] = 'awaiting_post_text'
                    
                    await update.message.reply_text("📝 **Шаг 2/3: Введите текст поста.**\n\nМожно использовать HTML-разметку.")
                else:
                    await update.message.reply_text("❌ Неверный номер канала. Повторите ввод.")

            except ValueError:
                await update.message.reply_text("❌ Ожидается ввод **номера**, а не текста. Повторите ввод.")
            return

        # Логика добавления поста (Шаг 2: Ввод текста)
        if user_state == 'awaiting_post_text':
            post_text = update.message.text
            context.user_data['post_text'] = post_text
            self.user_states[user_id] = 'awaiting_schedule_time'
            
            await update.message.reply_text(
                "⏰ **Шаг 3/3: Введите время публикации** (в Московском времени).\n\n"
                "Формат: `ГГГГ-ММ-ДД ЧЧ:ММ` (например, `2025-10-20 18:30`)"
            )
            return

        # Логика добавления поста (Шаг 3: Ввод времени)
        if user_state == 'awaiting_schedule_time':
            schedule_time_str = update.message.text.strip()
            
            try:
                # Парсинг и проверка времени
                naive_time = datetime.strptime(schedule_time_str, '%Y-%m-%d %H:%M')
                scheduled_time_aware = MOSCOW_TZ.localize(naive_time)
                
                # Проверка, что время в будущем
                if scheduled_time_aware <= datetime.now(MOSCOW_TZ):
                    await update.message.reply_text("❌ Выбранное время уже прошло. Укажите время в будущем.")
                    return
                
                channel_db_id = context.user_data['selected_channel'][0]
                post_text = context.user_data['post_text']

                # Сохранение в БД
                if self.db.add_post(channel_db_id, post_text, naive_time.strftime('%Y-%m-%d %H:%M:%S')):
                    channel_title = context.user_data['selected_channel'][2]
                    await update.message.reply_text(
                        f"✅ Пост успешно запланирован!\n\n"
                        f"**Канал:** {channel_title}\n"
                        f"**Время (МСК):** {scheduled_time_aware.strftime('%d.%m.%Y %H:%M')}",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при планировании поста. Проверьте логи.")
                
                # Сброс состояния и данных
                del self.user_states[user_id]
                context.user_data.clear()

            except (ValueError, TypeError):
                await update.message.reply_text("❌ Неверный формат. Используйте `ГГГГ-ММ-ДД ЧЧ:ММ`.", parse_mode='HTML')


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

    logger.info("Бот запущен. Ожидание обновлений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

