import sqlite3
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Database:
    def __init__(self, db_name):
        self.db_name = db_name
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_name)

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
                    media_ids TEXT, -- Store as JSON string or comma-separated
                    publish_time DATETIME NOT NULL,
                    is_published INTEGER DEFAULT 0, -- 0 for pending, 1 for published
                    message_id INTEGER, -- Telegram message_id after publishing
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Таблица для связи пользователя с каналом
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_channel_map (
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, channel_id)
                )
            ''')
            # Таблица для отслеживания платежей
            conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    order_id TEXT UNIQUE NOT NULL, -- Уникальный ID заказа от платежной системы
                    status TEXT DEFAULT 'pending', -- 'pending', 'success', 'failed', 'expired'
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    payment_system TEXT, -- 'cryptopay'
                    external_url TEXT -- Ссылка на оплату
                )
            ''')
            conn.commit()


    # --- Методы для работы с пользователями ---
    def add_user(self, user_id, username):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO users (id, username) VALUES (?, ?)',
                    (user_id, username)
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_user(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            return cursor.fetchone()

    def update_user_tariff(self, user_id, tariff_id, expires_at, max_channels, max_posts_per_day):
        with self.get_connection() as conn:
            conn.execute(
                'UPDATE users SET tariff_id = ?, tariff_expires = ?, max_channels = ?, max_posts_per_day = ? WHERE id = ?',
                (tariff_id, expires_at, max_channels, max_posts_per_day, user_id)
            )
            conn.commit()

    # --- Методы для работы с каналами ---
    def add_channel(self, user_id, channel_id, channel_name):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO channels (user_id, channel_id, channel_name) VALUES (?, ?, ?)',
                    (user_id, channel_id, channel_name)
                )
                conn.execute(
                    'INSERT INTO user_channel_map (user_id, channel_id) VALUES (?, ?)',
                    (user_id, channel_id)
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                logging.warning(f"User {user_id} already has channel {channel_id} or channel already exists.")
                return False
            except Exception as e:
                logging.error(f"Error adding channel: {e}")
                return False

    def remove_channel(self, user_id, channel_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM channels WHERE user_id = ? AND channel_id = ?', (user_id, channel_id))
            conn.execute('DELETE FROM user_channel_map WHERE user_id = ? AND channel_id = ?', (user_id, channel_id))
            conn.commit()

    def get_user_channels(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.execute(
                '''SELECT c.channel_id, c.channel_name FROM channels c
                   JOIN user_channel_map ucm ON c.channel_id = ucm.channel_id
                   WHERE ucm.user_id = ?''', (user_id,)
            )
            return cursor.fetchall()

    def get_channel_info(self, channel_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM channels WHERE channel_id = ?', (channel_id,))
            return cursor.fetchone()
            
    def get_channel_owner(self, channel_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT user_id FROM channels WHERE channel_id = ?', (channel_id,))
            result = cursor.fetchone()
            return result[0] if result else None


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
            cursor = conn.execute(
                'SELECT id, channel_id, text, publish_time, is_published FROM posts WHERE user_id = ? ORDER BY publish_time DESC',
                (user_id,)
            )
            return cursor.fetchall()

    def get_posts_to_publish(self):
        now = datetime.datetime.now(pytz.utc) # Получаем текущее время в UTC
        with self.get_connection() as conn:
            # Выбираем посты, которые должны быть опубликованы и еще не опубликованы
            cursor = conn.execute(
                'SELECT id, user_id, channel_id, text, media_ids FROM posts WHERE publish_time <= ? AND is_published = 0',
                (now,)
            )
            return cursor.fetchall()

    def set_post_published(self, post_id, message_id):
        with self.get_connection() as conn:
            conn.execute('UPDATE posts SET is_published = 1, message_id = ? WHERE id = ?', (message_id, post_id))
            conn.commit()

    def get_post_info(self, post_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,))
            return cursor.fetchone()

    def delete_post(self, post_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM posts WHERE id = ?', (post_id,))
            conn.commit()
            
    # --- НОВЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С ПЛАТЕЖАМИ ---
    def add_payment(self, user_id, amount, order_id, status, external_url, payment_system):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'INSERT INTO payments (user_id, amount, order_id, status, external_url, payment_system) VALUES (?, ?, ?, ?, ?, ?)',
                    (user_id, amount, order_id, status, external_url, payment_system)
                )
                conn.commit()
                logging.info(f"Payment {order_id} added for user {user_id} via {payment_system}.")
                return True
            except sqlite3.IntegrityError:
                logging.warning(f"Payment with order_id {order_id} already exists.")
                return False
            except Exception as e:
                logging.error(f"Error adding payment: {e}")
                return False

    def get_payment_by_order_id(self, order_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT * FROM payments WHERE order_id = ?', (order_id,))
            return cursor.fetchone() 

    def update_payment_status(self, order_id, status):
        with self.get_connection() as conn:
            try:
                conn.execute('UPDATE payments SET status = ? WHERE order_id = ?', (status, order_id))
                conn.commit()
                logging.info(f"Payment {order_id} status updated to {status}.")
                return True
            except Exception as e:
                logging.error(f"Error updating payment status for {order_id}: {e}")
                return False

    def add_balance(self, user_id, amount):
        with self.get_connection() as conn:
            try:
                conn.execute(
                    'UPDATE users SET balance = balance + ? WHERE id = ?',
                    (amount, user_id)
                )
                conn.commit()
                logging.info(f"Added {amount} to user {user_id} balance.")
                return True
            except Exception as e:
                logging.error(f"Error adding balance to user {user_id}: {e}")
                return False

    def get_user_balance(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0.0

