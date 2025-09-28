import logging
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
from datetime import datetime, timedelta
import re
import psutil
import os

from config import BOT_TOKEN, ADMIN_IDS, MOSCOW_TZ
from database import Database
from scheduler import PostScheduler

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SchedulerBot:
    def __init__(self):
        self.db = Database('scheduler.db')
        self.scheduler = PostScheduler(BOT_TOKEN, 'scheduler.db')
        self.user_states = {}
        self.start_time = datetime.now(MOSCOW_TZ)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа к этому боту")
            return
        
        commands = [
            BotCommand("start", "Запуск бота"),
            BotCommand("status", "Статус бота и время"),
            BotCommand("add_channel", "Добавить канал"),
            BotCommand("channels", "Список каналов"),
            BotCommand("add_post", "Добавить публикацию"),
            BotCommand("posts", "Список публикаций"),
        ]
        
        await context.bot.set_my_commands(commands)
        
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        
        await update.message.reply_text(
            f"📅 **Бот-планировщик публикаций**\n\n"
            f"⏰ Текущее время по Москве: {current_time}\n\n"
            "Доступные команды:\n"
            "/status - Статус бота и время\n"
            "/add_channel - Добавить канал\n"
            "/channels - Список каналов\n"
            "/add_post - Добавить публикацию\n"
            "/posts - Список публикаций"
        )
    
    async def show_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает статус бота, время и статистику"""
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        try:
            # Текущее время по Москве
            current_time = datetime.now(MOSCOW_TZ)
            current_time_str = current_time.strftime('%d.%m.%Y %H:%M:%S')
            
            # Время работы бота
            uptime = current_time - self.start_time
            hours, remainder = divmod(uptime.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{int(hours)}ч {int(minutes)}м {int(seconds)}с"
            
            # Статистика из базы данных
            channels = self.db.get_channels()
            posts = self.db.get_scheduled_posts()
            
            # Количество запланированных публикаций
            scheduled_count = len(posts)
            
            # Ближайшая публикация
            next_post_time = "Нет запланированных"
            if posts:
                next_post = min(posts, key=lambda x: datetime.strptime(x[5], '%Y-%m-%d %H:%M:%S'))
                next_time = datetime.strptime(next_post[5], '%Y-%m-%d %H:%M:%S')
                next_post_time = MOSCOW_TZ.localize(next_time).strftime('%d.%m.%Y %H:%M')
            
            # Статус сообщение
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
    
    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        await update.message.reply_text(
            "📝 **Добавление канала**\n\n"
            "1. Добавьте бота в канал как администратора\n"
            "2. Дайте боту право на публикацию сообщений\n"
            "3. Перешлите любое сообщение из канала в этот чат"
        )
        self.user_states[user_id] = 'awaiting_channel'
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id not in ADMIN_IDS:
            return
        
        # Обработка пересланного сообщения для добавления канала
        if self.user_states.get(user_id) == 'awaiting_channel' and update.message.forward_from_chat:
            channel = update.message.forward_from_chat
            
            if channel.type == 'channel':
                success = self.db.add_channel(
                    channel.id,
                    channel.title,
                    channel.username
                )
                
                if success:
                    await update.message.reply_text(
                        f"✅ Канал **{channel.title}** успешно добавлен!",
                        parse_mode='Markdown'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при добавлении канала")
                
                self.user_states[user_id] = None
                return
        
        # Обработка добавления поста
        if self.user_states.get(user_id) == 'awaiting_post_content':
            context.user_data['post_content'] = update.message.text
            context.user_data['media_type'] = 'text'
            
            current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M')
            await update.message.reply_text(
                f"⏰ **Укажите время публикации по Москве**\n\n"
                f"Сейчас: {current_time} (МСК)\n\n"
                "Формат: `ГГГГ-ММ-ДД ЧЧ:ММ`\n"
                "Пример: `2024-12-25 15:30`",
                parse_mode='Markdown'
            )
            self.user_states[user_id] = 'awaiting_post_time'
            return
        
        if self.user_states.get(user_id) == 'awaiting_post_time':
            try:
                # Парсим введенное время как московское
                naive_time = datetime.strptime(update.message.text, '%Y-%m-%d %H:%M')
                moscow_time = MOSCOW_TZ.localize(naive_time)
                current_moscow_time = datetime.now(MOSCOW_TZ)
                
                if moscow_time <= current_moscow_time:
                    await update.message.reply_text("❌ Время должно быть в будущем!")
                    return
                
                # Получаем данные из контекста
                channel_id = context.user_data.get('selected_channel')
                message_text = context.user_data.get('post_content', '')
                media_type = context.user_data.get('media_type', 'text')
                media_file_id = context.user_data.get('media_file_id', '')
                
                # Сохраняем время в формате для БД
                scheduled_time_str = moscow_time.strftime('%Y-%m-%d %H:%M:%S')
                
                # Добавляем пост в планировщик
                success = self.scheduler.add_new_post(
                    channel_id, message_text, media_type, media_file_id, scheduled_time_str
                )
                
                if success:
                    formatted_time = moscow_time.strftime('%d.%m.%Y %H:%M')
                    await update.message.reply_text(
                        f"✅ Публикация запланирована на {formatted_time} (МСК)"
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при планировании публикации")
                
                # Очищаем состояние
                self.user_states[user_id] = None
                context.user_data.clear()
                
            except ValueError:
                await update.message.reply_text("❌ Неверный формат времени! Используйте: ГГГГ-ММ-ДД ЧЧ:ММ")
    
    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        channels = self.db.get_channels()
        
        if not channels:
            await update.message.reply_text("📭 Каналы не добавлены")
            return
        
        message = "📋 **Список каналов:**\n\n"
        for channel in channels:
            message += f"• {channel[2]} (@{channel[3]})\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        channels = self.db.get_channels()
        
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал через /add_channel")
            return
        
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M')
        await update.message.reply_text(
            f"📝 **Добавление публикации**\n\n"
            f"⏰ Текущее время: {current_time} (МСК)\n\n"
            "Отправьте текст публикации:"
        )
        
        self.user_states[user_id] = 'awaiting_post_content'
        # Сохраняем первый канал (можно улучшить выбор)
        context.user_data['selected_channel'] = channels[0][0]
    
    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет доступа")
            return
        
        posts = self.db.get_scheduled_posts()
        
        if not posts:
            await update.message.reply_text("📭 Нет запланированных публикаций")
            return
        
        current_time = datetime.now(MOSCOW_TZ)
        message = f"📋 **Запланированные публикации** (МСК):\n\n"
        
        for post in posts:
            post_time = datetime.strptime(post[5], '%Y-%m-%d %H:%M:%S')
            moscow_time = MOSCOW_TZ.localize(post_time)
            time_str = moscow_time.strftime('%d.%m.%Y %H:%M')
            message += f"• {time_str} - {post[9]}\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')

def main():
    bot = SchedulerBot()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("status", bot.show_status))
    application.add_handler(CommandHandler("add_channel", bot.add_channel))
    application.add_handler(CommandHandler("channels", bot.list_channels))
    application.add_handler(CommandHandler("add_post", bot.add_post))
    application.add_handler(CommandHandler("posts", bot.list_posts))
    
    # Обработчик сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    
    # Запуск бота
    current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
    print(f"🤖 Бот запущен...")
    print(f"⏰ Текущее время по Москве: {current_time}")
    print(f"📊 Для проверки статуса используйте команду /status")
    application.run_polling()

if __name__ == '__main__':
    main()