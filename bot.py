import logging
import asyncio
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

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Welcome! Use /login <phone_number> to authenticate, /addtask to set forwarding, /listtasks to view tasks, or /removetask to delete a task.")

async def login(update: Update, context: CallbackContext) -> None:
    global client_running
    phone_number = " ".join(context.args)
    if not phone_number:
        await update.message.reply_text("Please provide your phone number: /login <phone_number>")
        return
    try:
        if not client_running:
            # Start Pyrogram client with the provided phone number
            await user_client.connect()
            code = await user_client.send_code(phone_number)
            await user_client.disconnect()
            client_running = True
            await update.message.reply_text("Enter the verification code sent to your phone: /verify <code>")
            context.user_data["phone_code_hash"] = code.phone_code_hash
            context.user_data["phone_number"] = phone_number
        else:
            await update.message.reply_text("Client already running. Please verify with /verify <code> or logout first.")
    except Exception as e:
        logger.error(f"Login error: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

async def verify(update: Update, context: CallbackContext) -> None:
    global client_running
    code = " ".join(context.args)
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
    except Exception as e:
        logger.error(f"Verification error: {e}")
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
        await update.message.reply_text("Usage: /addtask <source_id> <destination_id> <type>\nTypes: channel_to_channel, bot_to_channel, channel_to_bot, channel_to_user, user_to_bot")
        return
    source_id, destination_id, task_type = args
    valid_types = ["channel_to_channel", "bot_to_channel", "channel_to_bot", "channel_to_user", "user_to_bot"]
    if task_type not in valid_types:
        await update.message.reply_text(f"Invalid type. Use one of: {', '.join(valid_types)}")
        return
    try:
        task_id = add_forwarding_task(source_id, destination_id, task_type, tasks_collection)
        await update.message.reply_text(f"Task added successfully! Task ID: {task_id}")
    except Exception as e:
        logger.error(f"Add task error: {e}")
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
        logger.error(f"List tasks error: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

async def remove_task(update: Update, context: CallbackContext) -> None:
    task_id = " ".join(context.args)
    if not task_id:
        await update.message.reply_text("Please provide the task ID: /removetask <task_id>")
        return
    try:
        result = remove_forwarding_task(task_id, tasks_collection)
        if result:
            await update.message.reply_text(f"Task {task_id} removed successfully!")
        else:
            await update.message.reply_text(f"Task {task_id} not found.")
    except Exception as e:
        logger.error(f"Remove task error: {e}")
        await update.message.reply_text(f"Error: {str(e)}")

# Pyrogram message handler for forwarding
@user_client.on_message(filters.chat([int(task["source_id"]) for task in get_forwarding_tasks(tasks_collection)]))
async def forward_message(client: Client, message: Message):
    tasks = get_forwarding_tasks(tasks_collection)
    for task in tasks:
        if int(task["source_id"]) == message.chat.id:
            try:
                await message.forward(int(task["destination_id"]))
                logger.info(f"Forwarded message from {task['source_id']} to {task['destination_id']}")
            except Exception as e:
                logger.error(f"Forwarding error: {e}")

async def run_bot():
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("login", login))
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
        if client_running:
            await user_client.stop()
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
