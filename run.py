import asyncio
from app.main import bot, dp, init_db

async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())