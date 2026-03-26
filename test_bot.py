"""Quick test — just the bot, no GEE, no scheduler."""
import os
from dotenv import load_dotenv
load_dotenv()

from telegram.ext import Application, CommandHandler
from telegram import Update
from telegram.ext import ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
print(f"Using token: {TOKEN[:20]}...")

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is working! LandSentinel is alive.")

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))

print("Bot started — send /start to @Skydeeder_bot on Telegram")
app.run_polling()
