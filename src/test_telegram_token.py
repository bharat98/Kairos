import os
import asyncio
from telegram import Bot
from dotenv import load_dotenv

# Force reload of .env
load_dotenv(override=True)

async def verify_token():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token or token == "your_telegram_bot_token_here":
        print("ERROR: TELEGRAM_BOT_TOKEN not found or not set in .env")
        return

    print(f"Attempting to verify token: {token[:5]}...{token[-5:]}")
    
    try:
        bot = Bot(token=token)
        me = await bot.get_me()
        print(f"SUCCESS! Connected to Telegram.")
        print(f"Bot Name: {me.first_name}")
        print(f"Bot Username: @{me.username}")
    except Exception as e:
        print(f"FAILED to connect to Telegram: {e}")

if __name__ == "__main__":
    asyncio.run(verify_token())
