# database.py

import sqlite3
import logging
import datetime # <-- ДОБАВИТЬ
import pytz     # <-- ДОБАВИТЬ

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Database:
    def __init__(self, db_name):
        self.db_name = db_name
        self.init_db()

    def get_connection(self):
        # Эта настройка помогает правильно работать с датой и временем
        return sqlite3.connect(self.db_name, detect_types=sqlite3.PARSE_DECLTYPES)

    def init_db(self):
        with self.get_connection() as conn:
            # Таблица для пользователей
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    balance REAL DEFAULT 0.0,
                    tariff_id INTEGER DEFAULT 0,
                    tariff_expires DATETIME,
                    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    max_channels INTEGER DEFAULT 1,
                    max_posts_per_day INTEGER DEFAULT 2
                )
            ''')
            # Таблица для каналов
            conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    channel_name TEXT,
                    UNIQUE(user_id, channel_id)
                )
            ''')
            # Таблица для постов
            conn.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    text TEXT,
                    media_ids TEXT,
                    publish_time TEXT NOT NULL, -- Храним как текст в формате ISO
                    is_published INTEGER DEFAULT 0,
                    message_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Таблица для платежей
            conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    order_id TEXT UNIQUE NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    payment_system TEXT,
                    external_url TEXT
                )
            ''')
            conn.commit()

    # --- Методы для работы с пользователями ---
    def add_user(self, user_id, username):
        with self.get_connection() as conn:
            try:
                conn.execute('INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)', (user_id, username))
                conn.commit()
            except sqlite3.IntegrityError:
                pass
    
    def get_user(self, user_id):
        with self.get_connection() as conn:
            return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    # --- Методы для работы с каналами ---
    def add_channel(self, user_id, channel_id, channel_name):
        with self.get_connection() as conn:
            try:
                conn.execute('INSERT INTO channels (user_id, channel_id, channel_name) VALUES (?, ?, ?)', (user_id, channel_id, channel_name))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_channel(self, user_id, channel_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM channels WHERE user_id = ? AND channel_id = ?', (user_id, channel_id))
            conn.commit()

    def get_user_channels(self, user_id):
        with self.get_connection() as conn:
            return conn.execute('SELECT channel_id, channel_name FROM channels WHERE user_id = ?', (user_id,)).fetchall()

    def get_channel_info(self, channel_id):
        with self.get_connection() as conn:
            return conn.execute('SELECT * FROM channels WHERE channel_id = ?', (channel_id,)).fetchone()

    # --- Методы для работы с постами ---
    def add_post(self, user_id, channel_id, text, media_ids, publish_time):
        with self.get_connection() as conn:
            conn.execute(
                'INSERT INTO posts (user_id, channel_id, text, media_ids, publish_time) VALUES (?, ?, ?, ?, ?)',
                (user_id, channel_id, text, media_ids, publish_time)
            )
            conn.commit()

    def get_user_posts(self, user_id):
        with self.get_connection() as conn:
            return conn.execute(
                'SELECT id, channel_id, text, publish_time, is_published FROM posts WHERE user_id = ? ORDER BY publish_time DESC',
                (user_id,)
            ).fetchall()

    def get_posts_to_publish(self):
        now_utc_str = datetime.datetime.now(pytz.utc).isoformat()
        with self.get_connection() as conn:
            return conn.execute(
                'SELECT id, user_id, channel_id, text, media_ids FROM posts WHERE publish_time <= ? AND is_published = 0',
                (now_utc_str,)
            ).fetchall()

    def set_post_published(self, post_id, message_id):
        with self.get_connection() as conn:
            conn.execute('UPDATE posts SET is_published = 1, message_id = ? WHERE id = ?', (message_id, post_id))
            conn.commit()

    def get_post_info(self, post_id):
        with self.get_connection() as conn:
            return conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()

    def delete_post(self, post_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM posts WHERE id = ?', (post_id,))
            conn.commit()
            
    # --- Методы для работы с платежами ---
    def add_payment(self, user_id, amount, order_id, status, external_url, payment_system):
        with self.get_connection() as conn:
            conn.execute(
                'INSERT INTO payments (user_id, amount, order_id, status, external_url, payment_system) VALUES (?, ?, ?, ?, ?, ?)',
                (user_id, amount, order_id, status, external_url, payment_system)
            )
            conn.commit()

    def get_payment_by_order_id(self, order_id):
        with self.get_connection() as conn:
            return conn.execute('SELECT * FROM payments WHERE order_id = ?', (order_id,)).fetchone() 

    def update_payment_status(self, order_id, status):
        with self.get_connection() as conn:
            conn.execute('UPDATE payments SET status = ? WHERE order_id = ?', (status, order_id))
            conn.commit()

    def add_balance(self, user_id, amount):
        with self.get_connection() as conn:
            conn.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))
            conn.commit()

    def get_user_balance(self, user_id):
        with self.get_connection() as conn:
            result = conn.execute('SELECT balance FROM users WHERE id = ?', (user_id,)).fetchone()
            return result[0] if result else 0.0

