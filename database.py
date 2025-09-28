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
                    created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES channels (id)
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
                logging.error(f"Error adding channel: {e}")
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
    
    def delete_post(self, post_id):
        with self.get_connection() as conn:
            try:
                conn.execute('DELETE FROM posts WHERE id = ?', (post_id,))
                conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error deleting post: {e}")
                return False