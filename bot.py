import logging
import asyncio
import re
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from telegram.ext import Application, CommandHandler, CallbackContext
from telegram import Update
from utils import add_forwarding_task, get_forwarding_tasks, remove_forwarding_task
from config import TELEGRAM_BOT_TOKEN, API_ID, API_HASH, MONGO_URI
from pymongo import MongoClient

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize MongoDB
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["telegram_bot"]
tasks_collection = db["forwarding_tasks"]

# Initialize Pyrogram client (do not start yet)
user_client = Client("user_session", api_id=API_ID, api_hash=API_HASH, no_updates=True)

# Bot instance for python-telegram-bot
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# Flag to track if Pyrogram client is running
client_running = False

# Cooldown tracking for login/resendcode
last_code_request = 0
COOLDOWN_SECONDS = 30

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Welcome! Use /login <phone_number> to authenticate, /addtask to set forwarding, /listtasks to view tasks, or /removetask to delete a task.")

async def login(update: Update, context: CallbackContext) -> None:
    global client_running, last_code_request
    phone_number = " ".join(context.args).strip()
    if not phone_number:
        await update.message.reply_text("Please provide your phone number: /login <phone_number>\nExample: /login +1234567890")
        return
    # Validate phone number format
    if not re.match(r'^\+\d{10,15}$', phone_number):
        await update.message.reply_text("Invalid phone number format. Use international format: /login +1234567890")
        return
    # Check cooldown
    current_time = time.time()
    if current_time - last_code_request < COOLDOWN_SECONDS:
        await update.message.reply_text(f"Please wait {int(COOLDOWN_SECONDS - (current_time - last_code_request))} seconds before requesting a new code.")
        return
    try:
        # Ensure client is disconnected before attempting new connection
        if user_client.is_connected:
            await user_client.disconnect()
        await user_client.connect()
        code = await user_client.send_code(phone_number)
        client_running = True
        last_code_request = current_time
        await update.message.reply_text("Verification code sent! Enter it within 2 minutes using: /verify <code>\nCheck your Telegram app or SMS. If the code expires, use /resendcode.")
        context.user_data["phone_code_hash"] = code.phone_code_hash
        context.user_data["phone_number"] = phone_number
        logger.info(f"Login initiated at {time.ctime()}: phone number (masked): {phone_number[:4]}...")
    except Exception as e:
        logger.error(f"Login error at {time.ctime()}: {e}")
        if "429" in str(e):
            await update.message.reply_text("Error: Too many login attempts. Please wait a few minutes and try again.")
        else:
            await update.message.reply_text(f"Error: {str(e)}")
    finally:
        if user_client.is_connected:
            await user_client.disconnect()

async def resend_code(update: Update, context: CallbackContext) -> None:
    global client_running, last_code_request
    phone_number = context.user_data.get("phone_number")
    if not phone_number:
        await update.message.reply_text("Please login first using /login <phone_number>")
        return
    # Check cooldown
    current_time = time.time()
    if current_time - last_code_request < COOLDOWN_SECONDS:
        await update.message.reply_text(f"Please wait {int(COOLDOWN_SECONDS - (current_time - last_code_request))} seconds before requesting a new code.")
        return
    try:
        if user_client.is_connected:
            await user_client.disconnect()
        await user_client.connect()
        code = await user_client.send_code(phone_number)
        client_running = True
        last_code_request = current_time
        await update.message.reply_text("New verification code sent! Enter it within 2 minutes using: /verify <code>\nCheck your Telegram app or SMS.")
        context.user_data["phone_code_hash"] = code.phone_code_hash
        logger.info(f"Code resent at {time.ctime()}: phone number (masked): {phone_number[:4]}...")
    except Exception as e:
        logger.error(f"Resend code error at {time.ctime()}: {e}")
        if "429" in str(e):
            await update.message.reply_text("Error: Too many login attempts. Please wait a few minutes and try again.")
        else:
            await update.message.reply_text(f"Error: {str(e)}")
    finally:
        if user_client.is_connected:
            await user_client.disconnect()

async def verify(update: Update, context: CallbackContext) -> None:
    global client_running
    code = " ".join(context.args).strip()
    phone_number = context.user_data.get("phone_number")
    phone_code_hash = context.user_data.get("phone_code_hash")
    if not code or not phone_number or not phone_code_hash:
        await update.message.reply_text("Please login first using /login <phone_number>")
        return
    try:
        await user_client.connect()
        await user_client.sign_in(phone_number, phone_code_hash, code)
        await user_client.start()  # Start client after successful login
        client_running = True
        await update.message.reply_text("Successfully logged in!")
        logger.info(f"Verification successful at {time.ctime()}: phone number (masked): {phone_number[:4]}...")
    except Exception as e:
        logger.error(f"Verification error at {time.ctime()}: {e}")
        if "PHONE_CODE_EXPIRED" in str(e):
            await update.message.reply_text("Error: The verification code has expired. Request a new code using /resendcode.")
            client_running = False
        elif "429" in str(e):
            await update.message.reply_text("Error: Too many login attempts. Please wait a few minutes and try again.")
        else:
            await update.message.reply_text(f"Error: {str(e)}")
    finally:
        if user_client.is_connected:
            await user_client.disconnect()

async def add_task(update: Update, context: CallbackContext) -> None:
    global client_running
    if not client_running:
        await update.message.reply_text("Please login first using /login <phone_number> and /verify <code>")
        return
    args = context.args
    if len(args) != 3:
        await update.message.reply_text("Usage: /addtask <source_id> <destination_id> <type>\nExample: /addtask -100123456789 -100987654321 channel_to_channel\nTypes: channel_to_channel, bot_to_channel, channel_to_bot, channel_to_user, user_to_bot")
        return
    source_id, destination_id, task_type = args
    valid_types = ["channel_to_channel", "bot_to_channel", "channel_to_bot", "channel_to_user", "user_to_bot"]
    if task_type not in valid_types:
        await update.message.reply_text(f"Invalid type. Use one of: {', '.join(valid_types)}")
        return
    # Validate chat IDs
    if not (source_id.startswith('-') or source_id.startswith('@') or source_id.isdigit()) or \
       not (destination_id.startswith('-') or destination_id.startswith('@') or destination_id.isdigit()):
        await update.message.reply_text("Invalid chat ID format. Use channel IDs (e.g., -100123456789), usernames (e.g., @username), or user IDs.")
        return
    try:
        task_id = add_forwarding_task(source_id, destination_id, task_type, tasks_collection)
        await update.message.reply_text(f"Task added successfully! Task ID: {task_id}")
        logger.info(f"Task added at {time.ctime()}: {source_id} -> {destination_id}, Type: {task_type}")
    except Exception as e:
        logger.error(f"Add task error at {time.ctime()}: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

async def list_tasks(update: Update, context: CallbackContext) -> None:
    try:
        tasks = get_forwarding_tasks(tasks_collection)
        if not tasks:
            await update.message.reply_text("No forwarding tasks found.")
            return
        response = "Forwarding Tasks:\n"
        for task in tasks:
            response += f"ID: {task['_id']}, Source: {task['source_id']}, Destination: {task['destination_id']}, Type: {task['type']}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"List tasks error at {time.ctime()}: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

async def remove_task(update: Update, context: CallbackContext) -> None:
    task_id = " ".join(context.args).strip()
    if not task_id:
        await update.message.reply_text("Please provide the task ID: /removetask <task_id>\nExample: /removetask 507f1f77bcf86cd799439011")
        return
    try:
        result = remove_forwarding_task(task_id, tasks_collection)
        if result:
            await update.message.reply_text(f"Task {task_id} removed successfully!")
        else:
            await update.message.reply_text(f"Task {task_id} not found.")
        logger.info(f"Task removal attempted at {time.ctime()}: ID: {task_id}, Success: {result}")
    except Exception as e:
        logger.error(f"Remove task error at {time.ctime()}: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

# Pyrogram message handler for forwarding
@user_client.on_message(filters.chat([int(task["source_id"]) for task in get_forwarding_tasks(tasks_collection) if task["source_id"].startswith('-')]))
async def forward_message(client: Client, message: Message):
    tasks = get_forwarding_tasks(tasks_collection)
    for task in tasks:
        if int(task["source_id"]) == message.chat.id:
            try:
                await message.forward(int(task["destination_id"]))
                logger.info(f"Forwarded message at {time.ctime()}: {task['source_id']} -> {task['destination_id']}")
            except Exception as e:
                logger.error(f"Forwarding error at {time.ctime()}: {e}")

async def run_bot():
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("login", login))
    application.add_handler(CommandHandler("resendcode", resend_code))
    application.add_handler(CommandHandler("verify", verify))
    application.add_handler(CommandHandler("addtask", add_task))
    application.add_handler(CommandHandler("listtasks", list_tasks))
    application.add_handler(CommandHandler("removetask", remove_task))

    # Start polling for bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

async def main():
    # Run the bot and keep the event loop alive
    await run_bot()
    try:
        while True:
            await asyncio.sleep(3600)  # Keep the loop running
    except KeyboardInterrupt:
        global client_running
        if client_running and user_client.is_connected:
            await user_client.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
