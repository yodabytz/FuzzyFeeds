#!/usr/bin/env python3
import asyncio
import logging
import json
import os
import time
import fnmatch
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError

from config import (
    telegram_bot_token, telegram_channels, admin, admins,
    start_time
)
import feed
import persistence
import users
from commands import get_help
from channels import load_channels


logging.basicConfig(level=logging.INFO)

# Global variables
telegram_bot_instance = None
telegram_application = None
POSTED_FILE = "telegram_posted.json"

# Disable internal feed loop - centralized polling handles feeds
feed_loop_enabled = False

def disable_feed_loop():
    global feed_loop_enabled
    feed_loop_enabled = False

# --- Per-Chat Posted Feeds Storage ---
def load_posted_articles():
    if os.path.exists(POSTED_FILE):
        try:
            with open(POSTED_FILE, "r") as f:
                data = json.load(f)
                return {chat: set(links) for chat, links in data.items()}
        except Exception as e:
            logging.error(f"Error loading {POSTED_FILE}: {e}")
            return {}
    return {}

def save_posted_articles(posted_dict):
    try:
        serializable = {chat: list(links) for chat, links in posted_dict.items()}
        with open(POSTED_FILE, "w") as f:
            json.dump(serializable, f, indent=4)
    except Exception as e:
        logging.error(f"Error saving {POSTED_FILE}: {e}")

# Global posted articles tracking
posted_articles = load_posted_articles()

def match_feed(feed_dict, pattern):
    """Match feed names with wildcards support"""
    if "*" in pattern or "?" in pattern:
        matches = [name for name in feed_dict.keys() if fnmatch.fnmatch(name, pattern)]
        return matches[0] if len(matches) == 1 else (matches if matches else None)
    return pattern if pattern in feed_dict else None

def get_feeds_for_chat(chat_id):
    """Get feeds configured for a specific Telegram chat"""
    chat_key = str(chat_id)
    feeds = feed.channel_feeds.get(chat_key)
    if feeds is not None:
        return feeds
    return {}

def get_user_key(username):
    """Get normalized user key for Telegram users"""
    if not username:
        return "unknown"
    return username.lower().replace("@", "")

def is_authorized_user(username, chat_id):
    """Check if user is authorized to manage feeds"""
    if not username:
        return False
    
    user_key = get_user_key(username)
    
    # Check if user is super admin or global admin
    is_super_admin = (user_key == admin.lower())
    is_global_admin = user_key in [a.lower() for a in admins]
    
    # For now, allow authorized users to manage feeds in any chat they have access to
    # This can be enhanced later with per-chat admin mapping
    
    return is_super_admin or is_global_admin

async def send_telegram_message_async(chat_id, message, bypass_posted_check=False):
    """Send message to Telegram chat with duplicate checking"""
    global posted_articles, telegram_bot_instance
    
    if not telegram_bot_instance:
        logging.error("Telegram bot instance not initialized.")
        return
    
    # Check for duplicate posts (extract link from message)
    link = None
    for line in message.splitlines():
        if line.startswith("Link:"):
            link = line[len("Link:"):].strip()
            break
    
    if link and not bypass_posted_check:
        chat_key = str(chat_id)
        if chat_key not in posted_articles:
            posted_articles[chat_key] = set()
        if link in posted_articles[chat_key]:
            logging.info(f"Link already posted in Telegram chat {chat_id}: {link}")
            return
        else:
            posted_articles[chat_key].add(link)
            save_posted_articles(posted_articles)
    
    try:
        await telegram_bot_instance.send_message(chat_id=chat_id, text=message, parse_mode=None)
        logging.info(f"Sent Telegram message to {chat_id}: {message[:100]}...")
    except TelegramError as e:
        logging.error(f"Failed to send Telegram message to {chat_id}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error sending Telegram message to {chat_id}: {e}")

def send_telegram_message(chat_id, message, bypass_posted_check=False):
    """Thread-safe wrapper for sending Telegram messages"""
    global telegram_application
    
    if not telegram_application:
        logging.error("Telegram application not initialized.")
        return
        
    # Create and run the async task in a thread-safe way
    try:
        # Try to get the current event loop
        try:
            loop = asyncio.get_running_loop()
            # If we're in an async context, schedule the task
            asyncio.create_task(send_telegram_message_async(chat_id, message, bypass_posted_check))
        except RuntimeError:
            # No running loop, we're likely in a different thread
            # Create a new event loop for this thread if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                # No event loop in this thread, create one
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Run the coroutine in the loop
            if not loop.is_running():
                loop.run_until_complete(send_telegram_message_async(chat_id, message, bypass_posted_check))
            else:
                # Use run_coroutine_threadsafe for thread safety
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(
                    send_telegram_message_async(chat_id, message, bypass_posted_check), 
                    loop
                )
                future.result(timeout=30)  # Wait up to 30 seconds
                
    except Exception as e:
        logging.error(f"Error scheduling Telegram message: {e}")
        # As a fallback, try using threading to run in the bot's event loop
        import threading
        
        def run_async():
            try:
                asyncio.run(send_telegram_message_async(chat_id, message, bypass_posted_check))
            except Exception as thread_error:
                logging.error(f"Fallback Telegram message failed: {thread_error}")
        
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

# Telegram Bot Command Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        f"🤖 RSS Bot is ready!\n\n"
        f"I can manage RSS feeds for this chat. Use /help to see available commands.\n\n"
        f"Chat ID: {update.effective_chat.id}"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "📡 **RSS Bot Commands**\n\n"
        "**Feed Management:**\n"
        "• `/listfeeds` - List all feeds for this chat\n"
        "• `/latest <feed_name>` - Show latest entry from a feed\n"
        "• `/search <query>` - Search for RSS feeds\n"
        "• `/getfeed <query>` - Find and show latest from a feed\n"
        "• `/stats` - Show bot statistics\n\n"
        "**Admin Commands:**\n"
        "• `/addfeed <name> <url>` - Add RSS feed to this chat\n"
        "• `/delfeed <name>` - Remove RSS feed from this chat\n"
        "• `/getadd <query>` - Search and auto-add feed\n\n"
        "**Personal Subscriptions:**\n"
        "• `/addsub <name> <url>` - Subscribe to private feed\n"
        "• `/unsub <name>` - Unsubscribe from private feed\n"
        "• `/mysubs` - List your subscriptions\n"
        "• `/latestsub <name>` - Latest from your subscription\n\n"
        "Use `!command` format for compatibility with other platforms."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages and process commands"""
    message = update.message
    if not message or not message.text:
        return
    
    text = message.text.strip()
    logging.info(f"[TELEGRAM] Raw message received: '{text}' from {message.from_user.username if message.from_user else 'unknown'}")
    
    if not (text.startswith('!') or text.startswith('/')):
        logging.info(f"[TELEGRAM] Message doesn't start with ! or /, ignoring: '{text}'")
        return
    
    # Handle both /command and /command@FightPulseBot formats
    if text.startswith('/'):
        # Only remove bot username if it's at the end (/command@FightPulseBot -> /command)
        # But preserve arguments like /listfeeds @channel
        parts = text.split(' ', 1)
        command_part = parts[0]
        args_part = parts[1] if len(parts) > 1 else ""
        
        # Remove bot username from command only
        if '@' in command_part:
            command_part = command_part.split('@')[0]
        
        # Reconstruct the full command
        text = command_part + (' ' + args_part if args_part else '')
        text = '!' + text[1:]
    
    user = message.from_user
    username = user.username if user else None
    chat_id = message.chat.id
    
    logging.info(f"Telegram command from {username} in {chat_id}: {text}")
    
    # Use centralized command handler
    from commands import handle_centralized_command
    
    async def telegram_send(target, msg):
        await send_telegram_message_async(chat_id, msg)
    
    async def telegram_send_private(user_, msg):
        # For Telegram, private messages go to the same chat since we can't DM arbitrary users
        await send_telegram_message_async(chat_id, f"@{username}: {msg}")
    
    async def telegram_send_multiline(target, msg):
        # Split very long messages for Telegram's limits
        if len(msg) > 4000:
            lines = msg.split('\n')
            current_msg = ""
            for line in lines:
                if len(current_msg) + len(line) + 1 > 4000:
                    if current_msg:
                        await send_telegram_message_async(chat_id, current_msg)
                    current_msg = line
                else:
                    if current_msg:
                        current_msg += '\n' + line
                    else:
                        current_msg = line
            if current_msg:
                await send_telegram_message_async(chat_id, current_msg)
        else:
            await send_telegram_message_async(chat_id, msg)
    
    # Check authorization
    is_authorized = is_authorized_user(username, chat_id)
    
    # Handle the command using the centralized system
    handle_centralized_command(
        "telegram",
        lambda tgt, msg: asyncio.create_task(telegram_send(tgt, msg)),
        lambda usr, msg: asyncio.create_task(telegram_send_private(usr, msg)),
        lambda tgt, msg: asyncio.create_task(telegram_send_multiline(tgt, msg)),
        username or "anonymous",
        str(chat_id),
        text,
        is_authorized
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors in the Telegram bot"""
    logging.error(f"Telegram bot error: {context.error}")

async def run_telegram_bot():
    """Main function to run the Telegram bot"""
    global telegram_bot_instance, telegram_application
    
    logging.info("Starting Telegram bot...")
    
    # Load feeds and users
    feed.load_feeds()
    users.load_users()
    
    # Load Telegram channels from channels.json
    channels_data = load_channels()
    telegram_chats = channels_data.get("telegram_channels", telegram_channels)
    
    # Initialize feeds for configured chats
    for chat in telegram_chats:
        if chat not in feed.channel_feeds:
            feed.channel_feeds[chat] = {}
    
    # Create application
    application = Application.builder().token(telegram_bot_token).build()
    telegram_application = application
    telegram_bot_instance = application.bot
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.COMMAND, handle_message))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    logging.info(f"Telegram bot configured for chats: {telegram_chats}")
    
    # Start the bot
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        logging.info("Telegram bot is running...")
        
        # Keep the bot running
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logging.error(f"Error running Telegram bot: {e}")
    finally:
        if application:
            await application.stop()

def start_telegram_bot():
    """Start the Telegram bot in an asyncio event loop"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_telegram_bot())
    except KeyboardInterrupt:
        logging.info("Telegram bot stopped by user")
    except Exception as e:
        logging.error(f"Telegram bot error: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    start_telegram_bot()