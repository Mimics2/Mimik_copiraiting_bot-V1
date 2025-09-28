import logging
import os
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN', '8331986255:AAH6Y0ELNanUc0Ae7gD0qLh3A-tf-cH5V4E')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '6646433980').split(',')]
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path='/tmp/bot.db'):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        with self.get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER UNIQUE,
                    title TEXT,
                    username TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    message_text TEXT,
                    scheduled_time DATETIME,
                    status TEXT DEFAULT 'scheduled',
                    created_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
    
    def add_channel(self, channel_id, title, username):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO channels (channel_id, title, username) VALUES (?, ?, ?)',
                    (channel_id, title, username)
                )
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding channel: {e}")
                return False
    
    def get_channels(self):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM channels')
            return cursor.fetchall()
    
    def add_post(self, channel_id, message_text, scheduled_time):
        with self.get_connection() as conn:
            try:
                cursor = conn.execute(
                    'INSERT INTO posts (channel_id, message_text, scheduled_time) VALUES (?, ?, ?)',
                    (channel_id, message_text, scheduled_time)
                )
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logger.error(f"Error adding post: {e}")
                return False
    
    def get_posts(self):
        with self.get_connection() as conn:
            cursor = conn.execute('''
                SELECT p.*, c.title, c.channel_id as tg_channel_id 
                FROM posts p 
                JOIN channels c ON p.channel_id = c.id 
                WHERE p.status = 'scheduled'
                ORDER BY p.scheduled_time
            ''')
            return cursor.fetchall()
    
    def update_post_status(self, post_id, status):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'UPDATE posts SET status = ? WHERE id = ?',
                    (status, post_id)
                )
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error updating post status: {e}")
                return False

class TelegramBot:
    def __init__(self):
        self.db = Database()
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}
        self.scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    
    async def check_posts(self):
        """Проверка и публикация запланированных постов"""
        try:
            posts = self.db.get_posts()
            current_time = datetime.now(MOSCOW_TZ)
            
            for post in posts:
                post_id, channel_id, message_text, scheduled_time, status, created_date, channel_title, tg_channel_id = post
                
                post_time_naive = datetime.strptime(scheduled_time, '%Y-%m-%d %H:%M:%S')
                post_time = MOSCOW_TZ.localize(post_time_naive)
                
                if post_time <= current_time:
                    await self.publish_post(post_id, tg_channel_id, message_text)
                    
        except Exception as e:
            logger.error(f"Error checking posts: {e}")
    
    async def publish_post(self, post_id, channel_id, message_text):
        """Публикация поста"""
        try:
            # Для публикации нужен bot instance, который будет передан позже
            if hasattr(self, 'bot'):
                await self.bot.send_message(chat_id=channel_id, text=message_text)
                self.db.update_post_status(post_id, 'published')
                logger.info(f"✅ Пост {post_id} опубликован")
        except Exception as e:
            logger.error(f"❌ Ошибка публикации: {e}")
            self.db.update_post_status(post_id, 'error')
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        await update.message.reply_text(f"🤖 Бот запущен! Время: {current_time} (МСК)")
    
    async def time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            return
        
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        await update.message.reply_text(f"⏰ Текущее время: {current_time} (МСК)")

async def main():
    """Основная функция запуска"""
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    bot = TelegramBot()
    
    # Сохраняем ссылку на бота для использования в планировщике
    bot.bot = application.bot
    
    # Настраиваем планировщик для проверки постов каждые 30 секунд
    bot.scheduler.add_job(bot.check_posts, IntervalTrigger(seconds=30))
    bot.scheduler.start()
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("time", bot.time))
    
    logger.info("Бот запущен на Railway")
    
    # Запускаем бота
    await application.run_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
