import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv('BOT_TOKEN', '8331986255:AAH6Y0ELNanUc0Ae7gD0qLh3A-tf-cH5V4E')

logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает на Railway!")

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    
    print("Бот запущен")
    application.run_polling()

if __name__ == '__main__':
    main()
