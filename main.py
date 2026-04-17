import logging
import pytz
from apscheduler.triggers.cron import CronTrigger
from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler
from src.config import TELEGRAM_BOT_TOKEN, MARKET_TZ
from src.models.database import get_pool, close_pool
from src.models.schema import init_schema
from src.handlers.commands import (
    start, subscribe, unsubscribe, pause, resume,
    watchlist, add_ticker, remove_ticker,
    set_portfolio, buy, sell, check, help_command,
)
from src.scheduler.jobs import hourly_update

BOT_COMMANDS = [
    BotCommand("start", "Khởi động bot và đăng ký tài khoản"),
    BotCommand("help", "Hướng dẫn sử dụng & tuyên bố miễn trừ"),
    BotCommand("subscribe", "Bật nhận cập nhật hàng giờ"),
    BotCommand("unsubscribe", "Tắt nhận cập nhật"),
    BotCommand("pause", "Tạm dừng cập nhật"),
    BotCommand("resume", "Tiếp tục cập nhật"),
    BotCommand("watchlist", "Xem danh mục theo dõi"),
    BotCommand("add", "Thêm cổ phiếu — /add HPG"),
    BotCommand("remove", "Xóa cổ phiếu — /remove HPG"),
    BotCommand("setportfolio", "Cài giá trị danh mục — /setportfolio 100000000"),
    BotCommand("buy", "Ghi nhận mua — /buy HPG 27000"),
    BotCommand("sell", "Đóng vị thế — /sell HPG"),
    BotCommand("check", "Phân tích ngay — /check HPG"),
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application):
    await get_pool()
    await init_schema()
    logger.info("Database connected and schema initialized.")

    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Bot command menu registered.")

    tz = pytz.timezone(MARKET_TZ)
    application.job_queue.run_custom(
        hourly_update,
        job_kwargs={
            "trigger": CronTrigger(
                day_of_week="mon-fri",
                hour="8-15",
                minute=0,
                timezone=tz,
            )
        },
    )
    logger.info("Hourly scheduler registered (Mon–Fri 8:00–15:00 ICT).")


async def post_shutdown(application):
    await close_pool()
    logger.info("Database pool closed.")


def main():
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("watchlist", watchlist))
    app.add_handler(CommandHandler("add", add_ticker))
    app.add_handler(CommandHandler("remove", remove_ticker))
    app.add_handler(CommandHandler("setportfolio", set_portfolio))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("check", check))

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
