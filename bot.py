#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crystal Bot - yagona fayl (bot.py)
aiogram 3 + SQLite (aiosqlite) asosida yozilgan.

Ishga tushirish:
    pip install aiogram==3.7.0 aiosqlite --break-system-packages
    python3 bot.py

Pastdagi "SOZLAMALAR" bo'limida BOT_TOKEN va ADMIN_IDS ni to'ldiring.
"""

import sys
import asyncio
import logging
import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest



# ==================================================
# SOZLAMALAR (config.py)
# ==================================================

# config.py
# Bot sozlamalari shu yerda kiritiladi

BOT_TOKEN = "8936252736:AAHx2R9SoSJQYgYPohDA-yRxQdrBMV7Ln6M"  # @BotFather dan olingan token

# Admin(lar) Telegram ID raqami(lari). Bir nechta admin bo'lsa vergul bilan qo'shing.
ADMIN_IDS = [8487361853]  # <-- O'ZINGIZNING ID RAQAMINGIZNI YOZING

# Majburiy obuna kanali (username, @ belgisisiz ham ishlaydi, lekin @ bilan yozish tavsiya etiladi)
# Agar majburiy obuna kerak bo'lmasa None qoldiring
MANDATORY_CHANNEL = None  # masalan: "@mychannel"

# Konkurs / reyting e'lonlari yuboriladigan kanal
CONTEST_CHANNEL = None  # masalan: "@my_contest_channel"

DB_PATH = "crystal_bot.db"

# Toshkent vaqt zonasi (UTC+5)
TASHKENT_OFFSET_HOURS = 5


# ==================================================
# MA'LUMOTLAR BAZASI (database.py)
# ==================================================

import aiosqlite
import datetime


def now_tashkent() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=TASHKENT_OFFSET_HOURS)


def now_iso() -> str:
    return now_tashkent().isoformat()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            ref_crystal REAL DEFAULT 0,
            task_count INTEGER DEFAULT 0,
            bonus_count INTEGER DEFAULT 0,
            deposit REAL DEFAULT 0,
            withdraw_count INTEGER DEFAULT 0,
            daily_ref_count INTEGER DEFAULT 0,
            weekly_ref_count INTEGER DEFAULT 0,
            referred_by INTEGER,
            last_bonus_time TEXT,
            register_date TEXT,
            banned INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            added_at TEXT
        )
        """)

        # eski bazalarda "banned" ustuni bo'lmasligi mumkin, shuning uchun xavfsiz qo'shamiz
        try:
            await db.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
        except Exception:
            pass

        await db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            post_text TEXT,
            channel TEXT,
            reward REAL,
            limit_count INTEGER,
            done_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS task_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_id INTEGER,
            completed_at TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,          -- 'dogs'
            amount REAL,        -- crystal amount
            card_number TEXT,   -- DOGS uchun: foydalanuvchining TG hamyon manzili
            status TEXT DEFAULT 'pending',  -- pending / paid / cancelled
            created_at TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            status TEXT DEFAULT 'pending',  -- pending / answered
            admin_reply TEXT,
            created_at TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS weekly_contest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT,
            end_time TEXT,
            is_active INTEGER DEFAULT 0
        )
        """)

        # default settings
        defaults = {
            "referral_reward": "500",
            "daily_bonus_amount": "200",
            "dogs_rate": "1",            # 1 crystal = X DOGS
            "min_withdraw_dogs": "500",  # minimal yechish miqdori (DOGS)
            "admin_username": "@ff_coder",
            "about_text": "Crystal Bot - referral va vazifalar orqali 💎 Crystal yig'ing!",
            "last_daily_reset": "",
            "last_weekly_reset": "",
            "currency_name": "Crystal",
            "currency_emoji": "💎",
        }
        for k, v in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )

        await db.commit()


# ---------------- SETTINGS ----------------

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_currency():
    """Joriy valyuta (emoji, nom) qaytaradi. Masalan: ('💎', 'Crystal')"""
    emoji = await get_setting("currency_emoji") or "💎"
    name = await get_setting("currency_name") or "Crystal"
    return emoji, name


# ---------------- KANALLAR (majburiy obuna) ----------------

async def add_channel(username: str):
    username = username.strip()
    if not username.startswith("@"):
        username = "@" + username
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO channels (username, added_at) VALUES (?, ?)",
            (username, now_iso()),
        )
        await db.commit()


async def remove_channel(channel_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM channels WHERE id=?", (channel_id,))
        await db.commit()


async def get_channels():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM channels ORDER BY id")
        return await cur.fetchall()


# ---------------- USERS ----------------

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()


async def get_or_create_user(user_id: int, username: str, referred_by: int = None):
    user = await get_user(user_id)
    if user:
        return user, False

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users
            (user_id, username, register_date, referred_by)
            VALUES (?, ?, ?, ?)""",
            (user_id, username, now_iso(), referred_by),
        )
        await db.commit()

    user = await get_user(user_id)
    return user, True


async def update_username(user_id: int, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
        await db.commit()


# ---------------- USER BOSHQARUVI (ban / balans) ----------------

async def ban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET banned=1 WHERE user_id=?", (user_id,))
        await db.commit()


async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET banned=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def is_banned(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user:
        return False
    try:
        return bool(user["banned"])
    except Exception:
        return False


async def change_balance(user_id: int, amount: float, deposit: bool = False):
    """amount musbat bo'lsa qo'shadi, manfiy bo'lsa ayiradi"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id)
        )
        if deposit and amount > 0:
            await db.execute(
                "UPDATE users SET deposit = deposit + ? WHERE user_id=?", (amount, user_id)
            )
        await db.commit()


async def increment_task_count(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET task_count = task_count + 1 WHERE user_id=?", (user_id,)
        )
        await db.commit()


async def increment_bonus_count(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET bonus_count = bonus_count + 1, last_bonus_time=? WHERE user_id=?",
            (now_iso(), user_id),
        )
        await db.commit()


async def increment_withdraw_count(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET withdraw_count = withdraw_count + 1 WHERE user_id=?", (user_id,)
        )
        await db.commit()


async def add_referral_reward(referrer_id: int, reward: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE users SET
            balance = balance + ?,
            ref_count = ref_count + 1,
            ref_crystal = ref_crystal + ?,
            daily_ref_count = daily_ref_count + 1,
            weekly_ref_count = weekly_ref_count + 1
            WHERE user_id=?""",
            (reward, reward, referrer_id),
        )
        await db.commit()


# ---------------- TASKS ----------------

async def add_task(text, post_text, channel, reward, limit_count):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks (text, post_text, channel, reward, limit_count)
            VALUES (?, ?, ?, ?, ?)""",
            (text, post_text, channel, reward, limit_count),
        )
        await db.commit()


async def get_active_tasks():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE is_active=1 AND done_count < limit_count ORDER BY task_id"
        )
        return await cur.fetchall()


async def get_all_tasks():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks ORDER BY task_id")
        return await cur.fetchall()


async def get_task(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        return await cur.fetchone()


async def delete_task(task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
        await db.commit()


async def toggle_task(task_id: int):
    task = await get_task(task_id)
    if not task:
        return
    new_state = 0 if task["is_active"] else 1
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tasks SET is_active=? WHERE task_id=?", (new_state, task_id))
        await db.commit()
    return new_state


async def edit_task_field(task_id: int, field: str, value):
    allowed = {"text", "post_text", "channel", "reward", "limit_count"}
    if field not in allowed:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE tasks SET {field}=? WHERE task_id=?", (value, task_id))
        await db.commit()


async def has_completed_task(user_id: int, task_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM task_completions WHERE user_id=? AND task_id=?",
            (user_id, task_id),
        )
        row = await cur.fetchone()
        return row is not None


async def complete_task(user_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO task_completions (user_id, task_id, completed_at) VALUES (?, ?, ?)",
            (user_id, task_id, now_iso()),
        )
        await db.execute(
            "UPDATE tasks SET done_count = done_count + 1 WHERE task_id=?", (task_id,)
        )
        await db.commit()


# ---------------- DAILY BONUS ----------------

async def check_daily_bonus(user_id: int):
    """Returns (eligible: bool, seconds_left: int)"""
    user = await get_user(user_id)
    if not user or not user["last_bonus_time"]:
        return True, 0
    last = datetime.datetime.fromisoformat(user["last_bonus_time"])
    diff = now_tashkent() - last
    if diff >= datetime.timedelta(hours=24):
        return True, 0
    seconds_left = int((datetime.timedelta(hours=24) - diff).total_seconds())
    return False, seconds_left


# ---------------- WITHDRAWALS ----------------

async def create_withdrawal(user_id, type_, amount, card_number=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO withdrawals (user_id, type, amount, card_number, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (user_id, type_, amount, card_number, now_iso()),
        )
        await db.commit()
        return cur.lastrowid


async def get_withdrawal(withdrawal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,))
        return await cur.fetchone()


async def update_withdrawal_status(withdrawal_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, withdrawal_id))
        await db.commit()


# ---------------- COMPLAINTS ----------------

async def create_complaint(user_id, username, message):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO complaints (user_id, username, message, created_at)
            VALUES (?, ?, ?, ?)""",
            (user_id, username, message, now_iso()),
        )
        await db.commit()
        return cur.lastrowid


async def get_complaint(complaint_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM complaints WHERE id=?", (complaint_id,))
        return await cur.fetchone()


async def answer_complaint(complaint_id: int, reply: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE complaints SET admin_reply=?, status='answered' WHERE id=?",
            (reply, complaint_id),
        )
        await db.commit()


# ---------------- RATING ----------------

async def get_top_referrals(period: str, limit: int = 10):
    """period: 'daily' | 'weekly' | 'alltime'"""
    column = {
        "daily": "daily_ref_count",
        "weekly": "weekly_ref_count",
        "alltime": "ref_count",
    }[period]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT user_id, username, {column} as cnt FROM users "
            f"WHERE {column} > 0 ORDER BY {column} DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def get_user_rank(user_id: int, period: str):
    column = {
        "daily": "daily_ref_count",
        "weekly": "weekly_ref_count",
        "alltime": "ref_count",
    }[period]
    user = await get_user(user_id)
    if not user:
        return None
    my_count = user[column]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            f"SELECT COUNT(*) FROM users WHERE {column} > ?", (my_count,)
        )
        row = await cur.fetchone()
        return row[0] + 1


async def reset_daily_counters():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET daily_ref_count = 0")
        await db.commit()
    await set_setting("last_daily_reset", now_iso())


async def reset_weekly_counters():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET weekly_ref_count = 0")
        await db.commit()
    await set_setting("last_weekly_reset", now_iso())


# ---------------- WEEKLY CONTEST ----------------

async def get_active_contest():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM weekly_contest WHERE is_active=1 ORDER BY id DESC LIMIT 1"
        )
        return await cur.fetchone()


async def start_weekly_contest():
    start = now_tashkent()
    end = start + datetime.timedelta(days=7)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE weekly_contest SET is_active=0")
        await db.execute(
            "INSERT INTO weekly_contest (start_time, end_time, is_active) VALUES (?, ?, 1)",
            (start.isoformat(), end.isoformat()),
        )
        await db.commit()
    await reset_weekly_counters()
    return start, end


async def end_weekly_contest(contest_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE weekly_contest SET is_active=0 WHERE id=?", (contest_id,))
        await db.commit()


# ---------------- STATS (admin) ----------------

async def get_total_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        row = await cur.fetchone()
        return row[0]


async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def get_extended_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total_users = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM users WHERE banned=1")
        banned_users = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_balance = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COALESCE(SUM(deposit), 0) FROM users")
        total_deposit = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM tasks WHERE is_active=1")
        active_tasks = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
        pending_withdrawals = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='paid'")
        paid_withdrawals = (await cur.fetchone())[0]

        cur = await db.execute("SELECT COUNT(*) FROM complaints WHERE status='pending'")
        pending_complaints = (await cur.fetchone())[0]

        return {
            "total_users": total_users,
            "banned_users": banned_users,
            "total_balance": total_balance,
            "total_deposit": total_deposit,
            "active_tasks": active_tasks,
            "pending_withdrawals": pending_withdrawals,
            "paid_withdrawals": paid_withdrawals,
            "pending_complaints": pending_complaints,
        }


async def find_user_by_username(username: str):
    username = username.lstrip("@")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE username=?", (username,))
        return await cur.fetchone()


# ==================================================
# MODUL ALIAS (db.xxx / kb.xxx chaqiruvlari ishlashi uchun)
# ==================================================

db = kb = sys.modules[__name__]


# ==================================================
# FSM HOLATLAR (states.py)
# ==================================================

from aiogram.fsm.state import State, StatesGroup


class WithdrawStates(StatesGroup):
    waiting_wallet = State()
    waiting_dogs_amount = State()


class ComplaintStates(StatesGroup):
    waiting_text = State()


class AdminComplaintStates(StatesGroup):
    waiting_reply = State()


class AdminTaskStates(StatesGroup):
    waiting_text = State()
    waiting_post = State()
    waiting_channel = State()
    waiting_reward = State()
    waiting_limit = State()

    waiting_edit_value = State()


class AdminSettingsStates(StatesGroup):
    waiting_referral_reward = State()
    waiting_daily_bonus = State()
    waiting_dogs_rate = State()
    waiting_min_withdraw = State()
    waiting_admin_username = State()
    waiting_about_text = State()
    waiting_currency_name = State()
    waiting_currency_emoji = State()


class AdminBroadcastStates(StatesGroup):
    waiting_message = State()
    waiting_target_choice = State()
    waiting_target_id = State()
    waiting_target_message = State()


class AdminChannelStates(StatesGroup):
    waiting_channel_username = State()


class AdminUserStates(StatesGroup):
    waiting_user_id = State()
    waiting_balance_add = State()
    waiting_balance_sub = State()


# ==================================================
# KLAVIATURALAR (keyboards.py)
# ==================================================

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ---------------- REPLY KEYBOARDS ----------------

def main_menu_kb():
    kb = [
        [KeyboardButton(text="💎 Crystal ishlash"), KeyboardButton(text="💎 Crystal yechish")],
        [KeyboardButton(text="💳 Kabinet"), KeyboardButton(text="🏅 Reyting")],
        [KeyboardButton(text="📝 Murojaat"), KeyboardButton(text="⚙️ Yordam")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def crystal_ishlash_menu_kb():
    kb = [
        [KeyboardButton(text="👤 Referral yig'ish")],
        [KeyboardButton(text="🔧 Vazifa ishlash")],
        [KeyboardButton(text="🎁 Kunlik bonus")],
        [KeyboardButton(text="⬅️ Orqaga")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def crystal_yechish_menu_kb():
    kb = [
        [KeyboardButton(text="🐶 DOGS orqali yechish")],
        [KeyboardButton(text="⬅️ Orqaga")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def reyting_menu_kb():
    kb = [
        [KeyboardButton(text="🏆 Kunlik")],
        [KeyboardButton(text="🏆 Haftalik konkurs")],
        [KeyboardButton(text="🏆 Har doim")],
        [KeyboardButton(text="⬅️ Orqaga")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def yordam_menu_kb():
    kb = [
        [KeyboardButton(text="📖 Bot haqida")],
        [KeyboardButton(text="⬅️ Orqaga")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def back_kb():
    kb = [[KeyboardButton(text="⬅️ Orqaga")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def kabinet_kb():
    kb = [
        [KeyboardButton(text="💵 Balans to'ldirish")],
        [KeyboardButton(text="⬅️ Orqaga")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# ---------------- INLINE KEYBOARDS ----------------

def task_confirm_kb(task_id: int):
    kb = [[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"task_check:{task_id}")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def balans_toldirish_kb(admin_username: str):
    kb = [[InlineKeyboardButton(text="👨‍💻 Admin orqali", url=f"https://t.me/{admin_username.lstrip('@')}")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def dogs_confirm_kb():
    kb = [[InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="dogs_confirm")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_withdraw_kb(withdrawal_id: int):
    kb = [[
        InlineKeyboardButton(text="✅ To'landi", callback_data=f"wd_paid:{withdrawal_id}"),
        InlineKeyboardButton(text="❌ Bekor qilindi", callback_data=f"wd_cancel:{withdrawal_id}"),
    ]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_complaint_kb(complaint_id: int):
    kb = [[InlineKeyboardButton(text="💬 Javob berish", callback_data=f"complaint_reply:{complaint_id}")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ---------------- ADMIN PANEL ----------------

def admin_main_kb():
    kb = [
        [InlineKeyboardButton(text="💱 Valyuta", callback_data="adm_currency")],
        [InlineKeyboardButton(text="🔧 Vazifalar", callback_data="adm_tasks")],
        [InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="adm_settings")],
        [InlineKeyboardButton(text="📢 Majburiy obuna", callback_data="adm_channels")],
        [InlineKeyboardButton(text="🏆 Haftalik konkurs", callback_data="adm_contest")],
        [InlineKeyboardButton(text="🚫 User boshqaruvi", callback_data="adm_users")],
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm_stats")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_currency_kb():
    kb = [
        [InlineKeyboardButton(text="✏️ Nomini o'zgartirish (Crystal)", callback_data="cur_set_name")],
        [InlineKeyboardButton(text="✏️ Emoji o'zgartirish (💎)", callback_data="cur_set_emoji")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_channels_kb(channels):
    kb = []
    for ch in channels:
        kb.append([InlineKeyboardButton(
            text=f"🗑 {ch['username']}", callback_data=f"chan_del:{ch['id']}"
        )])
    kb.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="chan_add")])
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_broadcast_choice_kb():
    kb = [
        [InlineKeyboardButton(text="📢 Barchaga", callback_data="bc_all")],
        [InlineKeyboardButton(text="👤 Bitta userga", callback_data="bc_one")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_user_manage_kb(user_id: int, banned: bool):
    ban_text = "✅ Unban qilish" if banned else "🚫 Ban qilish"
    ban_cb = f"user_unban:{user_id}" if banned else f"user_ban:{user_id}"
    kb = [
        [InlineKeyboardButton(text="➕ Balans qo'shish", callback_data=f"user_addbal:{user_id}")],
        [InlineKeyboardButton(text="➖ Balans ayirish", callback_data=f"user_subbal:{user_id}")],
        [InlineKeyboardButton(text=ban_text, callback_data=ban_cb)],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_users")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_tasks_menu_kb():
    kb = [
        [InlineKeyboardButton(text="➕ Vazifa qo'shish", callback_data="task_add")],
        [InlineKeyboardButton(text="📋 Vazifalar ro'yxati", callback_data="task_list")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_task_item_kb(task_id: int, is_active: int):
    toggle_text = "🔴 O'chirish" if is_active else "🟢 Yoqish"
    kb = [
        [InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"task_edit:{task_id}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"task_toggle:{task_id}")],
        [InlineKeyboardButton(text="🗑 O'chirish (butunlay)", callback_data=f"task_delete:{task_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="task_list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_task_edit_field_kb(task_id: int):
    kb = [
        [InlineKeyboardButton(text="Matn", callback_data=f"tef:text:{task_id}")],
        [InlineKeyboardButton(text="Post", callback_data=f"tef:post_text:{task_id}")],
        [InlineKeyboardButton(text="Kanal", callback_data=f"tef:channel:{task_id}")],
        [InlineKeyboardButton(text="Mukofot", callback_data=f"tef:reward:{task_id}")],
        [InlineKeyboardButton(text="Limit", callback_data=f"tef:limit_count:{task_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"task_open:{task_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_settings_kb():
    kb = [
        [InlineKeyboardButton(text="👤 Referral mukofoti", callback_data="set_referral_reward")],
        [InlineKeyboardButton(text="🎁 Kunlik bonus miqdori", callback_data="set_daily_bonus")],
        [InlineKeyboardButton(text="🐶 DOGS kursi (1 💎 = X DOGS)", callback_data="set_dogs_rate")],
        [InlineKeyboardButton(text="📉 Min. DOGS yechish miqdori", callback_data="set_min_withdraw")],
        [InlineKeyboardButton(text="👨‍💻 Admin username", callback_data="set_admin_username")],
        [InlineKeyboardButton(text="📖 Bot haqida matni", callback_data="set_about_text")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_contest_kb(is_active: bool):
    if is_active:
        kb = [[InlineKeyboardButton(text="⏹ Konkursni yakunlash", callback_data="contest_end")]]
    else:
        kb = [[InlineKeyboardButton(text="▶️ Yangi konkurs boshlash", callback_data="contest_start")]]
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_main")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ==================================================
# ADMIN HANDLERLAR (handlers_admin.py)
# ==================================================

admin_router = Router()

# XAVFSIZLIK: admin_router ga kiruvchi HAR QANDAY message/callback avval shu yerda
# filtrlanadi. Bu orqali, agar kelajakda biror handlerda admin_only() tekshiruvi
# yozishni unutib qo'yishsa ham, oddiy foydalanuvchi baribir admin buyruqlariga
# hech qachon yeta olmaydi (router darajasidagi himoya - "defense in depth").
admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


def admin_only(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ---------------- ADMIN MAIN ----------------

@admin_router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not admin_only(message.from_user.id):
        return
    await state.clear()
    await message.answer("👨‍💻 Admin panel", reply_markup=kb.admin_main_kb())


@admin_router.callback_query(F.data == "adm_main")
async def adm_main(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.clear()
    await call.message.edit_text("👨‍💻 Admin panel", reply_markup=kb.admin_main_kb())
    await call.answer()


# ---------------- STATISTIKA ----------------

@admin_router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    s = await db.get_extended_stats()
    emoji, name = await db.get_currency()
    text = (
        "📊 Statistika\n\n"
        f"👤 Foydalanuvchilar soni: {s['total_users']} ta\n"
        f"🚫 Ban qilinganlar: {s['banned_users']} ta\n"
        f"{emoji} Umumiy balans: {s['total_balance']} {name}\n"
        f"💳 Umumiy kiritilgan: {s['total_deposit']} {name}\n"
        f"🔧 Faol vazifalar: {s['active_tasks']} ta\n"
        f"⏳ Kutilayotgan yechishlar: {s['pending_withdrawals']} ta\n"
        f"✅ To'langan yechishlar: {s['paid_withdrawals']} ta\n"
        f"📩 Javobsiz murojaatlar: {s['pending_complaints']} ta"
    )
    await call.message.edit_text(text, reply_markup=kb.admin_main_kb())
    await call.answer()


# ==================================================
#                    VALYUTA
# ==================================================

@admin_router.callback_query(F.data == "adm_currency")
async def adm_currency(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    emoji, name = await db.get_currency()
    await call.message.edit_text(
        f"💱 Valyuta sozlamalari\n\nHozirgi valyuta: {emoji} {name}",
        reply_markup=kb.admin_currency_kb(),
    )
    await call.answer()


@admin_router.callback_query(F.data == "cur_set_name")
async def cur_set_name(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminSettingsStates.waiting_currency_name)
    await call.message.edit_text("✏️ Valyutaning yangi nomini kiriting (masalan: Coin):")
    await call.answer()


@admin_router.message(AdminSettingsStates.waiting_currency_name)
async def cur_set_name_value(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Bo'sh bo'lishi mumkin emas.")
        return
    await db.set_setting("currency_name", name)
    await state.clear()
    await message.answer(f"✅ Valyuta nomi \"{name}\" ga o'zgartirildi.", reply_markup=kb.admin_main_kb())


@admin_router.callback_query(F.data == "cur_set_emoji")
async def cur_set_emoji(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminSettingsStates.waiting_currency_emoji)
    await call.message.edit_text("✏️ Valyutaning yangi emojisini yuboring (masalan: 🪙):")
    await call.answer()


@admin_router.message(AdminSettingsStates.waiting_currency_emoji)
async def cur_set_emoji_value(message: Message, state: FSMContext):
    emoji = message.text.strip()
    if not emoji:
        await message.answer("❌ Bo'sh bo'lishi mumkin emas.")
        return
    await db.set_setting("currency_emoji", emoji)
    await state.clear()
    await message.answer(f"✅ Valyuta emojisi \"{emoji}\" ga o'zgartirildi.", reply_markup=kb.admin_main_kb())


# ==================================================
#              MAJBURIY OBUNA KANALLARI
# ==================================================

@admin_router.callback_query(F.data == "adm_channels")
async def adm_channels(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    channels = await db.get_channels()
    text = "📢 Majburiy obuna kanallari:" if channels else "📢 Hozircha majburiy obuna kanali yo'q."
    await call.message.edit_text(text, reply_markup=kb.admin_channels_kb(channels))
    await call.answer()


@admin_router.callback_query(F.data == "chan_add")
async def chan_add(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminChannelStates.waiting_channel_username)
    await call.message.edit_text(
        "➕ Kanal usernameni kiriting (masalan @mychannel).\n\n"
        "❗️ Bot shu kanalda admin bo'lishi shart, aks holda obunani tekshira olmaydi."
    )
    await call.answer()


@admin_router.message(AdminChannelStates.waiting_channel_username)
async def chan_add_value(message: Message, state: FSMContext, bot: Bot):
    username = message.text.strip()
    if not username.startswith("@"):
        username = "@" + username

    # botning shu kanalda ishlay olishini tekshirib ko'ramiz
    try:
        await bot.get_chat(username)
    except Exception:
        await message.answer(
            "❌ Bu kanalni topib bo'lmadi yoki bot u yerda admin emas.\n"
            "Botni kanalga admin qilib qo'shing, so'ng qaytadan yuboring."
        )
        return

    await db.add_channel(username)
    await state.clear()
    channels = await db.get_channels()
    await message.answer(
        f"✅ {username} majburiy obuna ro'yxatiga qo'shildi.",
        reply_markup=kb.admin_channels_kb(channels),
    )


@admin_router.callback_query(F.data.startswith("chan_del:"))
async def chan_del(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    channel_id = int(call.data.split(":")[1])
    await db.remove_channel(channel_id)
    await call.answer("🗑 Kanal o'chirildi.", show_alert=True)
    channels = await db.get_channels()
    text = "📢 Majburiy obuna kanallari:" if channels else "📢 Hozircha majburiy obuna kanali yo'q."
    await call.message.edit_text(text, reply_markup=kb.admin_channels_kb(channels))


# ==================================================
#                 USER BOSHQARUVI
# ==================================================

@admin_router.callback_query(F.data == "adm_users")
async def adm_users(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminUserStates.waiting_user_id)
    await call.message.edit_text("🆔 Foydalanuvchi Telegram ID raqamini kiriting:")
    await call.answer()


async def _show_user_card(message: Message, user_id: int):
    user = await db.get_user(user_id)
    if not user:
        await message.answer("❌ Bunday ID li foydalanuvchi topilmadi. Qaytadan kiriting:")
        return
    emoji, cname = await db.get_currency()
    banned = bool(user["banned"]) if "banned" in user.keys() else False
    holati = "🚫 Ban qilingan" if banned else "✅ Faol"
    uname_display = f"@{user['username']}" if user["username"] else "username yo'q"
    text = (
        f"🆔 ID: {user['user_id']}\n"
        f"👤 Username: {uname_display}\n"
        f"{emoji} Balans: {user['balance']} {cname}\n"
        f"👥 Referrallar: {user['ref_count']} ta\n"
        f"📋 Vazifalar: {user['task_count']} ta\n"
        f"🌐 Yechishlar: {user['withdraw_count']} ta\n"
        f"Holati: {holati}"
    )
    await message.answer(text, reply_markup=kb.admin_user_manage_kb(user_id, banned))


@admin_router.message(AdminUserStates.waiting_user_id)
async def adm_users_id(message: Message, state: FSMContext):
    if not message.text.strip().lstrip("-").isdigit():
        await message.answer("❌ Faqat ID (raqam) kiriting.")
        return
    user_id = int(message.text.strip())
    await state.clear()
    await _show_user_card(message, user_id)


@admin_router.callback_query(F.data.startswith("user_ban:"))
async def user_ban_cb(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    user_id = int(call.data.split(":")[1])
    await db.ban_user(user_id)
    await call.answer("🚫 Foydalanuvchi ban qilindi.", show_alert=True)
    user = await db.get_user(user_id)
    emoji, cname = await db.get_currency()
    text = (
        f"🆔 ID: {user['user_id']}\n{emoji} Balans: {user['balance']} {cname}\n"
        f"Holati: 🚫 Ban qilingan"
    )
    await call.message.edit_text(text, reply_markup=kb.admin_user_manage_kb(user_id, True))


@admin_router.callback_query(F.data.startswith("user_unban:"))
async def user_unban_cb(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    user_id = int(call.data.split(":")[1])
    await db.unban_user(user_id)
    await call.answer("✅ Foydalanuvchi unban qilindi.", show_alert=True)
    user = await db.get_user(user_id)
    emoji, cname = await db.get_currency()
    text = (
        f"🆔 ID: {user['user_id']}\n{emoji} Balans: {user['balance']} {cname}\n"
        f"Holati: ✅ Faol"
    )
    await call.message.edit_text(text, reply_markup=kb.admin_user_manage_kb(user_id, False))


@admin_router.callback_query(F.data.startswith("user_addbal:"))
async def user_addbal_start(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    user_id = int(call.data.split(":")[1])
    await state.set_state(AdminUserStates.waiting_balance_add)
    await state.update_data(target_user_id=user_id)
    await call.message.edit_text(f"➕ #{user_id} hisobiga qo'shiladigan miqdorni kiriting:")
    await call.answer()


@admin_router.message(AdminUserStates.waiting_balance_add)
async def user_addbal_value(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Musbat raqam kiriting.")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await db.change_balance(user_id, amount)
    await state.clear()
    emoji, cname = await db.get_currency()
    await message.answer(f"✅ #{user_id} hisobiga {amount} {emoji} {cname} qo'shildi.")
    await _show_user_card(message, user_id)


@admin_router.callback_query(F.data.startswith("user_subbal:"))
async def user_subbal_start(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    user_id = int(call.data.split(":")[1])
    await state.set_state(AdminUserStates.waiting_balance_sub)
    await state.update_data(target_user_id=user_id)
    await call.message.edit_text(f"➖ #{user_id} hisobidan ayiriladigan miqdorni kiriting:")
    await call.answer()


@admin_router.message(AdminUserStates.waiting_balance_sub)
async def user_subbal_value(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Musbat raqam kiriting.")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await db.change_balance(user_id, -amount)
    await state.clear()
    emoji, cname = await db.get_currency()
    await message.answer(f"✅ #{user_id} hisobidan {amount} {emoji} {cname} ayirildi.")
    await _show_user_card(message, user_id)


# ==================================================
#                    VAZIFALAR
# ==================================================

@admin_router.callback_query(F.data == "adm_tasks")
async def adm_tasks(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await call.message.edit_text("🔧 Vazifalar bo'limi:", reply_markup=kb.admin_tasks_menu_kb())
    await call.answer()


@admin_router.callback_query(F.data == "task_add")
async def task_add_start(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminTaskStates.waiting_text)
    await call.message.edit_text("📝 Vazifa matnini kiriting (ichki nom uchun):")
    await call.answer()


@admin_router.message(AdminTaskStates.waiting_text)
async def task_add_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(AdminTaskStates.waiting_post)
    await message.answer("📩 Vazifa postini kiriting (userga yuboriladigan matn):")


@admin_router.message(AdminTaskStates.waiting_post)
async def task_add_post(message: Message, state: FSMContext):
    await state.update_data(post_text=message.text)
    await state.set_state(AdminTaskStates.waiting_channel)
    await message.answer("📢 Vazifa kanalini kiriting (masalan @mychannel):")


@admin_router.message(AdminTaskStates.waiting_channel)
async def task_add_channel(message: Message, state: FSMContext):
    await state.update_data(channel=message.text.strip())
    await state.set_state(AdminTaskStates.waiting_reward)
    await message.answer("💎 Mukofot miqdorini kiriting (raqam):")


@admin_router.message(AdminTaskStates.waiting_reward)
async def task_add_reward(message: Message, state: FSMContext):
    try:
        reward = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting.")
        return
    await state.update_data(reward=reward)
    await state.set_state(AdminTaskStates.waiting_limit)
    await message.answer("👥 Vazifa limitini kiriting (masalan 1000):")


@admin_router.message(AdminTaskStates.waiting_limit)
async def task_add_limit(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ Faqat butun son kiriting.")
        return
    limit_count = int(message.text.strip())
    data = await state.get_data()
    await db.add_task(data["text"], data["post_text"], data["channel"], data["reward"], limit_count)
    await state.clear()
    await message.answer("✅ Vazifa muvaffaqiyatli qo'shildi.", reply_markup=kb.admin_tasks_menu_kb())


@admin_router.callback_query(F.data == "task_list")
async def task_list(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    tasks = await db.get_all_tasks()
    if not tasks:
        await call.message.edit_text("📋 Hozircha vazifalar yo'q.", reply_markup=kb.admin_tasks_menu_kb())
        await call.answer()
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for t in tasks:
        state_icon = "🟢" if t["is_active"] else "🔴"
        rows.append([InlineKeyboardButton(
            text=f"{state_icon} #{t['task_id']} {t['text'][:25]} ({t['done_count']}/{t['limit_count']})",
            callback_data=f"task_open:{t['task_id']}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="adm_tasks")])
    await call.message.edit_text("📋 Vazifalar ro'yxati:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@admin_router.callback_query(F.data.startswith("task_open:"))
async def task_open(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    task_id = int(call.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task:
        await call.answer("Vazifa topilmadi.", show_alert=True)
        return
    holati = "🟢 Yoqilgan" if task["is_active"] else "🔴 O'chirilgan"
    text = (
        f"#{task['task_id']} vazifa\n\n"
        f"📝 Matn: {task['text']}\n"
        f"📩 Post: {task['post_text']}\n"
        f"📢 Kanal: {task['channel']}\n"
        f"💎 Mukofot: {task['reward']}\n"
        f"👥 Bajarildi: {task['done_count']} / {task['limit_count']}\n"
        f"Holati: {holati}"
    )
    await call.message.edit_text(text, reply_markup=kb.admin_task_item_kb(task_id, task["is_active"]))
    await call.answer()


@admin_router.callback_query(F.data.startswith("task_toggle:"))
async def task_toggle(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    task_id = int(call.data.split(":")[1])
    await db.toggle_task(task_id)
    await task_open(call)


@admin_router.callback_query(F.data.startswith("task_delete:"))
async def task_delete(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    task_id = int(call.data.split(":")[1])
    await db.delete_task(task_id)
    await call.answer("🗑 Vazifa o'chirildi.", show_alert=True)
    await task_list(call)


@admin_router.callback_query(F.data.startswith("task_edit:"))
async def task_edit(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    task_id = int(call.data.split(":")[1])
    await call.message.edit_text("✏️ Qaysi maydonni tahrirlaymiz?", reply_markup=kb.admin_task_edit_field_kb(task_id))
    await call.answer()


@admin_router.callback_query(F.data.startswith("tef:"))
async def task_edit_field(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    _, field, task_id = call.data.split(":")
    await state.update_data(edit_field=field, edit_task_id=int(task_id))
    await state.set_state(AdminTaskStates.waiting_edit_value)
    await call.message.edit_text(f"Yangi qiymatni kiriting ({field}):")
    await call.answer()


@admin_router.message(AdminTaskStates.waiting_edit_value)
async def task_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data["edit_field"]
    task_id = data["edit_task_id"]
    value = message.text.strip()
    if field in ("reward",):
        try:
            value = float(value)
        except ValueError:
            await message.answer("❌ Faqat raqam kiriting.")
            return
    if field in ("limit_count",):
        if not value.isdigit():
            await message.answer("❌ Faqat butun son kiriting.")
            return
        value = int(value)

    await db.edit_task_field(task_id, field, value)
    await state.clear()
    await message.answer("✅ Yangilandi.")


# ==================================================
#                    SOZLAMALAR
# ==================================================

SETTINGS_MAP = {
    "set_referral_reward": ("referral_reward", AdminSettingsStates.waiting_referral_reward, "Referral mukofoti miqdorini kiriting:"),
    "set_daily_bonus": ("daily_bonus_amount", AdminSettingsStates.waiting_daily_bonus, "Kunlik bonus miqdorini kiriting:"),
    "set_dogs_rate": ("dogs_rate", AdminSettingsStates.waiting_dogs_rate, "1 Crystal necha DOGS bo'lishini kiriting:"),
    "set_min_withdraw": ("min_withdraw_dogs", AdminSettingsStates.waiting_min_withdraw, "Minimal DOGS yechish miqdorini kiriting:"),
    "set_admin_username": ("admin_username", AdminSettingsStates.waiting_admin_username, "Admin username kiriting (masalan @admin):"),
    "set_about_text": ("about_text", AdminSettingsStates.waiting_about_text, "Bot haqida matnini kiriting:"),
}


@admin_router.callback_query(F.data == "adm_settings")
async def adm_settings(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await call.message.edit_text("⚙️ Sozlamalar:", reply_markup=kb.admin_settings_kb())
    await call.answer()


@admin_router.callback_query(F.data.in_(SETTINGS_MAP.keys()))
async def settings_prompt(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    key, target_state, prompt = SETTINGS_MAP[call.data]
    current = await db.get_setting(key)
    await state.set_state(target_state)
    await state.update_data(setting_key=key)
    await call.message.edit_text(f"{prompt}\n\nHozirgi qiymat: {current}")
    await call.answer()


@admin_router.message(AdminSettingsStates.waiting_referral_reward)
@admin_router.message(AdminSettingsStates.waiting_daily_bonus)
@admin_router.message(AdminSettingsStates.waiting_dogs_rate)
@admin_router.message(AdminSettingsStates.waiting_min_withdraw)
async def settings_numeric_value(message: Message, state: FSMContext):
    try:
        value = float(message.text.strip())
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting.")
        return
    data = await state.get_data()
    key = data["setting_key"]

    await db.set_setting(key, str(value))
    await state.clear()
    await message.answer("✅ Sozlama yangilandi.")


@admin_router.message(AdminSettingsStates.waiting_admin_username)
async def settings_admin_username(message: Message, state: FSMContext):
    value = message.text.strip()
    if not value.startswith("@"):
        value = "@" + value
    await db.set_setting("admin_username", value)
    await state.clear()
    await message.answer("✅ Admin username yangilandi.")


@admin_router.message(AdminSettingsStates.waiting_about_text)
async def settings_about_text(message: Message, state: FSMContext):
    await db.set_setting("about_text", message.text)
    await state.clear()
    await message.answer("✅ Bot haqida matni yangilandi.")


# ==================================================
#                HAFTALIK KONKURS
# ==================================================

@admin_router.callback_query(F.data == "adm_contest")
async def adm_contest(call: CallbackQuery):
    if not admin_only(call.from_user.id):
        return await call.answer()
    contest = await db.get_active_contest()
    is_active = bool(contest)
    text = "🏆 Haftalik konkurs boshqaruvi\n\n"
    if contest:
        text += f"Boshlangan: {contest['start_time']}\nTugash: {contest['end_time']}"
    else:
        text += "Hozircha faol konkurs yo'q."
    await call.message.edit_text(text, reply_markup=kb.admin_contest_kb(is_active))
    await call.answer()


@admin_router.callback_query(F.data == "contest_start")
async def contest_start(call: CallbackQuery, bot: Bot):
    if not admin_only(call.from_user.id):
        return await call.answer()
    start, end = await db.start_weekly_contest()
    await call.answer("Konkurs boshlandi!", show_alert=True)
    await adm_contest(call)

    if CONTEST_CHANNEL:
        try:
            await bot.send_message(
                CONTEST_CHANNEL,
                f"🏆 Yangi haftalik konkurs boshlandi!\n\n"
                f"⏳ Davomiyligi: 7 kun\n"
                f"🏁 Tugash sanasi: {end.strftime('%Y-%m-%d %H:%M')}\n\n"
                f"Ko'proq referral yig'ib, TOP-10ga kiring! 🎁",
            )
        except Exception:
            pass


@admin_router.callback_query(F.data == "contest_end")
async def contest_end(call: CallbackQuery, bot: Bot):
    if not admin_only(call.from_user.id):
        return await call.answer()
    contest = await db.get_active_contest()
    if not contest:
        await call.answer("Faol konkurs yo'q.", show_alert=True)
        return

    rows = await db.get_top_referrals("weekly")
    await db.end_weekly_contest(contest["id"])

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 Haftalik konkurs yakunlandi! TOP-10:", ""]
    for i, row in enumerate(rows):
        uname = row["username"] or "username_yo'q"
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {uname} | {row['cnt']} ta")
    text = "\n".join(lines) if rows else "Konkurs davomida hech kim referral yig'magan."

    await call.answer("Konkurs yakunlandi!", show_alert=True)
    await adm_contest(call)

    if CONTEST_CHANNEL:
        try:
            await bot.send_message(CONTEST_CHANNEL, text)
        except Exception:
            pass
    try:
        for admin_id in ADMIN_IDS:
            await bot.send_message(admin_id, text)
    except Exception:
        pass


# ==================================================
#                    BROADCAST
# ==================================================

@admin_router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.clear()
    await call.message.edit_text(
        "📢 Xabarni kimga yubormoqchisiz?", reply_markup=kb.admin_broadcast_choice_kb()
    )
    await call.answer()


@admin_router.callback_query(F.data == "bc_all")
async def bc_all(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminBroadcastStates.waiting_message)
    await call.message.edit_text("📢 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:")
    await call.answer()


@admin_router.message(AdminBroadcastStates.waiting_message)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_ids = await db.get_all_user_ids()
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await message.copy_to(uid)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(
        f"✅ Xabar yuborildi: {sent} ta\n❌ Yuborilmadi: {failed} ta",
        reply_markup=kb.admin_main_kb(),
    )


@admin_router.callback_query(F.data == "bc_one")
async def bc_one(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    await state.set_state(AdminBroadcastStates.waiting_target_id)
    await call.message.edit_text("🆔 Foydalanuvchi Telegram ID raqamini kiriting:")
    await call.answer()


@admin_router.message(AdminBroadcastStates.waiting_target_id)
async def bc_one_id(message: Message, state: FSMContext):
    if not message.text.strip().lstrip("-").isdigit():
        await message.answer("❌ Faqat ID (raqam) kiriting.")
        return
    target_id = int(message.text.strip())
    user = await db.get_user(target_id)
    if not user:
        await message.answer("❌ Bunday ID li foydalanuvchi topilmadi. Qaytadan kiriting:")
        return
    await state.update_data(target_id=target_id)
    await state.set_state(AdminBroadcastStates.waiting_target_message)
    await message.answer(f"✏️ #{target_id} ga yuboriladigan xabarni kiriting:")


@admin_router.message(AdminBroadcastStates.waiting_target_message)
async def bc_one_send(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data["target_id"]
    await state.clear()
    try:
        await message.copy_to(target_id)
        await message.answer("✅ Xabar yuborildi.", reply_markup=kb.admin_main_kb())
    except Exception:
        await message.answer("❌ Xabarni yuborib bo'lmadi (user botni bloklagan bo'lishi mumkin).", reply_markup=kb.admin_main_kb())


# ==================================================
#              MUROJAATGA JAVOB BERISH
# ==================================================

@admin_router.callback_query(F.data.startswith("complaint_reply:"))
async def complaint_reply_start(call: CallbackQuery, state: FSMContext):
    if not admin_only(call.from_user.id):
        return await call.answer()
    complaint_id = int(call.data.split(":")[1])
    await state.set_state(AdminComplaintStates.waiting_reply)
    await state.update_data(complaint_id=complaint_id)
    await call.message.answer(f"✏️ #{complaint_id} murojaatga javobingizni kiriting:")
    await call.answer()


@admin_router.message(AdminComplaintStates.waiting_reply)
async def complaint_reply_send(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    complaint_id = data["complaint_id"]
    complaint = await db.get_complaint(complaint_id)
    if not complaint:
        await message.answer("❌ Murojaat topilmadi.")
        await state.clear()
        return

    await db.answer_complaint(complaint_id, message.text)
    await state.clear()
    await message.answer("✅ Javob foydalanuvchiga yuborildi.")

    try:
        await bot.send_message(
            complaint["user_id"],
            f"📨 Admin javobi:\n\n{message.text}",
        )
    except Exception:
        pass


# ==================================================
# FOYDALANUVCHI HANDLERLAR (handlers_user.py)
# ==================================================

import datetime
from aiogram import Router, F, Bot, BaseMiddleware
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest


user_router = Router()


# ---------------- HELPERS ----------------

def fmt_seconds(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h} soat {m} daqiqa {s} soniya"


async def is_subscribed(bot: Bot, user_id: int) -> bool:
    if not MANDATORY_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(MANDATORY_CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except TelegramBadRequest:
        return True  # kanal topilmasa yoki xato bo'lsa, bloklamaymiz


async def get_unsubscribed_channels(bot: Bot, user_id: int):
    """Foydalanuvchi obuna bo'lmagan barcha majburiy kanallar ro'yxatini qaytaradi."""
    usernames = [ch["username"] for ch in await db.get_channels()]
    if MANDATORY_CHANNEL and MANDATORY_CHANNEL not in usernames:
        usernames.append(MANDATORY_CHANNEL)

    missing = []
    for username in usernames:
        try:
            member = await bot.get_chat_member(username, user_id)
            if member.status in ("left", "kicked"):
                missing.append(username)
        except Exception:
            # kanal topilmasa yoki bot admin bo'lmasa, o'sha kanalni bloklovchi qilib hisoblamaymiz
            continue
    return missing


def obuna_kb(channels):
    rows = []
    for username in channels:
        rows.append([InlineKeyboardButton(
            text=f"📢 {username}", url=f"https://t.me/{username.lstrip('@')}"
        )])
    rows.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def username_of(message_or_user) -> str:
    u = getattr(message_or_user, "username", None)
    return f"@{u}" if u else "username_yo'q"


# ---------------- XAVFSIZLIK: BAN VA MAJBURIY OBUNA NAZORATI ----------------
# Bu middleware user_router ga tegishli HAR BIR xabar/callback uchun ishlaydi.
# Admin buyruqlariga (admin_router) taalluqli emas, chunki u alohida router.

class UserGuardMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        if user is None or user.id in ADMIN_IDS:
            return await handler(event, data)

        # 1) BAN tekshiruvi - butun botni butunlay yopadi
        if await db.is_banned(user.id):
            text = "🚫 Siz admin tomonidan botdan foydalanishdan bloklangansiz."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                try:
                    await event.answer(text)
                except Exception:
                    pass
            return

        # 2) MAJBURIY OBUNA tekshiruvi
        is_check_cb = isinstance(event, CallbackQuery) and event.data == "check_sub"
        is_start_cmd = isinstance(event, Message) and bool(event.text) and event.text.startswith("/start")

        if not (is_check_cb or is_start_cmd):
            bot = data.get("bot")
            missing = await get_unsubscribed_channels(bot, user.id)
            if missing:
                text = "📢 Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo'ling, so'ng \"✅ Tekshirish\" tugmasini bosing:"
                markup = obuna_kb(missing)
                if isinstance(event, CallbackQuery):
                    await event.answer()
                    try:
                        await event.message.answer(text, reply_markup=markup)
                    except Exception:
                        pass
                else:
                    await event.answer(text, reply_markup=markup)
                return

        return await handler(event, data)


user_router.message.middleware(UserGuardMiddleware())
user_router.callback_query.middleware(UserGuardMiddleware())


@user_router.callback_query(F.data == "check_sub")
async def check_sub_cb(call: CallbackQuery, bot: Bot):
    missing = await get_unsubscribed_channels(bot, call.from_user.id)
    if missing:
        await call.answer("❌ Hali barcha kanallarga obuna bo'lmadingiz.", show_alert=True)
        return
    await call.answer("✅ Obuna tasdiqlandi!", show_alert=True)
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.message.answer("🏠 Asosiy menyu", reply_markup=kb.main_menu_kb())


# ---------------- /start ----------------

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = message.from_user.id
    username = username_of(message.from_user)

    referred_by = None
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
        if ref_id != user_id:
            referred_by = ref_id

    user, created = await db.get_or_create_user(user_id, username, referred_by)
    if not created:
        await db.update_username(user_id, username)

    if created and referred_by:
        subscribed = await is_subscribed(bot, user_id)
        if subscribed:
            reward = float(await db.get_setting("referral_reward"))
            referrer = await db.get_user(referred_by)
            if referrer:
                await db.add_referral_reward(referred_by, reward)
                r_emoji, r_cname = await db.get_currency()
                try:
                    await bot.send_message(
                        referred_by,
                        f"🎉 Sizning referral havolangiz orqali yangi foydalanuvchi qo'shildi!\n"
                        f"{r_emoji} +{reward} {r_cname} hisobingizga qo'shildi.",
                    )
                except Exception:
                    pass

    missing = await get_unsubscribed_channels(bot, user_id)
    if missing:
        await message.answer(
            "📢 Botdan foydalanish uchun quyidagi kanal(lar)ga obuna bo'ling, "
            "so'ng \"✅ Tekshirish\" tugmasini bosing:",
            reply_markup=obuna_kb(missing),
        )
        return

    emoji, cname = await db.get_currency()
    await message.answer(
        f"👋 Xush kelibsiz, Crystal Bot ga!\n\n"
        f"{emoji} Referral yig'ing, vazifalarni bajaring va {cname} to'plang.\n"
        f"Quyidagi menyudan foydalaning 👇",
        reply_markup=kb.main_menu_kb(),
    )


# ---------------- ORQAGA (universal) ----------------

@user_router.message(F.text == "⬅️ Orqaga", StateFilter("*"))
async def go_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 Asosiy menyu", reply_markup=kb.main_menu_kb())


# ==================================================
#                CRYSTAL ISHLASH
# ==================================================

@user_router.message(F.text == "💎 Crystal ishlash")
async def crystal_ishlash(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("💎 Crystal ishlash bo'limini tanlang:", reply_markup=kb.crystal_ishlash_menu_kb())


# ---------- Referral yig'ish ----------

@user_router.message(F.text == "👤 Referral yig'ish")
async def referral_yigish(message: Message, bot: Bot):
    me = await bot.get_me()
    user = await db.get_user(message.from_user.id)
    emoji, cname = await db.get_currency()
    link = f"https://t.me/{me.username}?start={message.from_user.id}"
    text = (
        "👥 Referral yig'ish\n\n"
        "Do'stlaringizni botga taklif qiling va har bir muvaffaqiyatli referral uchun "
        f"{emoji} {cname} mukofotiga ega bo'ling.\n\n"
        f"🔗 Sizning referral havolangiz:\n{link}\n\n"
        f"👤 Referral soni: {user['ref_count']} ta\n"
        f"{emoji} Referral orqali topilgan: {user['ref_crystal']} {cname}\n\n"
        "Do'stlaringiz havolangiz orqali botga kirib, majburiy obunadan muvaffaqiyatli "
        "o'tgandan keyingina mukofot avtomatik qo'shiladi."
    )
    await message.answer(text)


# ---------- Vazifa ishlash ----------

@user_router.message(F.text == "🔧 Vazifa ishlash")
async def vazifa_ishlash(message: Message):
    await send_next_task(message.from_user.id, message)


async def send_next_task(user_id: int, message: Message):
    tasks = await db.get_active_tasks()
    for task in tasks:
        completed = await db.has_completed_task(user_id, task["task_id"])
        if not completed:
            text = (
                f"{task['post_text']}\n\n"
                f"👥 Bajarildi: {task['done_count']} / {task['limit_count']}"
            )
            await message.answer(text, reply_markup=kb.task_confirm_kb(task["task_id"]))
            return
    await message.answer("✅ Hozircha bajarilishi mumkin bo'lgan vazifa yo'q. Keyinroq qayta urinib ko'ring.")


@user_router.callback_query(F.data.startswith("task_check:"))
async def task_check(call: CallbackQuery, bot: Bot):
    task_id = int(call.data.split(":")[1])
    user_id = call.from_user.id
    task = await db.get_task(task_id)

    if not task or not task["is_active"] or task["done_count"] >= task["limit_count"]:
        await call.message.edit_text("❌ Bu vazifa endi mavjud emas.")
        await call.answer()
        return

    already = await db.has_completed_task(user_id, task_id)
    if already:
        await call.answer("Siz bu vazifani allaqachon bajargansiz.", show_alert=True)
        return

    subscribed = True
    channel = task["channel"]
    if channel:
        try:
            member = await bot.get_chat_member(channel, user_id)
            subscribed = member.status not in ("left", "kicked")
        except TelegramBadRequest:
            subscribed = False

    if not subscribed:
        await call.message.edit_text(
            "❌ Vazifa bajarilmagan.\n\nShu vazifani qayta bajarishingiz kerak.",
            reply_markup=kb.task_confirm_kb(task_id),
        )
        await call.answer()
        return

    await db.complete_task(user_id, task_id)
    reward = task["reward"]
    await db.change_balance(user_id, reward)
    await db.increment_task_count(user_id)
    emoji, cname = await db.get_currency()

    await call.message.edit_text(
        f"✅ Vazifa muvaffaqiyatli bajarildi.\n\n"
        f"{emoji} {reward} {cname} avtomatik hisobiga qo'shildi."
    )
    await call.answer()

    await send_next_task(user_id, call.message)


# ---------- Kunlik bonus ----------

@user_router.message(F.text == "🎁 Kunlik bonus")
async def kunlik_bonus(message: Message):
    user_id = message.from_user.id
    eligible, seconds_left = await db.check_daily_bonus(user_id)
    if eligible:
        amount = float(await db.get_setting("daily_bonus_amount"))
        await db.change_balance(user_id, amount)
        await db.increment_bonus_count(user_id)
        emoji, cname = await db.get_currency()
        await message.answer(
            f"🎉 Tabriklaymiz!\n\nHisobingizga {amount} {emoji} {cname} qo'shildi."
        )
    else:
        await message.answer(
            "❌ 24 soat hali tugamagan.\n\n"
            f"⏳ Keyingi bonusgacha qolgan vaqt: {fmt_seconds(seconds_left)}"
        )


# ==================================================
#                CRYSTAL YECHISH
# ==================================================

@user_router.message(F.text == "💎 Crystal yechish")
async def crystal_yechish(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("💎 Crystal yechish usulini tanlang:", reply_markup=kb.crystal_yechish_menu_kb())


# ---------- DOGS orqali ----------

@user_router.message(F.text == "🐶 DOGS orqali yechish")
async def dogs_orqali(message: Message, state: FSMContext):
    await state.set_state(WithdrawStates.waiting_wallet)
    await message.answer(
        "🐶 DOGS ni qabul qiladigan TG hamyon manzilingizni kiriting:",
        reply_markup=kb.back_kb(),
    )


@user_router.message(WithdrawStates.waiting_wallet)
async def get_wallet(message: Message, state: FSMContext):
    wallet = message.text.strip()
    if len(wallet) < 5:
        await message.answer("❌ Hamyon manzili noto'g'ri. Qaytadan kiriting:")
        return

    await state.update_data(wallet=wallet)

    rate = float(await db.get_setting("dogs_rate"))
    min_withdraw = float(await db.get_setting("min_withdraw_dogs"))
    emoji, cname = await db.get_currency()

    await state.set_state(WithdrawStates.waiting_dogs_amount)
    await message.answer(
        f"Hozirgi kurs:\n1 {emoji} = {rate} DOGS\n\n"
        f"Minimal yechish:\n{min_withdraw} DOGS\n\n"
        f"Yechmoqchi bo'lgan {cname} miqdorini kiriting."
    )


@user_router.message(WithdrawStates.waiting_dogs_amount)
async def get_dogs_amount(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip().replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        await message.answer("❌ Iltimos, faqat raqam kiriting.")
        return

    rate = float(await db.get_setting("dogs_rate"))
    min_withdraw_dogs = float(await db.get_setting("min_withdraw_dogs"))
    emoji, cname = await db.get_currency()
    dogs_amount = amount * rate

    if dogs_amount < min_withdraw_dogs:
        await message.answer(
            f"❌ Minimal yechish miqdori {min_withdraw_dogs} DOGS "
            f"(≈{min_withdraw_dogs / rate:.2f} {emoji}) dan kam bo'lmasligi kerak."
        )
        return

    user = await db.get_user(message.from_user.id)
    if user["balance"] < amount:
        await message.answer(f"❌ Hisobingizda yetarli {cname} mavjud emas.")
        return

    data = await state.get_data()
    wallet = data.get("wallet")

    await db.change_balance(message.from_user.id, -amount)
    withdrawal_id = await db.create_withdrawal(message.from_user.id, "dogs", amount, wallet)
    await db.increment_withdraw_count(message.from_user.id)
    await state.clear()

    await message.answer(
        "✅ Arizangiz qabul qilindi. Admin tasdiqlashini kuting.",
        reply_markup=kb.main_menu_kb(),
    )

    username = username_of(message.from_user)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🐶 Yangi yechish arizasi (DOGS)\n\n"
                f"👤 User: {username}\n"
                f"🆔 ID: {message.from_user.id}\n"
                f"👛 Hamyon: {wallet}\n"
                f"{emoji} Miqdor: {amount} {cname}\n"
                f"🐶 DOGS: {dogs_amount:.2f}",
                reply_markup=kb.admin_withdraw_kb(withdrawal_id),
            )
        except Exception:
            pass


# ---------- Admin: to'landi / bekor qilindi ----------

@user_router.callback_query(F.data.startswith("wd_paid:"))
async def wd_paid(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Ruxsat yo'q.", show_alert=True)
        return
    withdrawal_id = int(call.data.split(":")[1])
    withdrawal = await db.get_withdrawal(withdrawal_id)
    if not withdrawal or withdrawal["status"] != "pending":
        await call.answer("Bu ariza allaqachon ko'rilgan.", show_alert=True)
        return

    await db.update_withdrawal_status(withdrawal_id, "paid")
    await call.message.edit_text(call.message.text + "\n\n✅ TO'LANDI")
    await call.answer()
    try:
        await bot.send_message(withdrawal["user_id"], "✅ Sizning yechish arizangiz bo'yicha to'lov amalga oshirildi.")
    except Exception:
        pass


@user_router.callback_query(F.data.startswith("wd_cancel:"))
async def wd_cancel(call: CallbackQuery, bot: Bot):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Ruxsat yo'q.", show_alert=True)
        return
    withdrawal_id = int(call.data.split(":")[1])
    withdrawal = await db.get_withdrawal(withdrawal_id)
    if not withdrawal or withdrawal["status"] != "pending":
        await call.answer("Bu ariza allaqachon ko'rilgan.", show_alert=True)
        return

    await db.update_withdrawal_status(withdrawal_id, "cancelled")
    await db.change_balance(withdrawal["user_id"], withdrawal["amount"])
    emoji, cname = await db.get_currency()
    await call.message.edit_text(call.message.text + f"\n\n❌ BEKOR QILINDI ({cname} qaytarildi)")
    await call.answer()
    try:
        await bot.send_message(
            withdrawal["user_id"],
            f"❌ Sizning yechish arizangiz bekor qilindi.\n{emoji} {withdrawal['amount']} {cname} hisobingizga qaytarildi.",
        )
    except Exception:
        pass


# ==================================================
#                     KABINET
# ==================================================

@user_router.message(F.text == "💳 Kabinet")
async def kabinet(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    reg_date = user["register_date"]
    try:
        reg_date = datetime.datetime.fromisoformat(reg_date).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    emoji, cname = await db.get_currency()
    text = (
        f"🆔 ID raqamingiz: {user['user_id']}\n\n"
        f"💵 Balansingiz: {user['balance']} {emoji}\n\n"
        f"🌐 Yechib olishlar: {user['withdraw_count']} ta\n\n"
        f"👤 Referralaringiz soni: {user['ref_count']} ta\n\n"
        f"{emoji} Referral orqali topilgan: {user['ref_crystal']} {emoji}\n\n"
        f"📋 Bajarilgan vazifalar: {user['task_count']} ta\n\n"
        f"🎁 Olingan kunlik bonuslar: {user['bonus_count']} ta\n\n"
        f"📅 Ro'yxatdan o'tgan sana: {reg_date}\n\n"
        f"💳 Kiritgan pullaringiz: {user['deposit']} {emoji}"
    )
    await message.answer(text, reply_markup=kb.kabinet_kb())


@user_router.message(F.text == "💵 Balans to'ldirish")
async def balans_toldirish(message: Message):
    admin_username = await db.get_setting("admin_username")
    await message.answer(
        "Quyidagilardan birini tanlang. 👇",
        reply_markup=kb.balans_toldirish_kb(admin_username),
    )


# ==================================================
#                     REYTING
# ==================================================

@user_router.message(F.text == "🏅 Reyting")
async def reyting(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏆 Reyting turini tanlang:", reply_markup=kb.reyting_menu_kb())


def render_top(rows, title):
    medals = ["🥇", "🥈", "🥉"]
    lines = [title, ""]
    if not rows:
        lines.append("Hozircha ma'lumot yo'q.")
    for i, row in enumerate(rows):
        uname = row["username"] or "username_yo'q"
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {uname} | {row['cnt']} ta")
    return "\n".join(lines)


@user_router.message(F.text == "🏆 Kunlik")
async def reyting_kunlik(message: Message):
    rows = await db.get_top_referrals("daily")
    rank = await db.get_user_rank(message.from_user.id, "daily")
    text = render_top(rows, "🏆 Kunlik reyting (Toshkent vaqti 00:00 dan)")
    text += f"\n\n📍 Sizning o'rningiz: {rank}"
    await message.answer(text)


@user_router.message(F.text == "🏆 Haftalik konkurs")
async def reyting_haftalik(message: Message):
    contest = await db.get_active_contest()
    if not contest:
        await message.answer("❌ Hali yangi haftalik konkurs boshlanmadi.\n\nBoshlanganda kanalga e'lon beriladi.")
        return

    rows = await db.get_top_referrals("weekly")
    rank = await db.get_user_rank(message.from_user.id, "weekly")
    end_time = contest["end_time"]
    text = render_top(rows, f"🏆 Haftalik konkurs\n⏳ Tugash vaqti: {end_time}")
    text += f"\n\n📍 Sizning o'rningiz: {rank}"
    await message.answer(text)


@user_router.message(F.text == "🏆 Har doim")
async def reyting_har_doim(message: Message):
    rows = await db.get_top_referrals("alltime")
    rank = await db.get_user_rank(message.from_user.id, "alltime")
    text = render_top(rows, "🏆 Har doimgi reyting")
    text += f"\n\n📍 Sizning o'rningiz: {rank}"
    await message.answer(text)


# ==================================================
#                     MUROJAAT
# ==================================================

@user_router.message(F.text == "📝 Murojaat")
async def murojaat(message: Message, state: FSMContext):
    await state.set_state(ComplaintStates.waiting_text)
    await message.answer("📝 Murojaat matnini yozing.", reply_markup=kb.back_kb())


@user_router.message(ComplaintStates.waiting_text)
async def murojaat_matn(message: Message, state: FSMContext, bot: Bot):
    complaint_id = await db.create_complaint(
        message.from_user.id, username_of(message.from_user), message.text
    )
    await state.clear()
    await message.answer("✅ Murojaatingiz adminga yuborildi.", reply_markup=kb.main_menu_kb())

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📩 Yangi murojaat\n\n"
                f"👤 User: {username_of(message.from_user)}\n"
                f"🆔 ID: {message.from_user.id}\n\n"
                f"📝 Xabar:\n{message.text}",
                reply_markup=kb.admin_complaint_kb(complaint_id),
            )
        except Exception:
            pass


# ==================================================
#                     YORDAM
# ==================================================

@user_router.message(F.text == "⚙️ Yordam")
async def yordam(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⚙️ Yordam bo'limi:", reply_markup=kb.yordam_menu_kb())


@user_router.message(F.text == "📖 Bot haqida")
async def bot_haqida(message: Message):
    text = await db.get_setting("about_text")
    await message.answer(text)


# ==================================================
# SCHEDULER (kunlik/haftalik avtomatik vazifalar)
# ==================================================

import asyncio
import datetime
from aiogram import Bot



def render_top_text(rows, title):
    medals = ["🥇", "🥈", "🥉"]
    lines = [title, ""]
    if not rows:
        lines.append("Bugun hech kim referral yig'magan.")
    for i, row in enumerate(rows):
        uname = row["username"] or "username_yo'q"
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {uname} | {row['cnt']} ta")
    return "\n".join(lines)


async def daily_reset_loop(bot: Bot):
    """Har kuni Toshkent vaqti 00:00 da ishga tushadi:
    - kunlik TOP-10 ni admin va konkurs kanaliga yuboradi
    - kunlik hisoblagichlarni tozalaydi
    """
    while True:
        now = db.now_tashkent()
        next_midnight = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_seconds = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        try:
            rows = await db.get_top_referrals("daily")
            text = render_top_text(rows, "🏆 Kunlik TOP-10 (yakunlandi)")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, text)
                except Exception:
                    pass
            if CONTEST_CHANNEL:
                try:
                    await bot.send_message(CONTEST_CHANNEL, text)
                except Exception:
                    pass
            await db.reset_daily_counters()
        except Exception:
            pass


async def weekly_contest_watch_loop(bot: Bot):
    """Har 10 daqiqada faol konkurs muddati tugaganmi tekshiradi,
    tugagan bo'lsa avtomatik yakunlaydi va TOP-10 yuboradi.
    """
    while True:
        await asyncio.sleep(600)
        try:
            contest = await db.get_active_contest()
            if not contest:
                continue
            end_time = datetime.datetime.fromisoformat(contest["end_time"])
            if db.now_tashkent() >= end_time:
                rows = await db.get_top_referrals("weekly")
                text = render_top_text(rows, "🏆 Haftalik konkurs yakunlandi! TOP-10:")
                await db.end_weekly_contest(contest["id"])
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, text)
                    except Exception:
                        pass
                if CONTEST_CHANNEL:
                    try:
                        await bot.send_message(CONTEST_CHANNEL, text)
                    except Exception:
                        pass
        except Exception:
            pass


def start_background_tasks(bot: Bot):
    asyncio.create_task(daily_reset_loop(bot))
    asyncio.create_task(weekly_contest_watch_loop(bot))


# ==================================================
# ASOSIY ISHGA TUSHIRISH FUNKSIYASI (main.py)
# ==================================================

async def main():
    logging.basicConfig(level=logging.INFO)

    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin_router)
    dp.include_router(user_router)

    start_background_tasks(bot)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
