import sqlite3
import logging
from datetime import datetime

class Database:
    def __init__(self, db_path='bot.db'):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        with self.get_connection() as conn:
            # Таблица channels: добавлено default_prompt
            conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER UNIQUE,
                    title TEXT,
                    username TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    default_prompt TEXT DEFAULT '' -- Поле для промпта ИИ
                )
            ''')
            
            # Таблица posts: добавлены media_file_id и media_type
            conn.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    message_text TEXT,
                    scheduled_time DATETIME,
                    status TEXT DEFAULT 'scheduled',
                    created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    media_file_id TEXT,  -- ID файла в Telegram
                    media_type TEXT,     -- Тип файла (photo, video)
                    FOREIGN KEY (channel_id) REFERENCES channels (id)
                )
            ''')

            # НОВАЯ ТАБЛИЦА: ADMINS для динамического управления доступом
            conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
    
    # --- МЕТОДЫ АДМИНИСТРАТОРОВ ---
    
    def add_admin(self, user_id, username=None):
        """Добавляет пользователя в список администраторов."""
        with self.get_connection() as conn:
            try:
                # INSERT OR IGNORE предотвращает ошибку, если админ уже есть
                conn.execute(
                    'INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)',
                    (user_id, username)
                )
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error adding admin: {e}")
                return False

    def get_admin_ids(self):
        """Возвращает список всех ID администраторов (для проверки прав)."""
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT user_id FROM admins')
            return [row[0] for row in cursor.fetchall()]

    def is_admin(self, user_id):
        """Проверяет, является ли пользователь администратором."""
        # Этот метод не используется напрямую, но его логика встроена в bot.py через get_admin_ids
        pass

    def get_admins(self):
        """Возвращает полный список администраторов (ID, username и т.д.)."""
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM admins ORDER BY added_date')
            return cursor.fetchall()
    
    # --- МЕТОДЫ КАНАЛОВ ---

    def add_channel(self, channel_id, title, username):
        with self.get_connection() as conn:
            try:
                # Вставка или замена существующего канала
                conn.execute(
                    'INSERT OR REPLACE INTO channels (channel_id, title, username) VALUES (?, ?, ?)',
                    (channel_id, title, username)
                )
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error adding channel: {e}")
                return False

    def get_channels(self):
        with self.get_connection() as conn:
            # Возвращаем все поля, включая default_prompt
            cursor = conn.execute('SELECT * FROM channels ORDER BY added_date')
            return cursor.fetchall()

    def get_channel_info(self, tg_channel_id):
        """Получает полную информацию о канале по его TG ID."""
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM channels WHERE channel_id = ?', (tg_channel_id,))
            return cursor.fetchone()

    def get_channel_info_by_db_id(self, db_id):
        """Получает полную информацию о канале по его внутреннему ID (id)."""
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM channels WHERE id = ?', (db_id,))
            return cursor.fetchone()

    def set_channel_prompt(self, tg_channel_id, new_prompt):
        """Обновляет промпт по умолчанию для указанного канала."""
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'UPDATE channels SET default_prompt = ? WHERE channel_id = ?',
                    (new_prompt, tg_channel_id)
                )
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error setting prompt for channel {tg_channel_id}: {e}")
                return False

    # --- МЕТОДЫ ПОСТОВ ---

    def add_post(self, channel_id, message_text, scheduled_time, media_file_id=None, media_type=None):
        """Добавление поста с поддержкой медиафайлов."""
        with self.get_connection() as conn:
            try:
                cursor = conn.execute(
                    'INSERT INTO posts (channel_id, message_text, scheduled_time, media_file_id, media_type) VALUES (?, ?, ?, ?, ?)',
                    (channel_id, message_text, scheduled_time, media_file_id, media_type)
                )
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logging.error(f"Error adding post: {e}")
                return False
    
    def get_posts(self):
        with self.get_connection() as conn:
            # Структура: 0-id, 1-channel_db_id, 2-message_text, 3-scheduled_time_str, 4-status, 5-created_date, 6-media_file_id, 7-media_type, 8-channel_title, 9-tg_channel_id
            cursor = conn.execute('''
                SELECT p.*, c.title, c.channel_id as tg_channel_id 
                FROM posts p 
                JOIN channels c ON p.channel_id = c.id 
                WHERE p.status = "scheduled"
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
                logging.error(f"Error updating post status: {e}")
                return False

