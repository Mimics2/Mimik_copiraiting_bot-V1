# config (1) (5).py

import os
from dotenv import load_dotenv
import pytz

load_dotenv()

# --- Настройки Telegram ---
# Если на Railway переменная BOT_TOKEN не установлена, будет использован ваш новый токен.
BOT_TOKEN = os.getenv('BOT_TOKEN', '8331986255:AAH6Y0ELNanUc0Ae7gD0qLh3A-tf-cH5V4E') 
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '6646433980').split(',') if x]
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# --- Настройки WebHook на Railway ---
# Порт 8080, который ты указал на Railway
WEB_SERVER_PORT = int(os.environ.get('PORT', 8080))  

# Базовый URL, который тебе сгенерировал Railway
WEB_SERVER_BASE_URL = "https://Mimikcopiraitingbot-v1-production.up.railway.app"

# Полный адрес, куда CryptoCloud будет отправлять уведомления
WEBHOOK_PATH = '/payment_callback'
WEBHOOK_URL = f"{WEB_SERVER_BASE_URL}{WEBHOOK_PATH}" 

# --- Настройки CryptoCloud (Криптовалютные платежи в USD) ---
# ТВОЙ API-КЛЮЧ (Токен JWT) - ВАЖНО: ХРАНИТЬ В ПЕРЕМЕННЫХ ОКРУЖЕНИЯ НА RAILWAY
CRYPTO_CLOUD_API_KEY = os.getenv('CRYPTO_CLOUD_API_KEY', 'EyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1dWlkIjoiTnpNeU5qTT0iLCJ0eXBlIjoicHJvamVjdCIsInYiOiIzNDgyYWM5OWEyMmM1YWNhZjUzMzYyNzBlMWE3N2QwMmMzNTNhYjYzMjFkYWM2Y2M1OGEwNmFkOGU1MmYwYzc1IiwiZXhwIjo4ODE1OTY1OTk1N30.CvtNWK4aUz3yimryjnBgfR54JCa2GW8AGpjiu902djI')
CRYPTO_CLOUD_CREATE_URL = "https://api.cryptocloud.plus/v2/invoice/create" 

# Секретный ключ WebHook (ОБЯЗАТЕЛЬНО ЗАМЕНИТЬ НА ВАШ СЕКРЕТ ИЗ CRYPTOCLOUD!)
# Этот ключ должен быть установлен в настройках WebHook на CryptoCloud
CRYPTO_CLOUD_WEBHOOK_SECRET = os.getenv('CRYPTO_CLOUD_WEBHOOK_SECRET', 'IzSawGM1NhTjubTt1KXqyKoxLn5GyP9qrEuv')
