# -*- coding: utf-8 -*-
"""
Telegram Game Top‑Up Bot (TeleBot version)
==========================================

• Framework: pyTelegramBotAPI (telebot)
• Storage  : SQLite (sqlite3)
• Runtime  : Compatible with Pydroid3 (Android) and desktop Python 3.9+

Features
--------
- Main menu for users (browse products, create top‑up order, send payment proof, track order, help).
- Admin panel (add product, manage products, list/review pending orders, accept/reject, view details).
- Inline keyboards with callback_data routing.
- Simple FSM implemented with in‑memory state map per user.
- Notifications to admins when new order / proof arrives.
- Safe DB schema with CHECK constraints and timestamps.
- Resilient error handling and chat‑friendly messages in Arabic.

IMPORTANT
---------
This file embeds the bot token and an admin ID because the user requested a version
with configuration inside the code. Replace TOKEN and ADMIN_IDS with your own values
when you deploy to production.

Tested with: pyTelegramBotAPI==4.14.0

"""

from __future__ import annotations

import os
import sys
import time
import math
import json
import sqlite3
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import telebot
from telebot import types

# ===================== الإعدادات الثابتة (حسب طلبك) =====================
# ❗ استبدل التوكن إن لزم — هذا تمت إضافته بطلبك ليكون داخل الكود.
TOKEN: str = "8279643165:AAHVi0naorcKQl2CDZ8tpOGE4Tz5lP1yMbc"
# يمكنك إضافة أكثر من آي دي أدمن هنا
ADMIN_IDS: List[int] = [8378812991]

# اسم ملف قاعدة البيانات
DB_PATH = "data.db"

# تفعيل/تعطيل رسائل التصحيح في وحدة التحكم
DEBUG = True

# إنشاء كائن البوت
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ===================== أدوات مساعدة عامة =====================

def log(msg: str) -> None:
    if DEBUG:
        print(msg)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# Format helpers --------------------------------------------------------------

def money(v: float) -> str:
    try:
        return f"{v:.2f}$"
    except Exception:
        return str(v)


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ===================== قاعدة البيانات =====================

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    lang        TEXT DEFAULT 'ar'
);

CREATE TABLE IF NOT EXISTS products (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    price   REAL NOT NULL CHECK(price >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    qty             INTEGER NOT NULL CHECK(qty > 0),
    total           REAL NOT NULL CHECK(total >= 0),
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/accepted/rejected
    payment_file_id TEXT,                             -- photo/document file_id
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(user_id)    REFERENCES users(user_id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
"""


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_init() -> None:
    conn = db_connect()
    cur = conn.cursor()
    cur.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    log("[DB] schema ready")


# CRUD helpers ---------------------------------------------------------------

def db_execute(sql: str, params: Tuple = ()) -> int:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def db_fetchone(sql: str, params: Tuple = ()) -> Optional[sqlite3.Row]:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    conn.close()
    return row


def db_fetchall(sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


# ===================== إدارة الواجهات (لوحات الأزرار) =====================

# ملاحظة: telebot لا يحتوي مُنشئ صفوف جاهز مثل aiogram، لذا سنبنيها يدويًا


def kb_main(is_admin_flag: bool) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🎮 قائمة الألعاب والأسعار", callback_data="user:list_products"),
        types.InlineKeyboardButton("🧾 إنشاء طلب شحن", callback_data="user:new_order"),
        types.InlineKeyboardButton("📸 إرسال إثبات الدفع", callback_data="user:send_proof"),
        types.InlineKeyboardButton("🔎 تتبّع حالة الطلب", callback_data="user:track_order"),
        types.InlineKeyboardButton("ℹ️ مساعدة", callback_data="user:help"),
    )
    if is_admin_flag:
        kb.add(types.InlineKeyboardButton("🛠️ لوحة الأدمن", callback_data="admin:panel"))
    return kb


def kb_back(cb: str = "back:main") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data=cb))
    return kb


def kb_admin_panel() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("➕ إضافة منتج", callback_data="admin:add_product"),
        types.InlineKeyboardButton("🗂️ إدارة المنتجات", callback_data="admin:manage_products"),
        types.InlineKeyboardButton("📬 الطلبات المعلّقة", callback_data="admin:list_pending"),
        types.InlineKeyboardButton("⬅️ رجوع", callback_data="back:main"),
    )
    return kb


def kb_product_actions(pid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("✏️ تعديل السعر", callback_data=f"admin:edit_price:{pid}"),
        types.InlineKeyboardButton("🗑️ حذف", callback_data=f"admin:delete_product:{pid}"),
        types.InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel"),
    )
    return kb


def kb_order_review(oid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ قبول", callback_data=f"admin:accept:{oid}"),
        types.InlineKeyboardButton("❌ رفض", callback_data=f"admin:reject:{oid}"),
    )
    kb.add(
        types.InlineKeyboardButton("📄 تفاصيل", callback_data=f"admin:details:{oid}"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data="admin:list_pending"))
    return kb


def kb_products_list() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    rows = db_fetchall("SELECT id, name, price FROM products ORDER BY id DESC")
    if not rows:
        kb.add(types.InlineKeyboardButton("لا توجد منتجات بعد", callback_data="noop"))
    else:
        for r in rows:
            kb.add(types.InlineKeyboardButton(f"{r['name']} — {money(r['price'])}", callback_data=f"user:product:{r['id']}"))
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data="back:main"))
    return kb


# ===================== حالة المستخدم (FSM مبسّط) =====================

class State:
    NONE = "none"
    NEW_ORDER_WAIT_PRODUCT = "new_order_wait_product"
    NEW_ORDER_WAIT_QTY = "new_order_wait_qty"
    NEW_ORDER_CONFIRM = "new_order_confirm"
    ADD_PRODUCT_WAIT_NAME = "add_product_wait_name"
    ADD_PRODUCT_WAIT_PRICE = "add_product_wait_price"
    EDIT_PRICE_WAIT_VALUE = "edit_price_wait_value"
    TRACK_WAIT_ID = "track_wait_id"
    SENDPROOF_WAIT_ORDER_ID = "sendproof_wait_order_id"
    SENDPROOF_WAIT_MEDIA = "sendproof_wait_media"


@dataclass
class UserSession:
    state: str = State.NONE
    data: Dict[str, any] = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}


SESSIONS: Dict[int, UserSession] = {}


def get_session(user_id: int) -> UserSession:
    sess = SESSIONS.get(user_id)
    if not sess:
        sess = UserSession()
        SESSIONS[user_id] = sess
    return sess


def clear_session(user_id: int) -> None:
    SESSIONS[user_id] = UserSession()


# ===================== وظائف بيانات (منتجات/طلبات) =====================

# Users

def ensure_user(user_id: int, username: str, first_name: str) -> None:
    db_execute(
        "INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES(?,?,?)",
        (user_id, username or "", first_name or ""),
    )


# Products

def product_get(pid: int) -> Optional[sqlite3.Row]:
    return db_fetchone("SELECT id,name,price FROM products WHERE id=?", (pid,))


def product_add(name: str, price: float) -> int:
    return db_execute("INSERT INTO products(name, price) VALUES(?,?)", (name, price))


def product_edit_price(pid: int, price: float) -> None:
    db_execute("UPDATE products SET price=? WHERE id=?", (price, pid))


def product_delete(pid: int) -> None:
    db_execute("DELETE FROM products WHERE id=?", (pid,))


# Orders

def order_create(user_id: int, product_id: int, qty: int) -> int:
    prod = product_get(product_id)
    if not prod:
        raise ValueError("product not found")
    total = float(prod["price"]) * qty
    return db_execute(
        "INSERT INTO orders(user_id, product_id, qty, total, status) VALUES(?,?,?,?, 'pending')",
        (user_id, product_id, qty, total),
    )


def order_get(oid: int) -> Optional[sqlite3.Row]:
    return db_fetchone(
        """
        SELECT o.id, o.user_id, o.product_id, o.qty, o.total, o.status,
               o.payment_file_id, o.created_at, o.updated_at,
               p.name as product_name, p.price as unit_price
        FROM orders o JOIN products p ON o.product_id = p.id
        WHERE o.id = ?
        """,
        (oid,),
    )


def orders_pending_ids() -> List[int]:
    rows = db_fetchall("SELECT id FROM orders WHERE status='pending' ORDER BY id DESC")
    return [int(r["id"]) for r in rows]


def order_set_status(oid: int, status: str) -> Optional[int]:
    row = db_fetchone("SELECT user_id FROM orders WHERE id=?", (oid,))
    if not row:
        return None
    user_id = int(row["user_id"])
    db_execute("UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?", (status, oid))
    return user_id


def order_set_payment_file(oid: int, file_id: str) -> None:
    db_execute(
        "UPDATE orders SET payment_file_id=?, updated_at=datetime('now') WHERE id=?",
        (file_id, oid),
    )


# ===================== إشعارات الأدمن =====================

def notify_admins(text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None) -> None:
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, text, reply_markup=reply_markup)
        except Exception as e:
            log(f"[WARN] notify admin {aid} failed: {e}")


# ===================== الأوامر العامة =====================

@bot.message_handler(commands=["start"])
def cmd_start(msg: types.Message):
    ensure_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name)
    clear_session(msg.from_user.id)
    bot.send_message(
        msg.chat.id,
        (
            "👋 أهلاً بك!\n\n"
            "أنا بوت شحن الألعاب. اختر من القائمة بالأسفل:\n"
            "- تصفّح الألعاب والأسعار\n"
            "- إنشاء طلب شحن\n"
            "- إرسال إثبات الدفع\n"
            "- تتبّع حالة طلبك\n"
        ),
        reply_markup=kb_main(is_admin(msg.from_user.id)),
    )


@bot.message_handler(commands=["help"])
def cmd_help(msg: types.Message):
    bot.send_message(
        msg.chat.id,
        (
            "ℹ️ للمساعدة:\n"
            "1) اطلع على الأسعار من \"قائمة الألعاب والأسعار\".\n"
            "2) أنشئ طلب شحن وحدد الكمية.\n"
            "3) حوّل المبلغ ثم أرسل صورة/ملف لإثبات الدفع مع رقم الطلب.\n"
            "4) ستصلك نتيجة الطلب: قبول ✅ أو رفض ❌.\n"
        ),
        reply_markup=kb_main(is_admin(msg.from_user.id)),
    )


# ===================== مسارات المستخدم (Callback) =====================

@bot.callback_query_handler(func=lambda c: c.data == "user:list_products")
def cq_user_list_products(cq: types.CallbackQuery):
    kb = kb_products_list()
    try:
        bot.edit_message_text(
            "🛍️ الألعاب المتاحة وأسعارها:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb,
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "🛍️ الألعاب المتاحة وأسعارها:", reply_markup=kb)
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data == "user:new_order")
def cq_user_new_order(cq: types.CallbackQuery):
    sess = get_session(cq.from_user.id)
    sess.state = State.NEW_ORDER_WAIT_PRODUCT
    kb = kb_products_list()
    try:
        bot.edit_message_text(
            "اختر اللعبة المطلوبة:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb,
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "اختر اللعبة المطلوبة:", reply_markup=kb)
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("user:product:"))
def cq_select_product(cq: types.CallbackQuery):
    sess = get_session(cq.from_user.id)
    if sess.state not in (State.NEW_ORDER_WAIT_PRODUCT, State.NEW_ORDER_WAIT_QTY):
        bot.answer_callback_query(cq.id, "ابدأ من إنشاء طلب")
        return
    try:
        pid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "خطأ في المنتج")
        return
    prod = product_get(pid)
    if not prod:
        bot.answer_callback_query(cq.id, "المنتج غير موجود", show_alert=True)
        return
    sess.state = State.NEW_ORDER_WAIT_QTY
    sess.data["product_id"] = pid
    kb = kb_back("user:new_order")
    bot.edit_message_text(
        (
            f"🔢 أدخل الكمية المطلوبة لـ <b>{prod['name']}</b>\n"
            f"السعر للوحدة: <b>{money(prod['price'])}</b>\n\n"
            "أرسل رقماً فقط."
        ),
        chat_id=cq.message.chat.id,
        message_id=cq.message.message_id,
        reply_markup=kb,
    )
    bot.answer_callback_query(cq.id)


@bot.message_handler(func=lambda m: get_session(m.from_user.id).state == State.NEW_ORDER_WAIT_QTY)
def msg_qty_entered(msg: types.Message):
    sess = get_session(msg.from_user.id)
    text = (msg.text or "").strip()
    if not text.isdigit():
        bot.send_message(msg.chat.id, "❌ الرجاء إرسال رقم صحيح للكمية.")
        return
    qty = int(text)
    if qty <= 0 or qty > 10000:
        bot.send_message(msg.chat.id, "❌ الكمية غير منطقية.")
        return
    pid = int(sess.data.get("product_id", 0))
    prod = product_get(pid)
    if not prod:
        clear_session(msg.from_user.id)
        bot.send_message(msg.chat.id, "حدث خطأ: المنتج لم يعد موجوداً.")
        return
    total = float(prod["price"]) * qty
    sess.state = State.NEW_ORDER_CONFIRM
    sess.data["qty"] = qty
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ تأكيد الطلب", callback_data="user:confirm_order"))
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data="user:new_order"))
    bot.send_message(
        msg.chat.id,
        (
            f"📦 تأكيد الطلب:\n\n"
            f"اللعبة: <b>{prod['name']}</b>\n"
            f"الكمية: <b>{qty}</b>\n"
            f"الإجمالي: <b>{money(total)}</b>\n\n"
            "اضغط تأكيد لإرسال الطلب المعلّق."
        ),
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data == "user:confirm_order")
def cq_confirm_order(cq: types.CallbackQuery):
    sess = get_session(cq.from_user.id)
    if sess.state != State.NEW_ORDER_CONFIRM:
        bot.answer_callback_query(cq.id, "لا يوجد طلب للتأكيد")
        return
    pid = int(sess.data.get("product_id", 0))
    qty = int(sess.data.get("qty", 0))
    try:
        oid = order_create(cq.from_user.id, pid, qty)
    except Exception as e:
        log(f"[ERR] create order: {e}")
        bot.answer_callback_query(cq.id, "فشل إنشاء الطلب", show_alert=True)
        return
    order = order_get(oid)
    clear_session(cq.from_user.id)

    # إشعار الأدمن
    try:
        notify_admins(
            (
                f"🚨 طلب جديد #{oid}\n"
                f"المستخدم: <a href='tg://user?id={cq.from_user.id}'>{cq.from_user.first_name}</a> ({cq.from_user.id})\n"
                f"اللعبة: {order['product_name']}\n"
                f"الكمية: {order['qty']}\n"
                f"الإجمالي: {money(order['total'])}\n"
                f"الحالة: {order['status']}\n"
                f"التاريخ: {order['created_at']}"
            ),
            kb_order_review(oid),
        )
    except Exception as e:
        log(f"[WARN] notify admins failed: {e}")

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📸 إرسال إثبات الدفع", callback_data="user:send_proof"))
    kb.add(types.InlineKeyboardButton("🔎 تتبّع حالة الطلب", callback_data="user:track_order"))
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data="back:main"))

    try:
        bot.edit_message_text(
            f"✅ تم إنشاء طلبك بنجاح!\nرقم الطلب: <b>#{oid}</b>\nالرجاء تحويل المبلغ ثم إرسال إثبات الدفع مرفقاً برقم الطلب.",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb,
        )
    except Exception:
        bot.send_message(
            cq.message.chat.id,
            f"✅ تم إنشاء طلبك بنجاح!\nرقم الطلب: <b>#{oid}</b>\nالرجاء تحويل المبلغ ثم إرسال إثبات الدفع مرفقاً برقم الطلب.",
            reply_markup=kb,
        )
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data == "user:send_proof")
def cq_send_proof_entry(cq: types.CallbackQuery):
    sess = get_session(cq.from_user.id)
    sess.state = State.SENDPROOF_WAIT_ORDER_ID
    try:
        bot.edit_message_text(
            "رجاء أرسل رقم الطلب (مثال: 123)",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_back("back:main"),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "رجاء أرسل رقم الطلب (مثال: 123)", reply_markup=kb_back("back:main"))
    bot.answer_callback_query(cq.id)


@bot.message_handler(func=lambda m: get_session(m.from_user.id).state == State.SENDPROOF_WAIT_ORDER_ID)
def msg_capture_order_id(msg: types.Message):
    sess = get_session(msg.from_user.id)
    text = (msg.text or "").strip()
    if not text.isdigit():
        bot.send_message(msg.chat.id, "❌ الرجاء إرسال رقم الطلب بشكل صحيح.")
        return
    oid = int(text)
    order = order_get(oid)
    if not order or (order["user_id"] != msg.from_user.id and not is_admin(msg.from_user.id)):
        bot.send_message(msg.chat.id, "❌ لم يتم العثور على الطلب بهذا الرقم أو ليس تابعاً لك.")
        return
    sess.state = State.SENDPROOF_WAIT_MEDIA
    sess.data["order_id"] = oid
    bot.send_message(
        msg.chat.id,
        "أرسل الآن صورة أو ملف لإثبات الدفع لهذا الطلب.",
        reply_markup=kb_back("back:main"),
    )


@bot.message_handler(content_types=["photo", "document"])
def msg_receive_proof(msg: types.Message):
    sess = get_session(msg.from_user.id)
    if sess.state != State.SENDPROOF_WAIT_MEDIA:
        return  # ignore media sent outside proof flow
    oid = int(sess.data.get("order_id", 0))
    if not oid:
        bot.send_message(msg.chat.id, "❌ خطأ داخلي. أعد العملية.")
        clear_session(msg.from_user.id)
        return

    file_id: Optional[str] = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.document:
        file_id = msg.document.file_id

    if not file_id:
        bot.send_message(msg.chat.id, "❌ الرجاء إرسال صورة أو ملف.")
        return

    order_set_payment_file(oid, file_id)

    # إشعار الأدمن بالإثبات
    try:
        notify_admins(
            text=f"📎 تم استلام إثبات دفع لطلب #{oid}",
            reply_markup=kb_order_review(oid),
        )
    except Exception as e:
        log(f"[WARN] notify admins proof: {e}")

    bot.send_message(
        msg.chat.id,
        f"✅ تم حفظ إثبات الدفع لطلب #{oid}. سيتم مراجعته قريباً.",
        reply_markup=kb_main(is_admin(msg.from_user.id)),
    )
    clear_session(msg.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data == "user:track_order")
def cq_track_order_entry(cq: types.CallbackQuery):
    sess = get_session(cq.from_user.id)
    sess.state = State.TRACK_WAIT_ID
    try:
        bot.edit_message_text(
            "أدخل رقم الطلب للاستعلام عن حالته:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_back("back:main"),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "أدخل رقم الطلب للاستعلام عن حالته:", reply_markup=kb_back("back:main"))
    bot.answer_callback_query(cq.id)


@bot.message_handler(func=lambda m: get_session(m.from_user.id).state == State.TRACK_WAIT_ID)
def msg_track_lookup(msg: types.Message):
    sess = get_session(msg.from_user.id)
    text = (msg.text or "").strip()
    if not text.isdigit():
        bot.send_message(msg.chat.id, "❌ الرجاء إرسال رقم الطلب بشكل صحيح.")
        return
    oid = int(text)
    order = order_get(oid)
    if not order or (order["user_id"] != msg.from_user.id and not is_admin(msg.from_user.id)):
        bot.send_message(msg.chat.id, "❌ لا يوجد طلب بهذا الرقم أو ليس لك.")
    else:
        proof_txt = "موجود" if order["payment_file_id"] else "غير مُرسل"
        bot.send_message(
            msg.chat.id,
            (
                f"🧾 تفاصيل الطلب #{oid}:\n"
                f"اللعبة: {order['product_name']}\n"
                f"الكمية: {order['qty']}\n"
                f"الإجمالي: {money(order['total'])}\n"
                f"الحالة الحالية: <b>{order['status']}</b>\n"
                f"إثبات الدفع: {proof_txt}\n"
                f"تاريخ الإنشاء: {order['created_at']}"
            ),
            reply_markup=kb_main(is_admin(msg.from_user.id)),
        )
    clear_session(msg.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data == "user:help")
def cq_user_help(cq: types.CallbackQuery):
    try:
        bot.edit_message_text(
            "إذا واجهت أي مشكلة، راسل الدعم من خلال إرسال /help أو ابدأ من /start.",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_back("back:main"),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "إذا واجهت أي مشكلة، راسل الدعم من خلال إرسال /help أو ابدأ من /start.", reply_markup=kb_back("back:main"))
    bot.answer_callback_query(cq.id)


# ===================== لوحة الأدمن =====================

@bot.callback_query_handler(func=lambda c: c.data == "admin:panel")
def cq_admin_panel(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ليست لديك صلاحية", show_alert=True)
        return
    try:
        bot.edit_message_text(
            "🛠️ لوحة التحكم بالأدمن:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_admin_panel(),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "🛠️ لوحة التحكم بالأدمن:", reply_markup=kb_admin_panel())
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data == "admin:add_product")
def cq_admin_add_product(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    sess = get_session(cq.from_user.id)
    sess.state = State.ADD_PRODUCT_WAIT_NAME
    try:
        bot.edit_message_text(
            "اكتب اسم اللعبة/المنتج:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_back("admin:panel"),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "اكتب اسم اللعبة/المنتج:", reply_markup=kb_back("admin:panel"))
    bot.answer_callback_query(cq.id)


@bot.message_handler(func=lambda m: get_session(m.from_user.id).state == State.ADD_PRODUCT_WAIT_NAME)
def msg_admin_add_product_name(msg: types.Message):
    sess = get_session(msg.from_user.id)
    name = (msg.text or "").strip()
    if not name:
        bot.send_message(msg.chat.id, "❌ الاسم لا يمكن أن يكون فارغاً.")
        return
    sess.data["new_product_name"] = name
    sess.state = State.ADD_PRODUCT_WAIT_PRICE
    bot.send_message(msg.chat.id, "أدخل السعر (مثال: 3.5)", reply_markup=kb_back("admin:panel"))


@bot.message_handler(func=lambda m: get_session(m.from_user.id).state == State.ADD_PRODUCT_WAIT_PRICE)
def msg_admin_add_product_price(msg: types.Message):
    sess = get_session(msg.from_user.id)
    text = (msg.text or "").strip().replace(",", ".")
    try:
        price = float(text)
        if price < 0:
            raise ValueError
    except Exception:
        bot.send_message(msg.chat.id, "❌ من فضلك أرسل رقمًا صالحًا للسعر.")
        return
    name = sess.data.get("new_product_name", "")
    product_add(name, price)
    clear_session(msg.from_user.id)
    bot.send_message(msg.chat.id, f"✅ تمت إضافة المنتج: {name} — {money(price)}", reply_markup=kb_admin_panel())


@bot.callback_query_handler(func=lambda c: c.data == "admin:manage_products")
def cq_admin_manage_products(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    rows = db_fetchall("SELECT id,name,price FROM products ORDER BY id DESC")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not rows:
        kb.add(types.InlineKeyboardButton("لا توجد منتجات", callback_data="noop"))
    else:
        for r in rows:
            kb.add(types.InlineKeyboardButton(f"{r['name']} — {money(r['price'])}", callback_data=f"admin:product:{r['id']}"))
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel"))
    try:
        bot.edit_message_text(
            "إدارة المنتجات:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb,
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "إدارة المنتجات:", reply_markup=kb)
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:product:"))
def cq_admin_product_actions(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        pid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    prod = product_get(pid)
    if not prod:
        bot.answer_callback_query(cq.id, "غير موجود", show_alert=True)
        return
    text = f"المنتج: {prod['name']} — {money(prod['price'])}"
    try:
        bot.edit_message_text(
            text,
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_product_actions(pid),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, text, reply_markup=kb_product_actions(pid))
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:edit_price:"))
def cq_admin_edit_price(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        pid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    sess = get_session(cq.from_user.id)
    sess.state = State.EDIT_PRICE_WAIT_VALUE
    sess.data["pid"] = pid
    try:
        bot.edit_message_text(
            "أدخل السعر الجديد (مثال: 2.75)",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_back("admin:manage_products"),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "أدخل السعر الجديد (مثال: 2.75)", reply_markup=kb_back("admin:manage_products"))
    bot.answer_callback_query(cq.id)


@bot.message_handler(func=lambda m: get_session(m.from_user.id).state == State.EDIT_PRICE_WAIT_VALUE)
def msg_admin_edit_price_commit(msg: types.Message):
    sess = get_session(msg.from_user.id)
    text = (msg.text or "").strip().replace(",", ".")
    try:
        new_price = float(text)
        if new_price < 0:
            raise ValueError
    except Exception:
        bot.send_message(msg.chat.id, "❌ أدخل رقمًا صالحًا.")
        return
    pid = int(sess.data.get("pid", 0))
    product_edit_price(pid, new_price)
    prod = product_get(pid)
    name = prod["name"] if prod else "—"
    clear_session(msg.from_user.id)
    bot.send_message(msg.chat.id, f"✅ تم تحديث سعر {name} إلى {money(new_price)}", reply_markup=kb_admin_panel())


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:delete_product:"))
def cq_admin_delete_product(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        pid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    product_delete(pid)
    try:
        bot.edit_message_text(
            "🗑️ تم حذف المنتج.",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_admin_panel(),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "🗑️ تم حذف المنتج.", reply_markup=kb_admin_panel())
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data == "admin:list_pending")
def cq_admin_list_pending(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    ids = orders_pending_ids()
    kb = types.InlineKeyboardMarkup(row_width=1)
    if not ids:
        kb.add(types.InlineKeyboardButton("لا توجد طلبات معلّقة", callback_data="noop"))
    else:
        for oid in ids:
            kb.add(types.InlineKeyboardButton(f"طلب #{oid}", callback_data=f"admin:review:{oid}"))
    kb.add(types.InlineKeyboardButton("⬅️ رجوع", callback_data="admin:panel"))
    try:
        bot.edit_message_text(
            "الطلبات المعلّقة:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb,
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "الطلبات المعلّقة:", reply_markup=kb)
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:review:"))
def cq_admin_review(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        oid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    order = order_get(oid)
    if not order:
        bot.answer_callback_query(cq.id, "الطلب غير موجود", show_alert=True)
        return
    text = (
        f"🧾 طلب #{order['id']}\n"
        f"المستخدم: {order['user_id']}\n"
        f"اللعبة: {order['product_name']}\n"
        f"الكمية: {order['qty']}\n"
        f"الإجمالي: {money(order['total'])}\n"
        f"الحالة: {order['status']}\n"
        f"تاريخ: {order['created_at']}"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_order_review(oid),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, text, reply_markup=kb_order_review(oid))

    if order["payment_file_id"]:
        # حاول إرسال الصورة أولاً، وإن فشل أرسل كوثيقة
        try:
            bot.send_photo(cq.from_user.id, order["payment_file_id"], caption=f"إثبات دفع — طلب #{oid}")
        except Exception:
            try:
                bot.send_document(cq.from_user.id, order["payment_file_id"], caption=f"إثبات دفع — طلب #{oid}")
            except Exception:
                pass
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:accept:"))
def cq_admin_accept(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        oid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    user_id = order_set_status(oid, "accepted")
    if not user_id:
        bot.answer_callback_query(cq.id, "غير موجود", show_alert=True)
        return
    try:
        bot.send_message(user_id, f"🎉 تم <b>قبول</b> طلبك #{oid}. شكرًا لك!")
    except Exception as e:
        log(f"[WARN] notify user accept: {e}")
    try:
        bot.edit_message_text(
            f"تم قبول الطلب #{oid}.",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_order_review(oid),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, f"تم قبول الطلب #{oid}.", reply_markup=kb_order_review(oid))
    bot.answer_callback_query(cq.id, "✅ تم القبول")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:reject:"))
def cq_admin_reject(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        oid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    user_id = order_set_status(oid, "rejected")
    if not user_id:
        bot.answer_callback_query(cq.id, "غير موجود", show_alert=True)
        return
    try:
        bot.send_message(user_id, f"❌ تم <b>رفض</b> طلبك #{oid}. تواصل مع الدعم إن لزم.")
    except Exception as e:
        log(f"[WARN] notify user reject: {e}")
    try:
        bot.edit_message_text(
            f"تم رفض الطلب #{oid}.",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_order_review(oid),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, f"تم رفض الطلب #{oid}.", reply_markup=kb_order_review(oid))
    bot.answer_callback_query(cq.id, "❌ تم الرفض")


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:details:"))
def cq_admin_details(cq: types.CallbackQuery):
    if not is_admin(cq.from_user.id):
        bot.answer_callback_query(cq.id, "ممنوع", show_alert=True)
        return
    try:
        oid = int(cq.data.split(":")[-1])
    except Exception:
        bot.answer_callback_query(cq.id, "غير صالح", show_alert=True)
        return
    order = order_get(oid)
    if not order:
        bot.answer_callback_query(cq.id, "غير موجود", show_alert=True)
        return
    proof = "✅" if order["payment_file_id"] else "—"
    text = (
        f"تفاصيل كاملة لطلب #{order['id']}:\n\n"
        f"المستخدم: {order['user_id']}\n"
        f"اللعبة: {order['product_name']} (#{order['product_id']})\n"
        f"سعر الوحدة: {money(order['unit_price'])}\n"
        f"الكمية: {order['qty']}\n"
        f"الإجمالي: {money(order['total'])}\n"
        f"الحالة: {order['status']}\n"
        f"إثبات الدفع: {proof}\n"
        f"الإنشاء: {order['created_at']}\n"
        f"آخر تحديث: {order['updated_at']}"
    )
    try:
        bot.edit_message_text(
            text,
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_order_review(oid),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, text, reply_markup=kb_order_review(oid))
    bot.answer_callback_query(cq.id)


# ===================== أزرار الرجوع و NOOP =====================

@bot.callback_query_handler(func=lambda c: c.data == "back:main")
def cq_back_main(cq: types.CallbackQuery):
    clear_session(cq.from_user.id)
    try:
        bot.edit_message_text(
            "القائمة الرئيسية:",
            chat_id=cq.message.chat.id,
            message_id=cq.message.message_id,
            reply_markup=kb_main(is_admin(cq.from_user.id)),
        )
    except Exception:
        bot.send_message(cq.message.chat.id, "القائمة الرئيسية:", reply_markup=kb_main(is_admin(cq.from_user.id)))
    bot.answer_callback_query(cq.id)


@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cq_noop(cq: types.CallbackQuery):
    bot.answer_callback_query(cq.id)


# ===================== أوامر مساعدة للأدمن (إضافية) =====================

@bot.message_handler(commands=["stats"])
def cmd_stats(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    prod_count = db_fetchone("SELECT COUNT(*) c FROM products")["c"]
    ord_count = db_fetchone("SELECT COUNT(*) c FROM orders")["c"]
    pend_count = db_fetchone("SELECT COUNT(*) c FROM orders WHERE status='pending'")["c"]
    acc_count = db_fetchone("SELECT COUNT(*) c FROM orders WHERE status='accepted'")["c"]
    rej_count = db_fetchone("SELECT COUNT(*) c FROM orders WHERE status='rejected'")["c"]
    bot.send_message(
        msg.chat.id,
        (
            "📊 إحصائيات:\n"
            f"المنتجات: {prod_count}\n"
            f"الطلبات: {ord_count} (قيد: {pend_count} / مقبول: {acc_count} / مرفوض: {rej_count})"
        ),
    )


@bot.message_handler(commands=["adddemo"])
def cmd_add_demo(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return
    demo = [
        ("UC PUBG", 0.99),
        ("Diamonds Free Fire", 0.79),
        ("CP Call of Duty", 1.49),
        ("Robux", 0.50),
    ]
    for name, price in demo:
        product_add(name, price)
    bot.send_message(msg.chat.id, "✅ تمت إضافة منتجات تجريبية.")


# ===================== نقطة تشغيل البوت =====================

def main():
    db_init()
    log("🚀 البوت يعمل الآن…")
    try:
        # polling(none_stop=True) مهم لتشغيل دائم
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except KeyboardInterrupt:
        print("Bot stopped by user")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
