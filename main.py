import logging
import os
import threading
import pytz
import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.error import TelegramError
from src.config import TELEGRAM_BOT_TOKEN, MARKET_TZ
from src.models.database import get_pool, close_pool
from src.models.schema import init_schema
from src.handlers.commands import (
    start, subscribe, unsubscribe, pause, resume,
    watchlist, add_ticker, remove_ticker,
    set_portfolio, set_interval, buy, sell, check, news, help_command,
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
    BotCommand("setinterval", "Cài tần suất cập nhật — /setinterval 30|60|90|120"),
    BotCommand("buy", "Ghi nhận mua — /buy HPG 27000"),
    BotCommand("sell", "Đóng vị thế — /sell HPG"),
    BotCommand("check", "Phân tích ngay — /check HPG"),
    BotCommand("news", "Xem tin tức — /news hoặc /news HPG"),
]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_HEALTH_PORT = int(os.environ.get("PORT", 8080))
_RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # suppress access logs


def _start_health_server():
    server = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
    server.serve_forever()


async def _keep_alive(context: ContextTypes.DEFAULT_TYPE) -> None:
    url = _RENDER_URL or f"http://localhost:{_HEALTH_PORT}"
    try:
        async with httpx.AsyncClient() as client:
            await client.get(f"{url}/health", timeout=10)
        logger.debug("Keep-alive ping sent.")
    except Exception as e:
        logger.warning(f"Keep-alive ping failed: {e}")


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
                minute="0,30",
                timezone=tz,
            )
        },
    )
    logger.info("Scheduler registered (Mon–Fri 8:00–15:00 ICT, every 30 min).")

    application.job_queue.run_custom(
        _keep_alive,
        job_kwargs={"trigger": IntervalTrigger(minutes=5)},
    )
    logger.info("Keep-alive job registered (every 5 min).")

    thread = threading.Thread(target=_start_health_server, daemon=True)
    thread.start()
    logger.info(f"Health server started on port {_HEALTH_PORT}.")


async def post_shutdown(application):
    await close_pool()
    logger.info("Database pool closed.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Telegram error: {context.error}")


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
    app.add_handler(CommandHandler("setinterval", set_interval))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("news", news))
    app.add_error_handler(error_handler)

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
