import logging
from datetime import datetime, timedelta
import pytz
import re 
from telegram import Update, BotCommand, ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import BOT_TOKEN, ADMIN_IDS
from database import Database

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
        self.db = Database() 
        # Инициализация: Добавляем ID из config.py в базу данных как основных админов
        for admin_id in ADMIN_IDS:
            self.db.add_admin(admin_id, username="Initial_Config_Admin")
            
        self.start_time = datetime.now(MOSCOW_TZ)
        self.user_states = {}  # Словарь для хранения состояний пользователей
        self.admin_ids = self.db.get_admin_ids() # Кешируем ID администраторов

    # --- ПРОВЕРКА АДМИНА (Используется везде) ---
    def is_user_admin(self, user_id):
        # Обновляем список админов при каждой проверке
        self.admin_ids = self.db.get_admin_ids()
        return user_id in self.admin_ids

    # --- ФУНКЦИИ ПЛАНИРОВАНИЯ ---
    async def check_posts_job(self, context: ContextTypes.DEFAULT_TYPE):
        """Периодически проверяет базу данных на наличие постов для публикации."""
        try:
            posts = self.db.get_posts()
            current_time = datetime.now(MOSCOW_TZ)

            # post: 0-id, 1-channel_db_id, 2-message_text, 3-scheduled_time_str, 4-status, 5-created_date, 6-media_file_id, 7-media_type, 8-channel_title, 9-tg_channel_id

            for post in posts:
                # Извлекаем нужные данные по индексу
                post_id, _, message_text, scheduled_time_str, _, _, media_file_id, media_type, _, tg_channel_id = post
                
                # Преобразуем время из строки в объект datetime
                post_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
                post_time_aware = MOSCOW_TZ.localize(post_time_naive)

                # Если время публикации настало, отправляем пост
                if post_time_aware <= current_time:
                    logger.info(f"Публикую пост {post_id} ({media_type or 'текст'}) в канал {tg_channel_id}")
                    await self.publish_post(post_id, tg_channel_id, message_text, media_file_id, media_type, context)
        except Exception as e:
            logger.error(f"Ошибка в задаче проверки постов: {e}")

    async def publish_post(self, post_id, channel_id, message_text, media_file_id, media_type, context: ContextTypes.DEFAULT_TYPE):
        """Отправляет сообщение (с медиа или без) в канал и обновляет статус."""
        try:
            tg_channel_id_str = str(channel_id)
            
            # Telegram API требует использовать caption для текста с медиа
            caption_text = message_text if media_file_id else None
            
            if media_type == 'photo':
                await context.bot.send_photo(
                    chat_id=tg_channel_id_str,
                    photo=media_file_id,
                    caption=caption_text,
                    parse_mode='HTML'
                )
            elif media_type == 'video':
                await context.bot.send_video(
                    chat_id=tg_channel_id_str,
                    video=media_file_id,
                    caption=caption_text,
                    parse_mode='HTML'
                )
            else: # Только текст
                await context.bot.send_message(
                    chat_id=tg_channel_id_str, 
                    text=message_text, 
                    parse_mode='HTML'
                )

            self.db.update_post_status(post_id, 'published')
            logger.info(f"✅ Пост {post_id} ({media_type or 'текст'}) успешно опубликован в канал {channel_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка публикации поста {post_id} в канал {channel_id}: {e}")
            self.db.update_post_status(post_id, 'error')


    # --- КОМАНДЫ СТАТУСА И ИНФОРМАЦИИ ---
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): 
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return

        commands = [
            BotCommand("status", "Посмотреть статус бота"),
            BotCommand("time", "Показать текущее время (МСК)"),
            BotCommand("add_channel", "Добавить новый канал"),
            BotCommand("manual_channel", "Ручной ввод ID канала"),
            BotCommand("set_prompt", "Установить промпт ИИ для канала"),
            BotCommand("add_admin", "Добавить нового администратора"),
            BotCommand("channels", "Список подключенных каналов"),
            BotCommand("admins", "Список администраторов"),
            BotCommand("add_post", "Запланировать новый пост (с фото/видео)"),
            BotCommand("posts", "Список запланированных постов"),
            BotCommand("test_post", "Проверить публикацию"),
        ]
        await context.bot.set_my_commands(commands)

        await update.message.reply_text(
            "<b>🤖 Бот для планирования публикаций</b>\n\n"
            "Используйте команды для управления:\n"
            "/add_post - Планирование поста\n"
            "/set_prompt - Настройка ИИ инструкции\n"
            "/add_admin - Добавление нового админа",
            parse_mode='HTML'
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return

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

    async def show_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        current_time = datetime.now(MOSCOW_TZ).strftime('%d.%m.%Y %H:%M:%S')
        await update.message.reply_text(
            f"Текущее время в Москве (МСК): \n<b>{current_time}</b>",
            parse_mode='HTML'
        )

    async def test_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return

        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Нет подключенных каналов для теста.")
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


    # --- КОМАНДЫ УПРАВЛЕНИЯ КАНАЛАМИ И КОНТЕНТОМ ---

    async def add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        self.user_states[update.effective_user.id] = 'awaiting_channel_forward'
        await update.message.reply_text(
            "<b>Чтобы добавить канал через пересылку:</b>\n"
            "1. Сделайте этого бота администратором в вашем канале с правом на публикацию сообщений.\n"
            "2. Перешлите сюда любое сообщение из этого канала.\n\n"
            "Или используйте <b>/manual_channel</b> для ручного ввода."
            , parse_mode='HTML'
        )
    
    async def manual_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        self.user_states[update.effective_user.id] = 'awaiting_channel_manual_id'
        await update.message.reply_text(
            "<b>РЕЖИМ РУЧНОГО ВВОДА:</b>\n"
            "Введите числовой ID канала (например, <code>-1001234567890</code>) и его название через запятую.\n\n"
            "<b>Формат:</b> <code>-ID,Название канала</code>",
            parse_mode='HTML'
        )

    async def list_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("Каналы еще не добавлены. Используйте /add_channel.")
            return
        
        message = "<b>📋 Подключенные каналы:</b>\n\n"
        # Структура channel: 0-id (DB), 1-channel_id (TG), 2-title, 3-username, 4-added_date, 5-default_prompt
        for db_id, tg_id, title, username, _, default_prompt in channels:
            prompt_status = "✅ Есть промпт" if default_prompt else "❌ Нет промпта"
            message += f"• {title} ({prompt_status})\n"
            message += f"  (ID: <code>{tg_id}</code>, Внутр. ID: <code>{db_id}</code>)\n"
        await update.message.reply_text(message, parse_mode='HTML')

    async def add_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        
        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Сначала добавьте канал с помощью /add_channel.")
            return

        # 1. Показываем список каналов для выбора
        message = "<b>Выберите канал для публикации:</b>\n\n"
        # channel: 0-id (DB), 1-channel_id (TG), 2-title
        for db_id, _, title, _, _, _ in channels:
            message += f"• <b>{title}</b> (Внутр. ID: <code>{db_id}</code>)\n"
        
        message += "\nВведите **внутренний ID** канала (число в скобках)."
        
        self.user_states[update.effective_user.id] = 'awaiting_target_channel_id'
        await update.message.reply_text(message, parse_mode='HTML')


    async def list_posts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return
        posts = self.db.get_posts()
        if not posts:
            await update.message.reply_text("📭 Нет запланированных постов.")
            return

        message = "<b>📋 Запланированные посты (по МСК):</b>\n\n"
        # post: 0-id, 1-channel_db_id, 2-message_text, 3-scheduled_time_str, 7-media_type, 8-channel_title
        for post in posts:
            post_id, _, message_text, scheduled_time_str, _, _, _, media_type, channel_title, _ = post
            post_time_naive = datetime.strptime(scheduled_time_str, '%Y-%m-%d %H:%M:%S')
            time_formatted = MOSCOW_TZ.localize(post_time_naive).strftime('%d.%m.%Y %H:%M')
            
            media_info = f" ({media_type.upper()})" if media_type else ""
            
            text_snippet = message_text[:40].replace('\n', ' ') + ('...' if len(message_text) > 40 else '')
            message += f"• <b>{time_formatted}</b>{media_info} в '{channel_title}'\n"
            message += f"  <i>Текст: {text_snippet}</i>\n"
        await update.message.reply_text(message, parse_mode='HTML')


    # --- КОМАНДЫ УПРАВЛЕНИЯ АДМИНАМИ И ПРОМПТАМИ ---

    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало диалога добавления администратора."""
        # Проверяем, является ли пользователь администратором из БД
        if not self.is_user_admin(update.effective_user.id): return 
        
        self.user_states[update.effective_user.id] = 'awaiting_new_admin_id'
        await update.message.reply_text(
            "<b>Введите ID нового администратора (число)</b>. \n\n"
            "Чтобы узнать ID, пользователь должен отправить команду /myid любому публичному боту.",
            parse_mode='HTML'
        )

    async def list_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список всех администраторов."""
        if not self.is_user_admin(update.effective_user.id): return
        
        admins = self.db.get_admins()
        if not admins:
            await update.message.reply_text("Нет активных администраторов.")
            return
            
        message = "<b>👥 Список администраторов:</b>\n\n"
        # admin: 0-id, 1-user_id, 2-username, 3-added_date
        for _, user_id, username, _ in admins:
            status = " (Главный)" if user_id in ADMIN_IDS else ""
            message += f"• <code>{user_id}</code>: {username or 'Нет юзернейма'}{status}\n"
        
        await update.message.reply_text(message, parse_mode='HTML')


    async def set_prompt_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_user_admin(update.effective_user.id): return

        channels = self.db.get_channels()
        if not channels:
            await update.message.reply_text("❌ Нет подключенных каналов. Сначала добавьте канал!")
            return

        message = "<b>📝 Введите Telegram ID канала (включая -100...), для которого вы хотите установить инструкцию (промпт):</b>\n\n"
        # channel: 0-id, 1-channel_id (TG), 2-title, 5-default_prompt
        for _, tg_id, title, _, _, default_prompt in channels:
            status = "✅ Установлен" if default_prompt else "❌ Не установлен"
            message += f"• <b>{title}</b> (ID: <code>{tg_id}</code>) - {status}\n"
        
        self.user_states[update.effective_user.id] = 'awaiting_prompt_channel_id'
        await update.message.reply_text(message, parse_mode='HTML')


    # --- ОБРАБОТЧИК СООБЩЕНИЙ (HANDLE_MESSAGE) ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self.is_user_admin(user_id) or user_id not in self.user_states: return

        state = self.user_states[user_id]
        
        # 1. АВТОМАТИЧЕСКАЯ ПРИВЯЗКА (/add_channel)
        if state == 'awaiting_channel_forward':
            channel = None
            if update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
                channel = update.message.forward_from_chat
            elif update.message.sender_chat and update.message.sender_chat.type == 'channel':
                channel = update.message.sender_chat

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
                del self.user_states[user_id] 
            else:
                await update.message.reply_text(
                    "❌ Не удалось получить ID через пересылку. Попробуйте ручной ввод: <b>/manual_channel</b>"
                    , parse_mode='HTML'
                )

        # 2. РУЧНАЯ ПРИВЯЗКА (/manual_channel)
        elif state == 'awaiting_channel_manual_id':
            text = update.message.text.strip()
            match = re.match(r'^(-?\d+),(.*)$', text)
            
            if match:
                tg_channel_id = int(match.group(1))
                title = match.group(2).strip()
                username = None
                
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
        
        # 3. ДОБАВЛЕНИЕ НОВОГО АДМИНИСТРАТОРА (/add_admin)
        elif state == 'awaiting_new_admin_id':
            try:
                new_admin_id = int(update.message.text.strip())
                
                # Попытка получить юзернейм для удобства
                try:
                    chat_info = await context.bot.get_chat(new_admin_id)
                    username = chat_info.username or chat_info.full_name
                except Exception:
                    username = f"Пользователь ID {new_admin_id}"
                
                if self.db.add_admin(new_admin_id, username):
                    await update.message.reply_text(
                        f"✅ Пользователь <b>{username}</b> (ID: <code>{new_admin_id}</code>) успешно добавлен как администратор!",
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка базы данных или админ уже существует.")
                
                del self.user_states[user_id]
                
            except ValueError:
                await update.message.reply_text("❌ ID должен быть целым числом. Попробуйте снова.")
                return

        # 4. ОЖИДАНИЕ ВЫБОРА КАНАЛА ДЛЯ ПОСТА (/add_post)
        elif state == 'awaiting_target_channel_id':
            try:
                db_id = int(update.message.text.strip())
                # Используем новый метод для получения канала по внутреннему ID
                channel_info = self.db.get_channel_info_by_db_id(db_id) 
                
                if not channel_info:
                    await update.message.reply_text("❌ Канал с таким внутренним ID не найден. Попробуйте снова.")
                    return
                
                context.user_data['target_channel_id'] = db_id # Внутренний ID БД
                context.user_data['target_channel_title'] = channel_info[2]
                
                self.user_states[user_id] = 'awaiting_post_text'
                await update.message.reply_text(
                    f"Выбран канал <b>{channel_info[2]}</b>. Теперь отправьте **фото, видео или текст** для нового поста.\n\n"
                    "<i>(Текст для медиафайлов укажите в подписи!)</i>", 
                    parse_mode='HTML'
                )

            except ValueError:
                await update.message.reply_text("❌ ID должен быть числом. Попробуйте снова.")
                return

        
        # 5. ДОБАВЛЕНИЕ ТЕКСТА / МЕДИА ПОСТА (Продолжение /add_post)
        elif state == 'awaiting_post_text': 
            
            media_id = None
            media_type = None
            text = ""

            if update.message.photo:
                media_id = update.message.photo[-1].file_id
                media_type = 'photo'
                text = update.message.caption or ""
            
            elif update.message.video:
                media_id = update.message.video.file_id
                media_type = 'video'
                text = update.message.caption or ""

            elif update.message.text:
                text = update.message.text
                
            else:
                await update.message.reply_text("❌ Пришлите фото, видео или просто текст. Другой тип медиа не поддерживается.")
                return

            if not text and not media_id:
                await update.message.reply_text("❌ Пост не может быть пустым. Пришлите текст или медиафайл с подписью.")
                return

            # Сохраняем данные для следующего шага
            context.user_data['post_text'] = text
            context.user_data['media_file_id'] = media_id
            context.user_data['media_type'] = media_type

            self.user_states[user_id] = 'awaiting_post_time'
            
            media_status = f"✅ Медиафайл ({media_type}) принят." if media_id else "✅ Текст принят."
            await update.message.reply_text(
                f"{media_status}\nТеперь укажите время публикации (по МСК).\n\n"
                "<b>Формат:</b> <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>\n"
                "<b>Пример:</b> <code>2025-12-31 18:00</code>",
                parse_mode='HTML'
            )


        # 6. ДОБАВЛЕНИЕ ВРЕМЕНИ ПОСТА
        elif state == 'awaiting_post_time':
            try:
                naive_time = datetime.strptime(update.message.text, '%Y-%m-%d %H:%M')
                aware_time = MOSCOW_TZ.localize(naive_time)

                if aware_time <= datetime.now(MOSCOW_TZ):
                    await update.message.reply_text("❌ Это время уже прошло. Попробуйте снова.")
                    return

                channel_db_id = context.user_data['target_channel_id']
                post_text = context.user_data['post_text']
                media_file_id = context.user_data.get('media_file_id')
                media_type = context.user_data.get('media_type')
                
                # Добавление поста с медиа данными
                if self.db.add_post(channel_db_id, post_text, aware_time.strftime('%Y-%m-%d %H:%M:%S'), media_file_id, media_type):
                    channel_title = context.user_data['target_channel_title']
                    media_info = f" ({media_type.upper()})" if media_type else ""
                    await update.message.reply_text(
                        f"✅ Пост{media_info} запланирован в канал <b>{channel_title}</b> на <b>{aware_time.strftime('%d.%m.%Y %H:%M')}</b>.", 
                        parse_mode='HTML'
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при планировании поста (БД).")
                
                del self.user_states[user_id]
                context.user_data.clear()

            except (ValueError, TypeError):
                await update.message.reply_text("❌ Неверный формат. Используйте <code>ГГГГ-ММ-ДД ЧЧ:ММ</code>.", parse_mode='HTML')


        # 7. ОЖИДАНИЕ ID КАНАЛА ДЛЯ УСТАНОВКИ ПРОМПТА
        elif state == 'awaiting_prompt_channel_id':
            try:
                tg_id = int(update.message.text.strip())
                channel_info = self.db.get_channel_info(tg_id)
                
                if not channel_info:
                    await update.message.reply_text("❌ Канал с таким ID не найден. Попробуйте снова.")
                    return

                context.user_data['prompt_target_id'] = tg_id
                self.user_states[user_id] = 'awaiting_new_prompt_text'
                
                await update.message.reply_text(
                    f"Отлично! Вы выбрали канал <b>{channel_info[2]}</b>.\n\n"
                    "Теперь отправьте **полный промпт (инструкцию)** для нейросети.",
                    parse_mode='HTML'
                )

            except ValueError:
                await update.message.reply_text("❌ ID должен быть числом. Попробуйте снова.")
                return

        # 8. ОЖИДАНИЕ ТЕКСТА ПРОМПТА
        elif state == 'awaiting_new_prompt_text':
            tg_id = context.user_data.get('prompt_target_id')
            new_prompt = update.message.text

            if self.db.set_channel_prompt(tg_id, new_prompt):
                await update.message.reply_text(
                    f"✅ Инструкция (промпт) для канала <code>{tg_id}</code> успешно сохранена!", 
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text("❌ Ошибка базы данных при сохранении промпта.")

            del self.user_states[user_id]
            context.user_data.clear()


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
    application.add_handler(CommandHandler("time", bot.show_time))
    application.add_handler(CommandHandler("add_channel", bot.add_channel))
    application.add_handler(CommandHandler("manual_channel", bot.manual_channel))
    application.add_handler(CommandHandler("set_prompt", bot.set_prompt_command))
    application.add_handler(CommandHandler("add_admin", bot.add_admin_command))
    application.add_handler(CommandHandler("admins", bot.list_admins))
    application.add_handler(CommandHandler("channels", bot.list_channels))
    application.add_handler(CommandHandler("add_post", bot.add_post))
    application.add_handler(CommandHandler("posts", bot.list_posts))
    application.add_handler(CommandHandler("test_post", bot.test_post))
    
    # Регистрируем обработчик текстовых и медиа сообщений (filters.ALL для фото/видео/текста)
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, bot.handle_message))

    logger.info("Бот запускается...")
    application.run_polling()

if __name__ == '__main__':
    main()
