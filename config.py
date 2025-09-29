# config (1) (1).py
import os
from dotenv import load_dotenv
load_dotenv()
# Если на Railway переменная BOT_TOKEN не установлена, будет использован ваш новый токен.
BOT_TOKEN = os.getenv('BOT_TOKEN', '8331986255:AAH6Y0ELNanUc0Ae7gD0qLh3A-tf-cH5V4E') 
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '6646433980').split(',') if x]
