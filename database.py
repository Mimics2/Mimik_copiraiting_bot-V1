import sqlite3
import logging
from datetime import datetime, timedelta
import pytz

# Настройка часового пояса для корректной работы с датами
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

class Database:
    def __init__(self, db_path='bot.db'):
        self.db_path = db_path
        self.init_db()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        with self.get_connection() as conn:
            # Таблица channels
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
            
            # Таблица posts
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

            # Таблица admins
            conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE,
                    username TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # НОВАЯ КРИТИЧЕСКИ ВАЖНАЯ ТАБЛИЦА: USERS для управления Премиум-доступом
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY UNIQUE,
                    username TEXT,
                    is_premium BOOLEAN DEFAULT 0,
                    premium_until DATETIME DEFAULT NULL,
                    last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
    
    # --- МЕТОДЫ УПРАВЛЕНИЯ ПОЛЬЗОВАТЕЛЯМИ (Премиум) ---

    def get_or_create_user(self, user_id, username=None):
        """Получает данные пользователя или создает новую запись, если ее нет."""
        with self.get_connection() as conn:
            try:
                cursor = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                user_data = cursor.fetchone()
                
                if user_data:
                    # Обновляем имя и активность
                    conn.execute('UPDATE users SET username = ?, last_activity = ? WHERE user_id = ?', 
                                (username, datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d %H:%M:%S'), user_id))
                    conn.commit()
                    # Заново получаем данные после обновления
                    cursor = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                    return cursor.fetchone()
                else:
                    # Создаем нового пользователя
                    conn.execute(
                        'INSERT INTO users (user_id, username) VALUES (?, ?)',
                        (user_id, username)
                    )
                    conn.commit()
                    cursor = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
                    return cursor.fetchone()
            except Exception as e:
                logging.error(f"Error getting/creating user {user_id}: {e}")
                return None

    def is_user_premium(self, user_id):
        """Проверяет, активен ли премиум-доступ у пользователя."""
        user_data = self.get_or_create_user(user_id)
        if not user_data:
            return False
            
        # user_data: 0-user_id, 1-username, 2-is_premium, 3-premium_until
        is_premium_flag = bool(user_data[2])
        premium_until_str = user_data[3]
        
        if is_premium_flag and premium_until_str:
            try:
                premium_until = datetime.strptime(premium_until_str, '%Y-%m-%d %H:%M:%S')
                # Доступ активен, если дата окончания еще не наступила
                return datetime.now() < premium_until 
            except ValueError:
                return False # Некорректная дата
        
        return False

    def activate_premium(self, user_id, days: int, username=None):
        """Активирует или продлевает премиум-доступ."""
        user_data = self.get_or_create_user(user_id, username)
        
        # Определяем новую дату окончания
        current_time = datetime.now(MOSCOW_TZ)
        current_premium_until = current_time 

        if user_data and user_data[3]: # Если есть старая дата
            try:
                # Парсим старую дату как наивную, а потом делаем ее aware (МСК)
                old_until_naive = datetime.strptime(user_data[3], '%Y-%m-%d %H:%M:%S')
                old_until_aware = MOSCOW_TZ.localize(old_until_naive)

                # Если старый доступ не истек, продлеваем от старой даты
                if old_until_aware > current_time:
                    current_premium_until = old_until_aware
            except ValueError:
                pass # Пропускаем, если старая дата была некорректной

        new_until = current_premium_until + timedelta(days=days)
        new_until_str = new_until.strftime('%Y-%m-%d %H:%M:%S')
        
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'UPDATE users SET is_premium = 1, premium_until = ? WHERE user_id = ?',
                    (new_until_str, user_id)
                )
                conn.commit()
                # Возвращаем datetime объект новой даты для уведомления
                return new_until.replace(tzinfo=None) 
            except Exception as e:
                logging.error(f"Error activating premium for user {user_id}: {e}")
                return None


    # --- МЕТОДЫ АДМИНИСТРАТОРОВ (Остались без изменений) ---
    
    def add_admin(self, user_id, username=None):
        """Добавляет пользователя в список администраторов."""
        with self.get_connection() as conn:
            try:
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

    def get_admins(self):
        """Возвращает полный список администраторов (ID, username и т.д.)."""
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM admins ORDER BY added_date')
            return cursor.fetchall()
    
    # --- МЕТОДЫ КАНАЛОВ (Остались без изменений) ---

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
                logging.error(f"Error adding channel: {e}")
                return False

    def get_channels(self):
        with self.get_connection() as conn:
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

    # --- МЕТОДЫ ПОСТОВ (Остались без изменений) ---

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
 
