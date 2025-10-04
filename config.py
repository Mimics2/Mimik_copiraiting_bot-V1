# config (1) (3).py
import os
from dotenv import load_dotenv
load_dotenv()
# Если на Railway переменная BOT_TOKEN не установлена, будет использован ваш новый токен.
BOT_TOKEN = os.getenv('BOT_TOKEN', '8331986255:AAH6Y0ELNanUc0Ae7gD0qLh3A-tf-cH5V4E') 
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '6646433980').split(',') if x]

# ВАЖНО: Убедитесь, что эта переменная добавлена в ваш .env файл или Railway
CRYPTO_PAY_TOKEN = os.getenv('CRYPTO_PAY_TOKEN', '469021:AAk5JaZKX7N17mOHy932AEw5IH4ANCE96Jk')
