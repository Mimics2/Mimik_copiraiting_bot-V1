import os
import pytz
from dotenv import load_dotenv

load_dotenv()

# --- Настройки Telegram ---
BOT_TOKEN = os.getenv('8335870133:AAHwcXoy3usOWT4Y9F8cSOPiHwX5OO33hI8')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '6646433980').split(',') if x]
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# --- Настройки базы данных ---
DB_NAME = "scheduler.db"

# --- Настройки WebHook на Railway ---
WEB_SERVER_PORT = int(os.environ.get('PORT', 8080))
WEB_SERVER_BASE_URL = os.getenv('RAILWAY_STATIC_URL', "https://mimikcopiraitingbot-v1-production.up.railway.app")

# --- Настройки CryptoPay Bot ---
CRYPTOPAY_BOT_TOKEN = os.getenv('470214:AAtsGnRZSFgSV3t0yqvHfoepEW37pAcm5Ao')
CRYPTOPAY_CREATE_INVOICE_URL = "https://pay.crypt.bot/api/createInvoice"
CRYPTOPAY_WEBHOOK_PATH = '/payment/cryptopay'
