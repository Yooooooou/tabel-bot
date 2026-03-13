"""Handlers: добавление администраторов бота."""
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters,
)

import database as db
from keyboards import kb_cancel, kb_home
from states import NEW_ADMIN_ID


async def conv_newadmin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "Введите Telegram chat_id нового администратора бота:",
        reply_markup=kb_cancel()
    )
    return NEW_ADMIN_ID


async def conv_newadmin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    try:
        new_id = int(raw)
    except ValueError:
        await update.message.reply_text("Введите числовой chat_id:", reply_markup=kb_cancel())
        return NEW_ADMIN_ID
    db.add_bot_admin(new_id)
    await update.message.reply_text(
        f"✅ Администратор <code>{new_id}</code> добавлен.",
        parse_mode="HTML",
        reply_markup=kb_home()
    )
    return ConversationHandler.END


def conv_new_admin() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(conv_newadmin_start, pattern="^settings:add_admin$")],
        states={
            NEW_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_newadmin_id)],
        },
        fallbacks=[
            CommandHandler("cancel", _cmd_cancel),
            CallbackQueryHandler(_cb_cancel,   pattern="^cancel$"),
            CallbackQueryHandler(_cb_nav_home, pattern="^nav:home$"),
        ],
        per_message=False,
    )


async def _cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cmd_cancel
    return await cmd_cancel(update, context)


async def _cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cb_cancel
    return await cb_cancel(update, context)


async def _cb_nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot import cb_nav_home
    return await cb_nav_home(update, context)
