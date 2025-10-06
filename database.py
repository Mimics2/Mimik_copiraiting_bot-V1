# database (1) (4).py

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
        # Используем check_same_thread=False для совместимости с асинхронными фреймворками
        return sqlite3.connect(self.db_path, check_same_thread=False)
    
    def init_db(self):
        with self.get_connection() as conn:
            # Таблица channels (поле default_prompt удалено)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER UNIQUE,
                    title TEXT,
                    username TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
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
                    media_file_id TEXT,  
                    media_type TEXT,     
                    FOREIGN KEY (channel_id) REFERENCES channels(id)
                )
            ''')

            # --- ТАБЛИЦА: admins ---
            conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY UNIQUE,
                    username TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # --- ТАБЛИЦА: premium_users ---
            conn.execute('''
                CREATE TABLE IF NOT EXISTS premium_users (
                    user_id INTEGER PRIMARY KEY UNIQUE,
                    end_date DATETIME,
                    start_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')

            # --- ТАБЛИЦА: orders ---
            conn.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    amount REAL,
                    status TEXT DEFAULT 'pending',
                    created_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            conn.commit()

    # --- МЕТОДЫ ДЛЯ АДМИНОВ ---
    def add_admin(self, user_id, username="Unknown"):
        with self.get_connection() as conn:
            try:
                conn.execute('INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)', (user_id, username))
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error adding admin: {e}")
                return False

    def get_admin_ids(self):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT user_id FROM admins')
            return [row[0] for row in cursor.fetchall()]

    # --- МЕТОДЫ ДЛЯ PREMIUM/ОПЛАТЫ ---
    def is_user_premium(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.execute(
                'SELECT end_date FROM premium_users WHERE user_id = ? AND is_active = 1', 
                (user_id,)
            )
            result = cursor.fetchone()
            if result:
                end_date_str = result[0]
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=MOSCOW_TZ)
                return end_date > datetime.now(MOSCOW_TZ)
            return False

    def add_or_update_premium_user(self, user_id, days=30):
        with self.get_connection() as conn:
            try:
                cursor = conn.execute('SELECT end_date FROM premium_users WHERE user_id = ?', (user_id,))
                result = cursor.fetchone()
                
                if result:
                    current_end_date = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=MOSCOW_TZ)
                    start_count_date = max(current_end_date, datetime.now(MOSCOW_TZ))
                    new_end_date = start_count_date + timedelta(days=days)
                else:
                    new_end_date = datetime.now(MOSCOW_TZ) + timedelta(days=days)
                
                conn.execute(
                    'INSERT OR REPLACE INTO premium_users (user_id, end_date, is_active) VALUES (?, ?, 1)',
                    (user_id, new_end_date.strftime('%Y-%m-%d %H:%M:%S'))
                )
                
                conn.commit()
                return new_end_date
            except Exception as e:
                logging.error(f"Error adding/updating premium user: {e}")
                return False

    def add_order(self, order_id, user_id, amount):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO orders (order_id, user_id, amount, status) VALUES (?, ?, ?, ?)',
                    (order_id, user_id, amount, 'pending')
                )
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error adding order: {e}")
                return False

    def update_order_status(self, order_id, status):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'UPDATE orders SET status = ? WHERE id = ?',
                    (status, order_id)
                )
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error updating order status: {e}")
                return False

    # --- Методы для работы с каналами и постами (оставлены для полноты) ---
    def add_channel(self, channel_id, title, username):
        with self.get_connection() as conn:
            try:
                conn.execute('INSERT OR IGNORE INTO channels (channel_id, title, username) VALUES (?, ?, ?)', 
                             (channel_id, title, username))
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error adding channel: {e}")
                return False
                
    def get_channels(self):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT id, channel_id, title, username FROM channels')
            return cursor.fetchall()
            
    def add_post(self, channel_id, message_text, scheduled_time, media_file_id=None, media_type=None):
        with self.get_connection() as conn:
            try:
                scheduled_time_str = scheduled_time.strftime('%Y-%m-%d %H:%M:%S')
                cursor = conn.execute(
                    'INSERT INTO posts (channel_id, message_text, scheduled_time, media_file_id, media_type) VALUES (?, ?, ?, ?, ?)',
                    (channel_id, message_text, scheduled_time_str, media_file_id, media_type)
                )
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                logging.error(f"Error adding post: {e}")
                return False

    def get_posts(self):
        with self.get_connection() as conn:
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
