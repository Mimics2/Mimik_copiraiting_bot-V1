import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import pytz

from config import BOT_TOKEN, ADMIN_IDS
from database import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOSCOW_TZ = pytz.timezone('Europe/Moscow')

class SchedulerBot:
    def __init__(self):
        self.db = Database()
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}
    
    async def check_posts_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Задача для проверки и публикации постов"""
        try:
            posts = self.db.get_posts()
            current_time = datetime.now(MOSCOW_TZ)
            
            for post in posts:
                post_id, channel_id, message_text, scheduled_time, status, created_date, channel_title, tg_channel_id = post
                
                # Преобразуем время из базы в московское время
                post_time_naive = datetime.strptime(scheduled_time, '%Y-%m-%d %H:%M:%S')
                post_time = MOSCOW_TZ.localize(post_time_naive)
                
                # Если время пришло, публикуем
                if post_time <= current_time:
                    logger.info(f"Публикую пост {post_id} в канал {tg_channel_id}")
                    await self.publish_post(post_id, tg_channel_id, message_text, context)
                    
        except Exception as e:
            logger.error(f"Ошибка при проверке постов: {e}")
    
    async def publish_post(self, post_id, channel_id, message_text, context: ContextTypes.DEFAULT_TYPE):
        """Публикует пост в канал"""
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=message_text
            )
            # Обновляем статус на "опубликовано"
            self.db.update_post_status(post_id, 'published')
            logger.info(f"✅ Пост {post_id} успешно опубликован в канал {channel_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка публикации поста {post_id}: {e}")
            self.db.update_post_status(post_id, 'error')
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа к этому боту")
            return
        
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        posts = self.db.get_posts()
        
        await update.message.reply_text(
            f"🤖 <b>Бот-планировщик публикаций</b>\n\n"
            f"⏰ <b>Текущее время:</b> {current_time} (МСК)\n"
            f"📊 <b>В очереди:</b> {len(posts)} публикаций\n\n"
            "<b>Команды:</b>\n"
            "/status - Статус\n"
            "/time - Время\n"
            "/add_channel - Добавить канал\n"
            "/add_post - Добавить публикацию\n"
            "/posts - Список публикаций",
            parse_mode='HTML'
        )
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        current_time = datetime.now(MOSCOW_TZ)
        uptime = current_time - self.start_time
        hours, remainder = divmod(uptime.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        channels = self.db.get_channels()
        posts = self.db.get_posts()
        
        # Ближайшая публикация
        next_post = "Нет"
        if posts:
            next_post_time = min([datetime.strptime(p[3], '%Y-%m-%d %H:%M:%S') for p in posts])
            next_post = MOSCOW_TZ.localize(next_post_time).strftime('%d.%m.%Y %H:%M')
        
        message = (
            f"🤖 <b>СТАТУС БОТА</b>\n\n"
            f"⏰ <b>Время:</b> {current_time.strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n"
            f"🕐 <b>Работает:</b> {int(hours)}ч {int(minutes)}м\n"
            f"📊 <b>Каналов:</b> {len(channels)}\n"
            f"📅 <b>В очереди:</b> {len(posts)} публикаций\n"
            f"⏱ <b>Ближайшая:</b> {next_post}\n"
            f"🟢 <b>Статус:</b> Активен"
        )
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def show_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        current_time = datetime.now(MOSCOW_TZ)
        await update.message.reply_text(
            f"⏰ <b>Московское время:</b>\n{current_time.strftime('%d.%m.%Y %H:%M:%S')}",
            parse_mode='HTML'
        )
    
    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        await update.message.reply_text(
            "📝 <b>Добавление канала</b>\n\n"
            "1. Добавьте бота в канал как администратора\n"
            "2. Дайте боту право на публикацию сообщений\n"
            "3. Перешлите любое сообщение из канала в этот чат",
            parse_mode='HTML'
        )
        self.user_states[user_id] = 'awaiting_channel'
    
    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        channels = self.db.get_channels()
        
        if not channels:
            await update.message.reply_text("📭 Каналы не добавлены")
            return
        
        message = "📋 <b>Список каналов:</b>\n\n"
        for channel in channels:
            message += f"• {channel[2]} (@{channel[3]})\n"
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        channels = self.db.get_channels()
        
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал через /add_channel")
            return
        
        await update.message.reply_text(
            "📝 <b>Добавление публикации</b>\n\n"
            "Отправьте текст публикации:",
            parse_mode='HTML'
        )
        self.user_states[user_id] = 'awaiting_post_text'
        context.user_data['channel_id'] = channels[0][0]  # Используем первый канал
    
    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        posts = self.db.get_posts()
        
        if not posts:
            await update.message.reply_text("📭 Нет запланированных публикаций")
            return
        
        message = "📋 <b>Запланированные публикации:</b>\n\n"
        for post in posts:
            post_time = datetime.strptime(post[3], '%Y-%m-%d %H:%M:%S')
            moscow_time = MOSCOW_TZ.localize(post_time)
            time_str = moscow_time.strftime('%d.%m.%Y %H:%M')
            message += f"• {time_str} - {post[6]}\n"
        
        await update.message.reply_text(message, parse_mode='HTML')
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            return
        
        # Обработка добавления канала
        if self.user_states.get(user_id) == 'awaiting_channel' and update.message.forward_from_chat:
            channel = update.message.forward_from_chat
            
            if channel.type == 'channel':
                success = self.db.add_channel(channel.id, channel.title, channel.username)
                
                if success:
                    await update.message.reply_text(f"✅ Канал <b>{channel.title}</b> добавлен!", parse_mode='HTML')
                else:
                    await update.message.reply_text("❌ Ошибка при добавлении канала")
                
                self.user_states[user_id] = None
                return
        
        # Обработка добавления поста
        if self.user_states.get(user_id) == 'awaiting_post_text':
            context.user_data['post_text'] = update.message.text
            
            await update.message.reply_text(
                "⏰ <b>Укажите время публикации</b>\n\n"
                "Формат: <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>\n"
                "Пример: <code>2024-12-25 15:30</code>",
                parse_mode='HTML'
            )
            self.user_states[user_id] = 'awaiting_post_time'
            return
        
        if self.user_states.get(user_id) == 'awaiting_post_time':
            try:
                # Парсим время
                naive_time = datetime.strptime(update.message.text, '%Y-%m-%d %H:%M')
                moscow_time = MOSCOW_TZ.localize(naive_time)
                current_time = datetime.now(MOSCOW_TZ)
                
                if moscow_time <= current_time:
                    await update.message.reply_text("❌ Время должно быть в будущем!")
                    return
                
                # Добавляем пост в базу
                channel_id = context.user_data.get('channel_id')
                post_text = context.user_data.get('post_text', '')
                
                success = self.db.add_post(channel_id, post_text, moscow_time.strftime('%Y-%m-%d %H:%M:%S'))
                
                if success:
                    await update.message.reply_text(
                        f"✅ Публикация запланирована на {moscow_time.strftime('%d.%m.%Y %H:%M')} (МСК)\n\n"
                        f"Текст: {post_text[:100]}..."
                    )
                    logger.info(f"Добавлена новая публикация на {moscow_time}")
                else:
                    await update.message.reply_text("❌ Ошибка при планировании")
                
                # Очищаем состояние
                self.user_states[user_id] = None
                context.user_data.clear()
                
            except ValueError:
                await update.message.reply_text("❌ Неверный формат времени! Используйте: ГГГГ-ММ-ДД ЧЧ:ММ")

def main():
    # Создаем приложение с JobQueue
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Создаем экземпляр бота
    bot = SchedulerBot()
    
    # Настраиваем периодическую задачу для проверки постов
    job_queue = application.job_queue
    job_queue.run_repeating(bot.check_posts_job, interval=10, first=1)  # Проверка каждые 10 секунд
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("status", bot.status))
    application.add_handler(CommandHandler("time", bot.show_time))
    application.add_handler(CommandHandler("add_channel", bot.add_channel))
    application.add_handler(CommandHandler("channels", bot.list_channels))
    application.add_handler(CommandHandler("add_post", bot.add_post))
    application.add_handler(CommandHandler("posts", bot.list_posts))
    
    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    
    print("🤖 Бот запущен с JobQueue!")
    print("⏰ Проверка публикаций каждые 10 секунд")
    print("📊 Для проверки используйте /status")
    
    application.run_polling()

if __name__ == '__main__':
    main()
