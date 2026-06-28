#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE Chat — Gaming Community Chat Platform
============================================
ONE single file. Only one thing to install: the `aiohttp` package.

    pip install aiohttp
    python run.py

Then open: http://localhost:8080

(See the bottom of this file for what's actually going on: the HTML/CSS/JS
and the database module are embedded as strings and reconstructed at startup
into temporary files, then the same server logic from the modular version
runs unchanged.)
"""
import asyncio
import json
import os
import random
import re
import secrets
import time
import types
import uuid
from pathlib import Path

from aiohttp import web


# ════════════════════════════════════════════════════════════════════════
#  Embedded db.py source — loaded as a real module named `db` at runtime.
#  (Keeping this as a separate module, exactly like the original db.py,
#  means every `db.get_conn()`, `db.tx()`, etc. call below works completely
#  unmodified — there's no risk of a half-finished find/replace.)
# ════════════════════════════════════════════════════════════════════════
_DB_SOURCE = '"""\ndb.py — SQLite layer for LINE Chat.\nOne file, zero external DB needed. Safe for concurrent access via WAL mode.\n"""\nimport sqlite3\nimport hashlib\nimport secrets\nimport time\nfrom pathlib import Path\nfrom contextlib import contextmanager\n\nimport os\n\nDATA_DIR = Path(os.environ.get("PERSIST_DIR", Path(__file__).parent)) / "data"\nDATA_DIR.mkdir(exist_ok=True, parents=True)\nDB_PATH = DATA_DIR / "line.db"\n\nAVATAR_COLORS = ["#00d4ff", "#7c3aed", "#39ff14", "#ff6b35", "#ffd700",\n                  "#ff69b4", "#00bfff", "#ff4757", "#a78bfa", "#34d399"]\n\n\n_conn = None\n\n\ndef get_conn():\n    global _conn\n    if _conn is None:\n        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)\n        _conn.row_factory = sqlite3.Row\n        _conn.execute("PRAGMA journal_mode=WAL")\n        _conn.execute("PRAGMA foreign_keys=ON")\n    return _conn\n\n\n@contextmanager\ndef tx():\n    conn = get_conn()\n    try:\n        yield conn\n        conn.commit()\n    except Exception:\n        conn.rollback()\n        raise\n\n\ndef _ensure_column(conn, table, column, decl):\n    """برای کسایی که از قبل دیتابیس ساخته بودن: اگه ستون جدید وجود نداشت، اضافه\u200cش می\u200cکنه."""\n    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]\n    if column not in cols:\n        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")\n        conn.commit()\n\n\ndef _svg_avatar(emoji: str, c1: str, c2: str) -> str:\n    """\n    یه آیکون ساده\u200cی گرادینت\u200cدار با یه ایموجی وسطش می\u200cسازه (به\u200cصورت data-URI).\n    این آیکون\u200cها خودمون ساختیمشون (نه لوگو/آرت واقعی بازی\u200cها) که مشکل کپی\u200cرایت نداشته باشن،\n    ولی بازم هر روم یه آواتار رنگی و مرتبط با موضوعش داره.\n    """\n    import urllib.parse\n    svg = (\n        \'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\'\n        \'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">\'\n        f\'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/>\'\n        \'</linearGradient></defs>\'\n        \'<rect width="100" height="100" rx="22" fill="url(#g)"/>\'\n        f\'<text x="50" y="58" font-size="46" text-anchor="middle" dominant-baseline="middle">{emoji}</text>\'\n        \'</svg>\'\n    )\n    return "data:image/svg+xml," + urllib.parse.quote(svg)\n\n\ndef init_db():\n    conn = get_conn()\n    conn.executescript("""\n    CREATE TABLE IF NOT EXISTS users (\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n        username TEXT UNIQUE NOT NULL,\n        password_hash TEXT NOT NULL,\n        display_name TEXT NOT NULL,\n        avatar_color TEXT NOT NULL,\n        avatar_url TEXT,\n        bio TEXT DEFAULT \'\',\n        status TEXT DEFAULT \'\',\n        current_game TEXT DEFAULT \'\',\n        role TEXT DEFAULT \'member\',          -- member | admin\n        message_count INTEGER DEFAULT 0,\n        score INTEGER DEFAULT 0,\n        created_at REAL NOT NULL,\n        banned INTEGER DEFAULT 0,\n        muted_until REAL DEFAULT 0\n    );\n\n    CREATE TABLE IF NOT EXISTS rooms (\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n        name TEXT NOT NULL,\n        slug TEXT UNIQUE NOT NULL,\n        kind TEXT DEFAULT \'public\',          -- public | game | voice | private\n        owner_id INTEGER,\n        password_hash TEXT,                  -- deprecated, kept for backward compatibility\n        invite_code TEXT,                    -- for private rooms: the join code (like a Telegram invite link)\n        icon TEXT DEFAULT \'💬\',               -- fallback emoji icon\n        icon_url TEXT,                       -- uploaded image icon (takes priority over the emoji if set)\n        created_at REAL NOT NULL\n    );\n\n    CREATE TABLE IF NOT EXISTS room_members (\n        room_id INTEGER NOT NULL,\n        user_id INTEGER NOT NULL,\n        joined_at REAL NOT NULL,\n        PRIMARY KEY (room_id, user_id)\n    );\n\n    CREATE TABLE IF NOT EXISTS messages (\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n        room_id INTEGER,                     -- NULL if it\'s a DM\n        dm_key TEXT,                         -- NULL if it\'s a room msg, else "min_id:max_id"\n        user_id INTEGER NOT NULL,\n        text TEXT DEFAULT \'\',\n        media_url TEXT,\n        media_type TEXT,                     -- image | gif\n        reply_to INTEGER,                    -- message id being replied to\n        is_sticker INTEGER DEFAULT 0,\n        created_at REAL NOT NULL,\n        deleted INTEGER DEFAULT 0\n    );\n\n    CREATE INDEX IF NOT EXISTS idx_msg_room ON messages(room_id, created_at);\n    CREATE INDEX IF NOT EXISTS idx_msg_dm ON messages(dm_key, created_at);\n\n    CREATE TABLE IF NOT EXISTS reactions (\n        message_id INTEGER NOT NULL,\n        user_id INTEGER NOT NULL,\n        emoji TEXT NOT NULL,\n        PRIMARY KEY (message_id, user_id, emoji)\n    );\n\n    CREATE TABLE IF NOT EXISTS friendships (\n        user_id INTEGER NOT NULL,\n        friend_id INTEGER NOT NULL,\n        status TEXT DEFAULT \'pending\',       -- pending | accepted\n        created_at REAL NOT NULL,\n        PRIMARY KEY (user_id, friend_id)\n    );\n\n    CREATE TABLE IF NOT EXISTS reports (\n        id INTEGER PRIMARY KEY AUTOINCREMENT,\n        reporter_id INTEGER NOT NULL,\n        message_id INTEGER,\n        target_user_id INTEGER,\n        reason TEXT,\n        created_at REAL NOT NULL,\n        resolved INTEGER DEFAULT 0\n    );\n\n    CREATE TABLE IF NOT EXISTS banned_words (\n        word TEXT PRIMARY KEY\n    );\n\n    CREATE TABLE IF NOT EXISTS sessions (\n        token TEXT PRIMARY KEY,\n        user_id INTEGER NOT NULL,\n        created_at REAL NOT NULL\n    );\n    """)\n    conn.commit()\n\n    _ensure_column(conn, "messages", "is_sticker", "INTEGER DEFAULT 0")\n    _ensure_column(conn, "rooms", "invite_code", "TEXT")\n    _ensure_column(conn, "rooms", "icon_url", "TEXT")\n    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_rooms_invite_code ON rooms(invite_code)")\n    conn.commit()\n\n    # Seed default rooms if empty\n    cur = conn.execute("SELECT COUNT(*) c FROM rooms")\n    if cur.fetchone()["c"] == 0:\n        now = time.time()\n        defaults = [\n            ("چت عمومی", "general", "public", "💬", _svg_avatar("💬", "#2aabee", "#229ed9")),\n            ("Valorant", "valorant", "game", "🔫", _svg_avatar("🔫", "#ff4655", "#1f2326")),\n            ("Minecraft", "minecraft", "game", "🟩", _svg_avatar("⛏️", "#4caf50", "#2e7d32")),\n            ("PUBG Mobile", "pubg-mobile", "game", "🪖", _svg_avatar("🪖", "#f2a93b", "#8a6d3b")),\n            ("Free Fire", "free-fire", "game", "🔥", _svg_avatar("🔥", "#ff8a00", "#d32f2f")),\n            ("Call of Duty Mobile", "cod-mobile", "game", "🎯", _svg_avatar("🎯", "#4a4a4a", "#1b1b1b")),\n        ]\n        for name, slug, kind, icon, icon_url in defaults:\n            conn.execute(\n                "INSERT INTO rooms(name, slug, kind, owner_id, icon, icon_url, created_at) VALUES (?,?,?,?,?,?,?)",\n                (name, slug, kind, None, icon, icon_url, now),\n            )\n        conn.commit()\n\n    # Seed default banned words\n    cur = conn.execute("SELECT COUNT(*) c FROM banned_words")\n    if cur.fetchone()["c"] == 0:\n        # Minimal starter filter list — admins can extend via the admin panel.\n        for w in ["badword1", "badword2"]:\n            conn.execute("INSERT OR IGNORE INTO banned_words(word) VALUES (?)", (w,))\n        conn.commit()\n\n\n# ── Password hashing (salted) ────────────────────────────────────────────────\ndef hash_password(password: str, salt: str = None) -> str:\n    salt = salt or secrets.token_hex(16)\n    h = hashlib.sha256((salt + password).encode()).hexdigest()\n    return f"{salt}${h}"\n\n\ndef verify_password(password: str, stored: str) -> bool:\n    try:\n        salt, _ = stored.split("$", 1)\n    except ValueError:\n        return False\n    return hash_password(password, salt) == stored\n\n\ndef dm_key(a: int, b: int) -> str:\n    lo, hi = sorted([a, b])\n    return f"{lo}:{hi}"\n\n\n# حروف/عدد گیج\u200cکننده (0/O، 1/I/L) رو حذف کردیم تا کد رو راحت بشه دستی هم تایپ کرد\nINVITE_CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"\n\n\ndef generate_invite_code(length=6):\n    return "".join(secrets.choice(INVITE_CODE_CHARS) for _ in range(length))\n\n'

db = types.ModuleType("db")
db.__file__ = __file__
exec(compile(_DB_SOURCE, "<embedded db.py>", "exec"), db.__dict__)


# ════════════════════════════════════════════════════════════════════════
#  Embedded frontend assets (originally templates/index.html,
#  static/css/style.css, static/js/app.js)
# ════════════════════════════════════════════════════════════════════════
INDEX_HTML = '<!DOCTYPE html>\n<html lang="fa" dir="rtl">\n<head>\n<meta charset="UTF-8"/>\n<meta name="viewport" content="width=device-width,initial-scale=1"/>\n<title>LINE Chat 🎮</title>\n<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700;900&family=Vazirmatn:wght@300;400;500;700&display=swap" rel="stylesheet"/>\n<link rel="stylesheet" href="/static/css/style.css"/>\n</head>\n<body data-theme="telegram">\n<canvas id="cv"></canvas>\n\n<!-- ════════════ AUTH SCREEN ════════════ -->\n<div class="scr on" id="aScr">\n  <div class="card">\n    <div class="logo">\n      <div class="hex"><span>L</span></div>\n      <div class="ltitle">LINE</div>\n      <div class="lsub">Gaming Community Chat</div>\n    </div>\n    <div class="tabs">\n      <button class="tab on" id="tL" onclick="stab(\'L\')">ورود</button>\n      <button class="tab" id="tR" onclick="stab(\'R\')">ثبت\u200cنام</button>\n    </div>\n    <div id="fL">\n      <div class="fld"><label>نام کاربری</label><input id="lu" placeholder="نام کاربری..." autocomplete="username"/></div>\n      <div class="fld"><label>رمز عبور</label><input id="lp" type="password" placeholder="رمز عبور..." autocomplete="current-password"/></div>\n      <div class="err" id="le"></div>\n      <button class="btnp" onclick="doLogin()">🎮 ورود به چت</button>\n    </div>\n    <div id="fR" class="hid">\n      <div class="fld"><label>نام کاربری</label><input id="ru" placeholder="حروف/عدد انگلیسی یا فارسی..." autocomplete="username"/></div>\n      <div class="fld"><label>اسم نمایشی</label><input id="rd" placeholder="اسم نمایشی..."/></div>\n      <div class="fld"><label>رمز عبور</label><input id="rp" type="password" placeholder="حداقل ۴ کاراکتر..." autocomplete="new-password"/></div>\n      <div class="fld"><label>کد ادمین (اختیاری)</label><input id="rAdminCode" type="password" placeholder="فقط اگه ادمین هستی پُرش کن..."/></div>\n      <div class="err" id="re"></div>\n      <button class="btnp" onclick="doReg()">⚡ ساخت اکانت</button>\n    </div>\n    <div class="afoot">💎 ساخته شده توسط <strong>LINE Community</strong></div>\n  </div>\n</div>\n\n<!-- ════════════ MAIN APP ════════════ -->\n<div class="scr" id="cScr">\n  <div class="rail-backdrop" id="railBackdrop" onclick="closeRailMobile()"></div>\n\n\n  <!-- LEFT RAIL: rooms / nav -->\n  <aside class="rail">\n    <div class="slogo"><div class="shex"><span>L</span></div><span class="rail-label">LINE</span></div>\n\n    <div class="rail-section">\n      <div class="slbl">روم\u200cهای عمومی</div>\n      <ul class="roomlist" id="roomlistPublic"></ul>\n    </div>\n    <div class="rail-section">\n      <div class="slbl">روم\u200cهای گیمینگ</div>\n      <ul class="roomlist" id="roomlistGame"></ul>\n    </div>\n    <div class="rail-section">\n      <div class="slbl">روم\u200cهای صوتی</div>\n      <ul class="roomlist" id="roomlistVoice"></ul>\n    </div>\n    <div class="rail-section">\n      <div class="slbl">روم\u200cهای خصوصی</div>\n      <ul class="roomlist" id="roomlistPrivate"></ul>\n      <button class="btn-join-code" onclick="openJoinByCodeModal()">🔑 ورود با کد</button>\n    </div>\n    <button class="btn-create-room" onclick="openCreateRoom()">+ ساخت روم</button>\n\n    <div class="rail-bottom">\n      <button class="navbtn" onclick="openPanel(\'dmPanel\')">💬 پیام خصوصی</button>\n      <button class="navbtn" onclick="openPanel(\'friendsPanel\')">👥 دوستان</button>\n      <button class="navbtn" id="adminNavBtn" onclick="openPanel(\'adminPanel\')" style="display:none">🔧 ادمین</button>\n      <button class="navbtn" onclick="openPanel(\'themePanel\')">🎨 تم\u200cها</button>\n    </div>\n  </aside>\n\n  <!-- CENTER: active chat -->\n  <main class="cmain">\n    <header class="chdr">\n      <div class="chl">\n        <button class="iconbtn railToggleBtn" onclick="toggleRail()" title="منو">☰</button>\n        <div class="hdot"></div>\n        <div>\n          <h2 id="chTitle">چت عمومی</h2>\n          <span class="obadge" id="ocnt">0 آنلاین</span>\n        </div>\n      </div>\n      <div class="chr">\n        <button class="iconbtn hid" id="roomManageBtn" onclick="openPanel(\'roomManagePanel\')" title="مدیریت روم">⚙️</button>\n        <span class="site-online-badge" id="siteOnlineBadge" title="کل کاربران آنلاین در سایت">🌐 0</span>\n        <button class="iconbtn membersToggleBtn" onclick="openPanel(\'membersPanel\')" title="اعضای روم">👥</button>\n        <button class="iconbtn" id="musicBtn" onclick="toggleMusic()" title="موزیک پس\u200cزمینه">🔇</button>\n        <div class="myprofile" onclick="openPanel(\'profilePanel\')">\n          <div class="mini-av" id="myAvMini"></div>\n          <span id="cusr"></span>\n        </div>\n      </div>\n    </header>\n\n    <!-- CALL SCREEN — shown instead of chat when the current room is a voice chat -->\n    <div class="call-screen hid" id="callScreen">\n      <div class="call-room-icon" id="callRoomIcon">🎙️</div>\n      <h2 class="call-room-title" id="callRoomTitle">چت صوتی</h2>\n      <p class="call-status" id="callStatus">برای شروع، وارد تماس شو</p>\n      <div class="call-participants" id="callParticipants"></div>\n      <div class="call-actions">\n        <button class="call-btn call-btn-join" id="callJoinBtn" onclick="joinVoiceChat()">📞 ورود به تماس</button>\n        <button class="call-btn call-btn-leave hid" id="callLeaveBtn" onclick="leaveVoiceChat()">🔴 پایان تماس</button>\n      </div>\n    </div>\n\n    <!-- CHAT VIEW — shown for normal text rooms -->\n    <div id="chatViewWrap">\n      <div class="msgs" id="msgs"></div>\n      <div class="tbar h" id="tbar"></div>\n\n      <div class="reply-preview hid" id="replyPreview">\n        <div><span class="rp-label">پاسخ به</span> <span id="rpName"></span><div id="rpText" class="rp-text"></div></div>\n        <button onclick="cancelReply()">✕</button>\n      </div>\n\n      <div class="sticker-picker hid" id="stickerPicker"></div>\n\n      <div class="iarea">\n        <button class="iconbtn iconbtn-plus" onclick="triggerMediaUpload()" title="آپلود عکس">+</button>\n        <input type="file" id="mediaInput" accept="image/*" class="hid" onchange="onMediaSelected(event)"/>\n        <button class="iconbtn" onclick="toggleStickerPicker()" title="استیکر">🎟️</button>\n        <input id="mi" placeholder="پیامت رو بنویس... (!dice !coin !quiz)" maxlength="1500" onkeydown="hkey(event)" oninput="styping()"/>\n        <button class="bsnd" onclick="sendMsg()">➤</button>\n      </div>\n    </div>\n  </main>\n\n  <!-- RIGHT: members of current room -->\n  <aside class="side">\n    <div class="pcard">\n      <div class="pcard-flare">⚡</div>\n      <h3>گروه لاین</h3>\n      <p>بزرگترین جامعه گیمری<br>تورنمنت، رویداد، دوستی</p>\n      <a href="#" class="pcbtn">عضو شو</a>\n    </div>\n    <div class="slbl">آنلاین در این روم</div>\n    <ul class="ulist" id="ulist"></ul>\n    <button class="btnlo" onclick="doLogout()">🚪 خروج</button>\n  </aside>\n</div>\n\n<!-- MEMBERS PANEL (mobile/tablet — mirrors .side, since .side is hidden there) -->\n<div class="panel hid" id="membersPanel">\n  <div class="panel-hdr"><h3>👥 اعضای روم</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="panel-body">\n    <div class="slbl">کل آنلاین در کل سایت</div>\n    <div class="site-online-chip" id="siteOnlineChipMobile">— نفر آنلاین</div>\n    <div class="slbl">آنلاین در این روم</div>\n    <ul class="ulist" id="ulistMobile"></ul>\n    <button class="btnlo" onclick="closeAllPanels();doLogout()">🚪 خروج</button>\n  </div>\n</div>\n\n<!-- ════════════ SLIDE-OVER PANELS ════════════ -->\n<div class="overlay hid" id="overlay" onclick="closeAllPanels()"></div>\n\n<!-- DM PANEL -->\n<div class="panel hid" id="dmPanel">\n  <div class="panel-hdr"><h3>💬 پیام خصوصی</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="dm-layout">\n    <div class="dm-list" id="dmUserList">\n      <input class="dm-search" placeholder="جستجوی کاربر..." oninput="searchDmUsers(this.value)"/>\n      <div id="dmSearchResults"></div>\n      <div class="slbl">گفتگوهای اخیر</div>\n      <div id="dmRecent"></div>\n    </div>\n    <div class="dm-active hid" id="dmActive">\n      <div class="dm-active-hdr">\n        <div class="mini-av" id="dmActiveAv"></div>\n        <span id="dmActiveName"></span>\n      </div>\n      <div class="msgs" id="dmMsgs"></div>\n      <div class="iarea">\n        <button class="iconbtn" onclick="triggerMediaUpload(\'dm\')">🖼️</button>\n        <input id="dmInput" placeholder="پیام بفرست..." maxlength="1500" onkeydown="dmKey(event)"/>\n        <button class="bsnd" onclick="sendDm()">➤</button>\n      </div>\n    </div>\n  </div>\n</div>\n\n<!-- FRIENDS PANEL -->\n<div class="panel hid" id="friendsPanel">\n  <div class="panel-hdr"><h3>👥 دوستان</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="panel-body">\n    <div class="slbl">درخواست\u200cهای دریافتی</div>\n    <div id="friendsIncoming" class="friend-list"></div>\n    <div class="slbl">درخواست\u200cهای ارسالی</div>\n    <div id="friendsOutgoing" class="friend-list"></div>\n    <div class="slbl">دوستان من</div>\n    <div id="friendsList" class="friend-list"></div>\n  </div>\n</div>\n\n<!-- PROFILE PANEL -->\n<div class="panel hid" id="profilePanel">\n  <div class="panel-hdr"><h3>👤 پروفایل من</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="panel-body">\n    <div class="profile-edit">\n      <div class="big-av" id="profAvBig"></div>\n      <button class="btn-secondary sm" onclick="triggerMediaUpload(\'avatar\')">📷 تغییر آواتار</button>\n      <div class="fld"><label>اسم نمایشی</label><input id="profDisplayName" maxlength="30"/></div>\n      <div class="fld"><label>استاتوس</label><input id="profStatus" maxlength="60" placeholder="مثلاً: آنلاینم، بزن بریم 🎮"/></div>\n      <div class="fld"><label>بیو</label><textarea id="profBio" maxlength="200" rows="3" placeholder="چند خط درباره خودت..."></textarea></div>\n      <div class="fld"><label>بازی در حال انجام</label><input id="profGame" maxlength="40" placeholder="مثلاً: Valorant"/></div>\n      <div class="fld"><label>رنگ آواتار</label><div class="color-row" id="colorRow"></div></div>\n      <button class="btnp" onclick="saveProfile()">💾 ذخیره تغییرات</button>\n    </div>\n    <div class="profile-stats" id="profStats"></div>\n    <div class="admin-redeem-box hid" id="adminRedeemBox">\n      <div class="slbl">دسترسی ادمین</div>\n      <div class="fld-row">\n        <input id="profAdminCode" type="password" placeholder="کد ادمین رو اینجا بزن..."/>\n        <button class="btn-secondary sm" onclick="redeemAdminCode()">🔑 فعال\u200cسازی</button>\n      </div>\n    </div>\n  </div>\n</div>\n\n<!-- ADMIN PANEL -->\n<div class="panel hid wide" id="adminPanel">\n  <div class="panel-hdr"><h3>🔧 پنل مدیریت</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="panel-body admin-grid">\n    <div>\n      <div class="slbl">گزارش\u200cهای کاربران</div>\n      <div id="reportsList" class="admin-list"></div>\n    </div>\n    <div>\n      <div class="slbl">فیلتر کلمات</div>\n      <div class="fld-row">\n        <input id="newBannedWord" placeholder="کلمه ممنوعه جدید..."/>\n        <button class="btn-secondary sm" onclick="addBannedWord()">+ افزودن</button>\n      </div>\n      <div id="bannedWordsList" class="word-tags"></div>\n      <div class="slbl" style="margin-top:18px">اقدام سریع روی کاربر</div>\n      <div class="fld-row">\n        <input id="adminTargetUsername" placeholder="نام کاربری..."/>\n        <button class="btn-secondary sm danger" onclick="adminActOnUsername(\'admin_mute\')">🔇 میوت ۱۰ دقیقه</button>\n      </div>\n      <div class="fld-row">\n        <button class="btn-secondary sm danger" onclick="adminActOnUsername(\'admin_kick\')">👋 کیک</button>\n        <button class="btn-secondary sm danger" onclick="adminActOnUsername(\'admin_ban\')">⛔ بن</button>\n        <button class="btn-secondary sm" onclick="adminActOnUsername(\'admin_unban\')">✅ آنبن</button>\n      </div>\n    </div>\n  </div>\n</div>\n\n<!-- THEME PANEL -->\n<div class="panel hid" id="themePanel">\n  <div class="panel-hdr"><h3>🎨 ظاهر</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="panel-body">\n    <div class="slbl">تم رنگی</div>\n    <div class="theme-grid">\n      <button class="theme-opt" data-theme="telegram" onclick="setTheme(\'telegram\')">\n        <span class="theme-swatch telegram"></span>Telegram\n      </button>\n      <button class="theme-opt" data-theme="cyberpunk" onclick="setTheme(\'cyberpunk\')">\n        <span class="theme-swatch cyberpunk"></span>Cyberpunk\n      </button>\n      <button class="theme-opt" data-theme="neon" onclick="setTheme(\'neon\')">\n        <span class="theme-swatch neon"></span>Neon\n      </button>\n      <button class="theme-opt" data-theme="darkred" onclick="setTheme(\'darkred\')">\n        <span class="theme-swatch darkred"></span>Dark Red\n      </button>\n      <button class="theme-opt" data-theme="aurora" onclick="setTheme(\'aurora\')">\n        <span class="theme-swatch aurora"></span>Aurora\n      </button>\n    </div>\n    <div class="slbl">افکت پس\u200cزمینه</div>\n    <label class="switch-row"><input type="checkbox" id="particlesToggle" onchange="toggleParticles(this.checked)"/> شبکه ذرات متحرک</label>\n    <div class="slbl">صدا</div>\n    <label class="switch-row"><input type="checkbox" id="soundToggle" checked onchange="soundEnabled=this.checked"/> صدای پیام جدید</label>\n  </div>\n</div>\n\n<!-- CREATE ROOM MODAL -->\n<div class="modal hid" id="createRoomModal">\n  <div class="modal-card">\n    <h3>+ ساخت روم جدید</h3>\n    <div class="fld"><label>نام روم</label><input id="newRoomName" maxlength="40" placeholder="مثلاً: Apex Legends"/></div>\n    <div class="fld" id="newRoomKindWrap"><label>نوع روم</label>\n      <select id="newRoomKind" onchange="onRoomKindChange()">\n        <option value="public">عمومی</option>\n        <option value="game">گیمینگ</option>\n        <option value="voice">صوتی</option>\n        <option value="private">خصوصی (با کد دعوت)</option>\n      </select>\n    </div>\n    <div class="fld hid" id="newRoomKindLockedNote">\n      <div class="private-room-note">🔒 چون ادمین نیستی، روم\u200cهایی که می\u200cسازی همیشه خصوصی هستن (فقط با کد دعوت قابل ورود برای بقیه).</div>\n    </div>\n    <div class="fld hid" id="newRoomPrivateNote">\n      <div class="private-room-note">🔒 این روم توی لیست عمومی دیده نمی\u200cشه. بعد از ساخت، یه کد دعوت بهت می\u200cدیم که می\u200cتونی به هرکی خواستی بدی تا وارد روم بشه — دقیقاً مثل لینک گروه خصوصی تلگرام.</div>\n    </div>\n    <div class="fld"><label>آیکون روم</label>\n      <div class="room-icon-picker">\n        <div class="room-icon-preview" id="newRoomIconPreview">🎮</div>\n        <div class="room-icon-picker-actions">\n          <input id="newRoomIcon" maxlength="4" placeholder="یه ایموجی بنویس" value="🎮" oninput="onNewRoomEmojiInput()"/>\n          <button type="button" class="btn-secondary sm" onclick="triggerMediaUpload(\'room_icon_new\')">📷 یا یه عکس آپلود کن</button>\n        </div>\n      </div>\n      <input type="hidden" id="newRoomIconUrl" value=""/>\n    </div>\n    <div class="err" id="createRoomErr"></div>\n    <div class="modal-actions">\n      <button class="btn-secondary sm" onclick="closeCreateRoom()">انصراف</button>\n      <button class="btnp" onclick="submitCreateRoom()">ساخت روم</button>\n    </div>\n  </div>\n</div>\n\n<!-- JOIN BY CODE MODAL -->\n<div class="modal hid" id="joinByCodeModal">\n  <div class="modal-card">\n    <h3>🔑 ورود به روم خصوصی با کد</h3>\n    <div class="fld"><label>کد دعوت</label><input id="joinCodeInput" maxlength="10" placeholder="مثلاً: GPVZZ2" style="text-align:center;letter-spacing:3px;font-family:var(--font-disp);text-transform:uppercase" onkeydown="if(event.key===\'Enter\')submitJoinByCode()"/></div>\n    <div class="err" id="joinCodeErr"></div>\n    <div class="modal-actions">\n      <button class="btn-secondary sm" onclick="closeJoinByCodeModal()">انصراف</button>\n      <button class="btnp" onclick="submitJoinByCode()">ورود به روم</button>\n    </div>\n  </div>\n</div>\n\n<!-- ROOM MANAGE PANEL (owner/admin controls for the current room) -->\n<div class="panel hid" id="roomManagePanel">\n  <div class="panel-hdr"><h3>⚙️ مدیریت روم</h3><button onclick="closeAllPanels()">✕</button></div>\n  <div class="panel-body">\n    <div class="fld"><label>اسم روم</label><input id="manageRoomName" maxlength="40"/></div>\n    <div class="fld"><label>آیکون روم</label>\n      <div class="room-icon-picker">\n        <div class="room-icon-preview" id="manageRoomIconPreview">🎮</div>\n        <div class="room-icon-picker-actions">\n          <input id="manageRoomIcon" maxlength="4" placeholder="یه ایموجی بنویس" oninput="onManageRoomEmojiInput()"/>\n          <button type="button" class="btn-secondary sm" onclick="triggerMediaUpload(\'room_icon_manage\')">📷 یا یه عکس آپلود کن</button>\n        </div>\n      </div>\n    </div>\n    <button class="btnp" onclick="saveRoomEdit()">💾 ذخیره تغییرات</button>\n\n    <div class="fld hid" id="manageInviteCodeWrap" style="margin-top:20px">\n      <label>کد دعوت این روم</label>\n      <div class="invite-code-row">\n        <span class="invite-code-box" id="manageInviteCode">------</span>\n        <button class="btn-secondary sm" onclick="copyInviteCode()">📋 کپی</button>\n      </div>\n      <button class="btn-secondary sm danger" style="margin-top:8px" onclick="regenerateRoomCode()">🔄 ساخت کد جدید (کد قبلی غیرفعال می\u200cشه)</button>\n    </div>\n\n    <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--bor)">\n      <button class="btn-secondary sm danger" onclick="deleteRoomConfirm()">🗑️ حذف کامل این روم</button>\n    </div>\n  </div>\n</div>\n\n<!-- Lightbox for viewing images full-size -->\n<div class="lightbox hid" id="lightbox" onclick="closeLightbox()">\n  <img id="lightboxImg" src=""/>\n</div>\n\n<audio id="bgMusic" loop preload="none">\n  <source src="https://cdn.pixabay.com/audio/2022/05/27/audio_1808fbf07a.mp3" type="audio/mpeg">\n</audio>\n\n<script src="/static/js/app.js"></script>\n</body>\n</html>\n'

STYLE_CSS = ':root{\n  --bg:#080b14;--bg2:#0d1220;--bg3:#111827;--sur:#161d2e;--bor:#1e2d4a;\n  --ac:#00d4ff;--ac2:#7c3aed;--ac3:#39ff14;--red:#ff4757;\n  --tx:#e2e8f0;--dim:#64748b;--mut:#334155;\n  --font-disp:\'Orbitron\',monospace;--font-body:\'Vazirmatn\',sans-serif;\n}\nbody[data-theme="neon"]{\n  --bg:#05060a;--bg2:#0a0d16;--bg3:#0e1220;--sur:#13172a;--bor:#251b3d;\n  --ac:#ff2e92;--ac2:#00ffd5;--ac3:#fff700;--red:#ff3860;\n}\nbody[data-theme="darkred"]{\n  --bg:#0a0505;--bg2:#120808;--bg3:#1a0a0a;--sur:#1e0e0e;--bor:#3a1414;\n  --ac:#ff3b3b;--ac2:#ff8a00;--ac3:#ffd24d;--red:#ff0000;\n}\nbody[data-theme="aurora"]{\n  --bg:#060a12;--bg2:#0a1220;--bg3:#0e1a2e;--sur:#11203a;--bor:#1c3a5c;\n  --ac:#4dd0ff;--ac2:#7cffb2;--ac3:#a78bfa;--red:#ff6b81;\n}\nbody[data-theme="telegram"]{\n  --bg:#0e1621;--bg2:#17212b;--bg3:#1e2c3a;--sur:#17212b;--bor:#0b141c;\n  --ac:#2aabee;--ac2:#5288c1;--ac3:#4fae4e;--red:#e25050;\n  --tx:#e1e6eb;--dim:#7d8b99;--mut:#52606d;\n}\n\n*{box-sizing:border-box;margin:0;padding:0}\nhtml,body{height:100%;background:var(--bg);color:var(--tx);font-family:var(--font-body);overflow:hidden}\ncanvas{position:fixed;inset:0;pointer-events:none;z-index:0;display:none}\nbutton,input,select,textarea{font-family:var(--font-body)}\nul{list-style:none}\n\n/* ── SCREENS ───────────────────────────────────────────────────────── */\n.scr{position:fixed;inset:0;z-index:10;display:none}\n.scr.on{display:flex}\n@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}\n.scr.on{animation:fi .3s ease}\n\n/* ── AUTH ──────────────────────────────────────────────────────────── */\n#aScr{align-items:center;justify-content:center}\n.card{background:var(--sur);border:1px solid var(--bor);border-radius:20px;padding:38px 34px;width:100%;max-width:410px;position:relative;z-index:1;box-shadow:0 0 60px rgba(0,0,0,.6)}\n.logo{text-align:center;margin-bottom:26px}\n.hex{width:68px;height:68px;background:linear-gradient(135deg,var(--ac),var(--ac2));clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);display:inline-flex;align-items:center;justify-content:center;animation:spin 8s linear infinite}\n.hex span{font-family:var(--font-disp);font-size:24px;font-weight:900;color:#fff}\n.ltitle{font-family:var(--font-disp);font-size:30px;font-weight:900;letter-spacing:6px;background:linear-gradient(90deg,var(--ac),#fff,var(--ac2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-top:8px}\n.lsub{color:var(--dim);font-size:12px;letter-spacing:2px;margin-top:3px}\n.tabs{display:flex;background:var(--bg2);border-radius:10px;padding:4px;margin-bottom:22px}\n.tab{flex:1;background:transparent;border:none;color:var(--dim);padding:8px;border-radius:8px;cursor:pointer;font-size:14px;font-weight:700;transition:.2s}\n.tab.on{background:linear-gradient(135deg,var(--ac2),#c026d3);color:#fff}\n.fld{margin-bottom:14px}\n.fld label{display:block;font-size:11px;color:var(--dim);margin-bottom:5px;letter-spacing:1px}\n.fld input,.fld select,.fld textarea{width:100%;background:var(--bg2);border:1px solid var(--bor);color:var(--tx);padding:10px 13px;border-radius:8px;font-size:14px;outline:none;transition:.2s;resize:vertical}\n.fld input:focus,.fld select:focus,.fld textarea:focus{border-color:var(--ac);box-shadow:0 0 0 3px rgba(0,212,255,.15)}\n.err{color:var(--red);font-size:12px;margin-bottom:9px;min-height:17px}\n.btnp{width:100%;background:linear-gradient(135deg,var(--ac),var(--ac2));color:#fff;border:none;padding:12px;border-radius:10px;font-family:var(--font-disp);font-size:12px;font-weight:700;letter-spacing:2px;cursor:pointer;transition:.2s}\n.btnp:hover{transform:translateY(-2px);box-shadow:0 0 30px rgba(0,212,255,.4)}\n.btnp:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--ac);outline-offset:2px}\n.afoot{text-align:center;margin-top:18px;font-size:12px;color:var(--dim)}\n.afoot strong{color:var(--ac)}\n.hid{display:none!important}\n\n/* ── MAIN APP LAYOUT ──────────────────────────────────────────────── */\n#cScr{flex-direction:row}\n.rail{width:230px;background:var(--sur);border-left:1px solid var(--bor);display:flex;flex-direction:column;padding:14px 10px;gap:6px;overflow-y:auto;flex-shrink:0}\n.slogo{display:flex;align-items:center;gap:8px;font-family:var(--font-disp);font-size:14px;font-weight:900;letter-spacing:3px;color:var(--ac);padding:0 4px 12px;border-bottom:1px solid var(--bor);margin-bottom:6px}\n.shex{width:28px;height:28px;background:linear-gradient(135deg,var(--ac),var(--ac2));clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0}\n.shex span{font-family:var(--font-disp);font-size:11px;font-weight:900;color:#fff}\n.rail-section{margin-bottom:4px}\n.slbl{font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:var(--mut);padding:8px 4px 4px}\n.roomlist{display:flex;flex-direction:column;gap:2px}\n.room-item{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;font-size:12.5px;color:var(--dim);transition:.15s;border:1px solid transparent}\n.room-item:hover{background:var(--bg2);color:var(--tx)}\n.room-item.active{background:rgba(0,212,255,.12);border-color:rgba(0,212,255,.3);color:var(--ac)}\n.room-icon{font-size:14px}\n.room-icon-img{width:18px;height:18px;border-radius:6px;background-size:cover;background-position:center;display:inline-block;flex-shrink:0}\n.room-icon-picker{display:flex;align-items:center;gap:10px}\n.room-icon-preview{width:46px;height:46px;border-radius:12px;background:var(--bg2);border:1px solid var(--bor);display:flex;align-items:center;justify-content:center;font-size:22px;background-size:cover;background-position:center;flex-shrink:0}\n.room-icon-picker-actions{flex:1;display:flex;flex-direction:column;gap:6px}\n.room-icon-picker-actions input{width:100%;background:var(--bg2);border:1px solid var(--bor);color:var(--tx);padding:7px 10px;border-radius:8px;font-size:12px;outline:none}\n.room-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.room-online{font-size:10px;color:var(--mut);background:var(--bg2);padding:1px 6px;border-radius:10px}\n.room-lock{font-size:10px;opacity:.6}\n.btn-create-room{margin:8px 2px;background:transparent;border:1px dashed var(--bor);color:var(--dim);padding:8px;border-radius:8px;cursor:pointer;font-size:12px;transition:.2s}\n.btn-create-room:hover{border-color:var(--ac);color:var(--ac)}\n.btn-join-code{width:100%;margin-top:6px;background:rgba(124,58,237,.12);border:1px solid rgba(124,58,237,.4);color:var(--ac2);padding:7px;border-radius:8px;cursor:pointer;font-size:11.5px;transition:.2s}\n.btn-join-code:hover{background:rgba(124,58,237,.22);border-color:var(--ac2)}\n.private-room-note{background:var(--bg2);border:1px solid var(--bor);border-radius:8px;padding:10px 12px;font-size:11.5px;color:var(--dim);line-height:1.8}\n.invite-code-row{display:flex;align-items:center;gap:8px}\n.invite-code-box{flex:1;background:var(--bg2);border:1px solid var(--ac);border-radius:8px;padding:10px;text-align:center;font-family:var(--font-disp);font-size:18px;letter-spacing:4px;color:var(--ac3)}\n.rail-bottom{margin-top:auto;display:flex;flex-direction:column;gap:4px;padding-top:10px;border-top:1px solid var(--bor)}\n.navbtn{display:flex;align-items:center;gap:8px;background:transparent;border:none;color:var(--dim);padding:9px;border-radius:8px;cursor:pointer;font-size:12.5px;text-align:right;transition:.15s}\n.navbtn:hover{background:var(--bg2);color:var(--ac)}\n\n.cmain{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}\n.chdr{background:var(--sur);border-bottom:1px solid var(--bor);padding:11px 18px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}\n.chl{display:flex;align-items:center;gap:10px}\n.chr{display:flex;align-items:center;gap:12px}\n.hdot{width:9px;height:9px;border-radius:50%;background:var(--ac3);box-shadow:0 0 8px var(--ac3);animation:pulse 2s infinite}\n.chdr h2{font-family:var(--font-disp);font-size:13px;letter-spacing:2px;color:var(--ac)}\n.obadge{font-size:11px;color:var(--dim);display:block}\n.myprofile{display:flex;align-items:center;gap:7px;cursor:pointer;font-size:12px;color:var(--ac);font-family:var(--font-disp);padding:4px 8px;border-radius:8px;transition:.15s}\n.myprofile:hover{background:var(--bg2)}\n.site-online-badge{background:var(--bg2);border:1px solid var(--bor);color:var(--ac3);font-size:11px;padding:5px 10px;border-radius:14px;white-space:nowrap}\n.iconbtn{background:transparent;border:1px solid var(--bor);color:var(--dim);width:34px;height:34px;border-radius:8px;cursor:pointer;font-size:15px;display:flex;align-items:center;justify-content:center;transition:.2s;flex-shrink:0}\n.iconbtn:hover{border-color:var(--ac);color:var(--ac)}\n.iconbtn-plus{font-size:24px;font-weight:300;line-height:1}\n.iconbtn.sm{width:26px;height:26px;font-size:11px;font-weight:700}\n.iconbtn.mono{font-family:monospace}\n\n.mini-av,.uav{width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0;background-size:cover;background-position:center;overflow:hidden}\n\n.msgs{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:10px;scroll-behavior:smooth}\n#chatViewWrap{flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0}\n\n.call-screen{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:40px 20px;text-align:center}\n.call-room-icon{width:96px;height:96px;border-radius:24px;background:linear-gradient(135deg,var(--ac),var(--ac2));display:flex;align-items:center;justify-content:center;font-size:42px;background-size:cover;background-position:center;margin-bottom:6px;box-shadow:0 8px 30px rgba(0,0,0,.25)}\n.call-room-title{font-family:var(--font-disp);font-size:18px;color:var(--tx)}\n.call-status{color:var(--dim);font-size:13px;margin-bottom:6px}\n.call-participants{display:flex;flex-wrap:wrap;gap:14px;justify-content:center;max-width:520px;margin-bottom:10px}\n.call-participant{display:flex;flex-direction:column;align-items:center;gap:6px;font-size:11px;color:var(--dim)}\n.call-participant-av{width:56px;height:56px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:#fff;background-size:cover;background-position:center;border:2px solid var(--ac3);box-shadow:0 0 0 3px rgba(57,255,20,.15)}\n.call-actions{display:flex;gap:12px;margin-top:10px}\n.call-btn{border:none;border-radius:30px;padding:13px 28px;font-size:14px;font-weight:700;cursor:pointer;transition:.2s}\n.call-btn-join{background:linear-gradient(135deg,var(--ac3),#2bb673);color:#06120a}\n.call-btn-join:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(57,255,20,.3)}\n.call-btn-leave{background:var(--red);color:#fff}\n.call-btn-leave:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(255,71,87,.35)}\n.msgs::-webkit-scrollbar{width:3px}\n.msgs::-webkit-scrollbar-thumb{background:var(--bor);border-radius:2px}\n.msg{display:flex;gap:9px;max-width:78%;animation:mslide .25s ease;position:relative}\n.msg.me{flex-direction:row-reverse;align-self:flex-end}\n.mav{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;flex-shrink:0;border:2px solid rgba(255,255,255,.1);background-size:cover;background-position:center;cursor:pointer}\n.mmeta{font-size:10px;color:var(--dim);margin-bottom:3px;display:flex;gap:7px;align-items:center}\n.msg.me .mmeta{flex-direction:row-reverse}\n.mname{font-weight:700;color:var(--tx);font-size:11px;cursor:pointer}\n.mname.role-admin{color:var(--ac3)}\n.mname.role-admin::after{content:" 🛡️";font-size:9px}\n.mtime{color:var(--mut);font-size:10px}\n.mrank{font-size:10px}\n.mbub-wrap{position:relative}\n.mbub{background:var(--sur);border:1px solid var(--bor);border-radius:12px 12px 12px 3px;padding:9px 13px;font-size:13px;line-height:1.7;word-break:break-word}\n.msg.me .mbub{background:linear-gradient(135deg,rgba(0,212,255,.13),rgba(124,58,237,.13));border-color:rgba(0,212,255,.28);border-radius:12px 12px 3px 12px}\n.mbub code{background:rgba(0,0,0,.35);padding:1px 6px;border-radius:5px;font-family:monospace;font-size:12px}\n.mbub .spoiler{background:#444;color:transparent;border-radius:4px;cursor:pointer;transition:.15s;padding:0 2px}\n.mbub .spoiler.revealed{background:rgba(255,255,255,.06);color:inherit}\n.mreply{font-size:11px;color:var(--dim);border-right:2px solid var(--ac);padding:3px 8px;margin-bottom:5px;border-radius:4px;background:rgba(255,255,255,.03);cursor:pointer}\n.mreply b{color:var(--ac)}\n.mmedia{max-width:260px;border-radius:10px;margin-top:6px;cursor:pointer;display:block}\n.mactions{position:absolute;top:-14px;display:flex;gap:3px;background:var(--bg2);border:1px solid var(--bor);border-radius:20px;padding:3px;opacity:0;transition:.15s}\n.msg.me .mactions{left:-8px}\n.msg:not(.me) .mactions{right:-8px}\n.msg:hover .mactions{opacity:1}\n.mact-btn{background:transparent;border:none;color:var(--dim);width:22px;height:22px;border-radius:50%;cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;transition:.15s}\n.mact-btn:hover{background:var(--bg3);color:var(--ac)}\n.mreactions{display:flex;gap:4px;margin-top:5px;flex-wrap:wrap}\n.mreaction{background:var(--bg2);border:1px solid var(--bor);border-radius:12px;padding:1px 7px;font-size:11px;cursor:pointer;display:flex;gap:3px;align-items:center;transition:.15s}\n.mreaction:hover{border-color:var(--ac)}\n.mreaction.mine{border-color:var(--ac);background:rgba(0,212,255,.1)}\n.emoji-picker{position:absolute;bottom:100%;background:var(--bg2);border:1px solid var(--bor);border-radius:10px;padding:6px;display:flex;gap:4px;z-index:50;box-shadow:0 4px 20px rgba(0,0,0,.4)}\n.emoji-picker span{cursor:pointer;font-size:16px;padding:3px;border-radius:6px;transition:.15s}\n.emoji-picker span:hover{background:var(--bg3)}\n\n.sticker-picker{background:var(--bg2);border-top:1px solid var(--bor);padding:10px 14px;max-height:220px;overflow-y:auto}\n.sticker-pack{margin-bottom:10px}\n.sticker-pack-name{font-size:11px;color:var(--dim);margin-bottom:6px}\n.sticker-pack-grid{display:flex;flex-wrap:wrap;gap:4px}\n.sticker-btn{background:var(--bg3);border:1px solid var(--bor);border-radius:8px;width:38px;height:38px;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:.15s}\n.sticker-btn:hover{border-color:var(--ac);transform:scale(1.1)}\n.mbub-sticker{background:transparent!important;border:none!important;padding:0!important}\n.sticker-msg{font-size:52px;line-height:1;display:inline-block}\n.msys{text-align:center;font-size:11px;color:var(--mut);padding:3px 14px;background:var(--bg2);border-radius:20px;align-self:center;max-width:80%;font-style:italic;white-space:pre-line}\n\n.reply-preview{background:var(--bg2);border-top:1px solid var(--bor);padding:8px 16px;display:flex;justify-content:space-between;align-items:center;font-size:12px}\n.reply-preview .rp-label{color:var(--ac)}\n.reply-preview .rp-text{color:var(--dim);font-size:11px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.reply-preview button{background:transparent;border:none;color:var(--dim);cursor:pointer;font-size:14px}\n\n.tbar{padding:5px 18px;font-size:11px;color:var(--dim);font-style:italic;min-height:22px}\n.tbar.h{opacity:0}\n.iarea{background:var(--sur);border-top:1px solid var(--bor);padding:11px 14px;display:flex;gap:7px;align-items:center;flex-shrink:0}\n.iarea input{flex:1;background:var(--bg2);border:1px solid var(--bor);color:var(--tx);padding:9px 15px;border-radius:24px;font-size:13px;outline:none;transition:.2s}\n.iarea input:focus{border-color:var(--ac);box-shadow:0 0 0 3px rgba(0,212,255,.1)}\n.bsnd{width:42px;height:42px;border-radius:50%;background:linear-gradient(135deg,var(--ac),var(--ac2));border:none;color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:.2s;font-size:18px}\n.bsnd:hover{transform:scale(1.1);box-shadow:0 0 20px rgba(0,212,255,.5)}\n\n/* ── RIGHT SIDE (members) ──────────────────────────────────────────── */\n.side{width:230px;background:var(--sur);border-right:1px solid var(--bor);display:flex;flex-direction:column;padding:14px;gap:10px;overflow-y:auto;flex-shrink:0}\n.pcard{border-radius:12px;background:linear-gradient(135deg,rgba(124,58,237,.2),rgba(0,212,255,.1));border:1px solid rgba(124,58,237,.4);padding:14px;text-align:center;position:relative}\n.pcard-flare{font-size:26px;margin-bottom:7px;animation:pulse 2s infinite}\n.pcard h3{font-family:var(--font-disp);font-size:11px;color:var(--ac);letter-spacing:2px;margin-bottom:5px}\n.pcard p{font-size:10px;color:var(--dim);line-height:1.6;margin-bottom:10px}\n.pcbtn{display:inline-block;background:linear-gradient(90deg,var(--ac2),#c026d3);color:#fff;text-decoration:none;padding:5px 16px;border-radius:20px;font-size:11px;font-weight:700}\n.ulist{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:3px}\n.ui{display:flex;align-items:center;gap:7px;padding:7px 9px;border-radius:8px;background:var(--bg2);font-size:12px;cursor:pointer;transition:.15s}\n.ui:hover{background:var(--bg3)}\n.ui-info{flex:1;overflow:hidden}\n.ui-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}\n.ui-game{font-size:9px;color:var(--ac2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}\n.udot{width:7px;height:7px;border-radius:50%;background:var(--ac3);margin-right:auto;box-shadow:0 0 8px var(--ac3);flex-shrink:0}\n.btnlo{background:transparent;border:1px solid var(--bor);color:var(--dim);padding:8px;border-radius:8px;cursor:pointer;font-size:12px;transition:.2s;margin-top:auto}\n.btnlo:hover{border-color:var(--red);color:var(--red)}\n\n/* ── OVERLAY / SLIDE PANELS ────────────────────────────────────────── */\n.overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;backdrop-filter:blur(2px)}\n.panel{position:fixed;top:0;left:0;height:100%;width:420px;max-width:92vw;background:var(--sur);border-right:1px solid var(--bor);z-index:101;display:flex;flex-direction:column;box-shadow:8px 0 40px rgba(0,0,0,.5);animation:slidein .25s ease}\n.panel.wide{width:680px}\n@keyframes slidein{from{transform:translateX(-100%)}to{transform:translateX(0)}}\n.panel-hdr{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;border-bottom:1px solid var(--bor)}\n.panel-hdr h3{font-family:var(--font-disp);font-size:14px;color:var(--ac);letter-spacing:1px}\n.panel-hdr button{background:transparent;border:none;color:var(--dim);font-size:18px;cursor:pointer}\n.panel-body{flex:1;overflow-y:auto;padding:16px 18px}\n\n/* DM panel */\n.dm-layout{flex:1;display:flex;overflow:hidden}\n.dm-list{width:180px;border-left:1px solid var(--bor);padding:12px;overflow-y:auto;flex-shrink:0}\n.dm-search{width:100%;background:var(--bg2);border:1px solid var(--bor);color:var(--tx);padding:7px 10px;border-radius:8px;font-size:12px;outline:none;margin-bottom:8px}\n.dm-user-row{display:flex;align-items:center;gap:7px;padding:7px;border-radius:8px;cursor:pointer;font-size:12px;transition:.15s}\n.dm-user-row:hover{background:var(--bg2)}\n.dm-active{flex:1;display:flex;flex-direction:column}\n.dm-active-hdr{display:flex;align-items:center;gap:8px;padding:12px 16px;border-bottom:1px solid var(--bor);font-size:13px;font-weight:700}\n\n/* Friends panel */\n.friend-list{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}\n.friend-row{display:flex;align-items:center;gap:9px;padding:8px;background:var(--bg2);border-radius:10px;font-size:12px}\n.friend-row .ui-name{flex:1}\n.friend-actions{display:flex;gap:5px}\n.btn-tiny{background:var(--bg3);border:1px solid var(--bor);color:var(--tx);padding:4px 9px;border-radius:6px;cursor:pointer;font-size:11px;transition:.15s}\n.btn-tiny:hover{border-color:var(--ac);color:var(--ac)}\n.btn-tiny.danger:hover{border-color:var(--red);color:var(--red)}\n\n/* Profile panel */\n.profile-edit{display:flex;flex-direction:column;align-items:center;gap:10px;margin-bottom:18px}\n.big-av{width:84px;height:84px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:700;color:#fff;background-size:cover;background-position:center}\n.profile-edit .fld{width:100%}\n.btn-secondary{background:transparent;border:1px solid var(--bor);color:var(--dim);padding:9px 14px;border-radius:8px;cursor:pointer;font-size:12px;transition:.2s}\n.btn-secondary:hover{border-color:var(--ac);color:var(--ac)}\n.btn-secondary.sm{padding:6px 11px;font-size:11px}\n.btn-secondary.danger:hover{border-color:var(--red);color:var(--red)}\n.color-row{display:flex;gap:6px;flex-wrap:wrap}\n.color-dot{width:22px;height:22px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:.15s}\n.color-dot.sel{border-color:#fff}\n.profile-stats{display:flex;gap:10px;flex-wrap:wrap}\n.admin-redeem-box{margin-top:16px;padding-top:14px;border-top:1px solid var(--bor)}\n.stat-chip{background:var(--bg2);border:1px solid var(--bor);border-radius:10px;padding:8px 12px;font-size:11px;color:var(--dim);flex:1;text-align:center}\n.stat-chip b{display:block;color:var(--ac);font-size:15px;font-family:var(--font-disp)}\n\n/* Admin panel */\n.admin-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}\n@media(max-width:700px){.admin-grid{grid-template-columns:1fr}}\n.admin-list{display:flex;flex-direction:column;gap:6px;max-height:300px;overflow-y:auto}\n.admin-report-row{background:var(--bg2);border-radius:8px;padding:8px;font-size:11px}\n.fld-row{display:flex;gap:6px;margin-bottom:10px}\n.fld-row input{flex:1;background:var(--bg2);border:1px solid var(--bor);color:var(--tx);padding:7px 10px;border-radius:8px;font-size:12px;outline:none}\n.word-tags{display:flex;flex-wrap:wrap;gap:6px}\n.word-tag{background:var(--bg2);border:1px solid var(--bor);border-radius:14px;padding:3px 10px;font-size:11px;display:flex;align-items:center;gap:5px}\n.word-tag button{background:transparent;border:none;color:var(--red);cursor:pointer;font-size:11px}\n\n/* Theme panel */\n.theme-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:18px}\n.theme-opt{background:var(--bg2);border:1px solid var(--bor);color:var(--tx);padding:12px;border-radius:10px;cursor:pointer;display:flex;align-items:center;gap:9px;font-size:12px;transition:.2s}\n.theme-opt:hover{border-color:var(--ac)}\n.theme-swatch{width:20px;height:20px;border-radius:50%;display:inline-block;flex-shrink:0}\n.theme-swatch.cyberpunk{background:linear-gradient(135deg,#00d4ff,#7c3aed)}\n.theme-swatch.neon{background:linear-gradient(135deg,#ff2e92,#00ffd5)}\n.theme-swatch.darkred{background:linear-gradient(135deg,#ff3b3b,#ff8a00)}\n.theme-swatch.aurora{background:linear-gradient(135deg,#4dd0ff,#7cffb2)}\n.theme-swatch.telegram{background:linear-gradient(135deg,#2aabee,#229ed9)}\n.switch-row{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--dim);margin-bottom:10px;cursor:pointer}\n\n/* Modal (create room) */\n.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;display:flex;align-items:center;justify-content:center}\n.modal-card{background:var(--sur);border:1px solid var(--bor);border-radius:16px;padding:26px;width:380px;max-width:92vw}\n.modal-card h3{font-family:var(--font-disp);font-size:14px;color:var(--ac);margin-bottom:16px}\n.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:6px}\n\n/* Lightbox */\n.lightbox{position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:300;display:flex;align-items:center;justify-content:center;cursor:zoom-out}\n.lightbox img{max-width:90vw;max-height:90vh;border-radius:8px}\n\n/* Toast */\n.toast{position:fixed;bottom:28px;left:50%;transform:translateX(-50%);background:var(--sur);border:1px solid var(--ac);color:var(--tx);padding:9px 22px;border-radius:22px;font-size:13px;z-index:9999;animation:fi .2s ease}\n\n@keyframes spin{to{transform:rotate(360deg)}}\n@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}\n@keyframes mslide{from{opacity:0;transform:translateX(14px)}to{opacity:1;transform:none}}\n\n/* ── RESPONSIVE ─────────────────────────────────────────────────────── */\n.railToggleBtn{display:none}\n.membersToggleBtn{display:none}\n.site-online-chip{background:var(--bg2);border:1px solid var(--bor);border-radius:10px;padding:8px 12px;font-size:12px;color:var(--ac3);margin-bottom:6px;display:flex;align-items:center;gap:6px}\n.site-online-chip::before{content:"●";color:var(--ac3);animation:pulse 2s infinite}\n@media(max-width:900px){\n  .side{display:none}\n  .membersToggleBtn{display:flex}\n}\n@media(max-width:680px){\n  .railToggleBtn{display:flex}\n  .rail{position:fixed;inset:0 auto 0 0;z-index:150;transform:translateX(-100%);transition:.25s;width:240px;box-shadow:8px 0 30px rgba(0,0,0,.5)}\n  .rail.open{transform:translateX(0)}\n  .rail-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:140;display:none}\n  .rail-backdrop.show{display:block}\n  .panel{width:100vw}\n  .panel.wide{width:100vw}\n  .chdr h2{font-size:11px}\n  .chr{gap:6px}\n  .site-online-badge{display:none}\n  .iarea{padding:8px 6px;gap:4px;flex-wrap:wrap}\n  .iarea input{min-width:0}\n  .msg{max-width:90%}\n}\n@media(prefers-reduced-motion: reduce){\n  *{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}\n}\n'

APP_JS = '// ════════════════════════════════════════════════════════════════════════\n//  LINE Chat — client app\n// ════════════════════════════════════════════════════════════════════════\n\'use strict\';\n\nlet ws = null, sess = null, myUser = null;\nlet currentRoom = null;\nlet replyTarget = null;\nlet dmReplyTarget = null;\nlet activeDmUserId = null;\nlet tTimer = null, tThrottle = false;\nlet soundEnabled = true;\nlet pendingMediaContext = \'chat\'; // \'chat\' | \'dm\' | \'avatar\'\nlet roomsCache = [];\n\n// ── چت صوتی (WebRTC) ──────────────────────────────────────────────────────\nlet voiceActive = false;\nlet localStream = null;\nconst peerConnections = {}; // user_id -> RTCPeerConnection\nconst remoteAudioEls = {};  // user_id -> <audio>\nconst RTC_CONFIG = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };\n\nconst AVATAR_COLORS = ["#00d4ff","#7c3aed","#39ff14","#ff6b35","#ffd700","#ff69b4","#00bfff","#ff4757","#a78bfa","#34d399"];\nconst QUICK_EMOJI = ["👍","❤️","😂","🔥","🎮","😮","😢","👏"];\nconst STICKER_PACKS = {\n  "گیمینگ 🎮": ["🎮","🕹️","🏆","💀","👑","🔫","🛡️","⚔️","🎯","💣","🧨","🚀"],\n  "ری\u200cاکشن 😂": ["😂","🤣","😭","😡","😱","🥶","🤯","😴","🙄","😎","🥳","🤡"],\n  "قلب ❤️": ["❤️","💙","💚","💜","🧡","💛","🖤","🤍","💖","💞","💗","💔"],\n  "حیوانات 🐾": ["🐶","🐱","🦊","🐻","🐼","🦁","🐸","🐵","🦄","🐲","🐧","🦉"],\n  "دست\u200cها 👋": ["👍","👎","👏","🙏","🤝","✌️","🤞","👌","🤙","💪","🫡","🖕"],\n};\n\n// ── WebSocket plumbing ────────────────────────────────────────────────────\nfunction wsUrl() {\n  const proto = location.protocol === "https:" ? "wss:" : "ws:";\n  return `${proto}//${location.host}/ws`;\n}\n\nfunction conn() {\n  ws = new WebSocket(wsUrl());\n  ws.onopen = () => console.log("WS connected");\n  ws.onmessage = e => handle(JSON.parse(e.data));\n  ws.onclose = () => setTimeout(conn, 3000);\n  ws.onerror = e => console.error(e);\n}\n\nfunction send(o) {\n  if (ws && ws.readyState === 1) ws.send(JSON.stringify(o));\n}\n\nconn();\n\n// ── Message dispatch ─────────────────────────────────────────────────────\nfunction handle(m) {\n  switch (m.type) {\n    case "register_result": return onRegisterResult(m);\n    case "login_result": return onLoginResult(m);\n    case "room_list": return renderRoomList(m.rooms);\n    case "joined_room": return onJoinedRoom(m);\n    case "join_room_result": return onJoinRoomResult(m);\n    case "create_room_result": return onCreateRoomResult(m);\n    case "join_by_code_result": return onJoinByCodeResult(m);\n    case "room_update_result": return onRoomUpdateResult(m);\n    case "room_renamed": return onRoomRenamed(m);\n    case "room_delete_result": return onRoomDeleteResult(m);\n    case "room_deleted": return onRoomDeleted(m);\n    case "room_regenerate_code_result": return onRoomRegenerateCodeResult(m);\n    case "message": return onIncomingMessage(m);\n    case "system": return onSystemMessage(m);\n    case "room_users": return renderRoomUsers(m);\n    case "typing": return showTyping(m.display_name, m.user_id);\n    case "typing_dm": return showDmTyping(m.from, m.display_name);\n    case "reaction_update": return onReactionUpdate(m);\n    case "message_deleted": return onMessageDeleted(m);\n    case "profile_updated": return onProfileUpdated(m);\n    case "friends": return renderFriends(m);\n    case "redeem_admin_result": return onRedeemAdminResult(m);\n    case "voice_presence": return onVoicePresence(m);\n    case "voice_signal": return onVoiceSignal(m);\n    case "site_online": return renderSiteOnline(m.count);\n    case "dm_opened": return onDmOpened(m);\n    case "report_result": return toast("✅ گزارش ارسال شد. ادمین\u200cها بررسی می\u200cکنند.");\n    case "reports_list": return renderReports(m.reports);\n    case "banned_words_list": return renderBannedWords(m.words);\n    case "new_report": return flashAdminNav();\n    case "admin_action_result": return onAdminActionResult(m);\n    case "banned": toast("⛔ اکانت شما مسدود شد."); return doLogout(true);\n    case "kicked": toast("👋 از سرور بیرون انداخته شدید."); return doLogout(true);\n    case "muted": return toast(`🔇 شما برای ${m.minutes} دقیقه میوت شدید.`);\n    case "error": return onError(m);\n  }\n}\n\nfunction onError(m) {\n  const map = { NOT_AUTHENTICATED: "لطفاً دوباره وارد شوید", MUTED: "شما الان میوت هستید", NOT_ADMIN: "دسترسی ادمین لازم است", ROOM_NOT_FOUND: "روم پیدا نشد", NOT_ROOM_OWNER: "فقط مالک روم یا ادمین می\u200cتونه این کارو بکنه" };\n  toast(map[m.msg] || "خطایی رخ داد");\n}\n\n// ── AUTH ──────────────────────────────────────────────────────────────────\nfunction doLogin() {\n  const u = document.getElementById("lu").value.trim(), p = document.getElementById("lp").value;\n  if (!u || !p) { setErr("le", "همه فیلدها رو پر کن"); return; }\n  send({ type: "login", username: u, password: p });\n}\nfunction doReg() {\n  const u = document.getElementById("ru").value.trim(), d = document.getElementById("rd").value.trim(), p = document.getElementById("rp").value;\n  const adminCode = document.getElementById("rAdminCode")?.value.trim() || "";\n  if (!u || !p) { setErr("re", "همه فیلدها رو پر کن"); return; }\n  send({ type: "register", username: u, password: p, display_name: d || u, admin_code: adminCode });\n}\nfunction onRegisterResult(m) {\n  if (m.ok) {\n    stab("L");\n    document.getElementById("lu").value = document.getElementById("ru").value;\n    toast(m.first_admin ? "✅ ثبت\u200cنام شد! با کد ادمین وارد شدی و ادمین شدی 👑" : "✅ ثبت\u200cنام با موفقیت انجام شد!");\n  } else {\n    const map = { USERNAME_TAKEN: "این نام کاربری قبلاً گرفته شده", INVALID_USERNAME: "نام کاربری باید ۳ تا ۲۰ حرف باشد", WEAK_PASSWORD: "رمز عبور باید حداقل ۴ کاراکتر باشد" };\n    setErr("re", map[m.msg] || "خطا در ثبت\u200cنام");\n  }\n}\nfunction onLoginResult(m) {\n  if (m.ok) {\n    sess = m.user; myUser = m.user;\n    document.getElementById("cusr").textContent = sess.display_name;\n    renderMiniAvatar();\n    show("cScr");\n    if (sess.role === "admin") document.getElementById("adminNavBtn").style.display = "flex";\n  } else {\n    const map = { USER_NOT_FOUND: "کاربر پیدا نشد", WRONG_PASSWORD: "رمز عبور اشتباهه", BANNED: "این اکانت مسدود شده است" };\n    setErr("le", map[m.msg] || "خطا در ورود");\n  }\n}\nfunction doLogout(silent) {\n  if (voiceActive) leaveVoiceChat();\n  if (!silent) send({ type: "logout" });\n  sess = null; myUser = null; currentRoom = null;\n  clrMsgs(); show("aScr");\n  if (ws) { ws.close(); }\n  setTimeout(conn, 400);\n}\n\n// ── ROOMS ─────────────────────────────────────────────────────────────────\nfunction renderRoomList(rooms) {\n  roomsCache = rooms;\n  const buckets = { public: [], game: [], voice: [], private: [] };\n  rooms.forEach(r => {\n    const bucket = (r.kind === \'private\' || r.is_private) ? \'private\' : (buckets[r.kind] ? r.kind : \'public\');\n    buckets[bucket].push(r);\n  });\n  fillRoomBucket("roomlistPublic", buckets.public);\n  fillRoomBucket("roomlistGame", buckets.game);\n  fillRoomBucket("roomlistVoice", buckets.voice);\n  fillRoomBucket("roomlistPrivate", buckets.private);\n}\nfunction roomIconHtml(r) {\n  if (r.icon_url) return `<span class="room-icon room-icon-img" style="background-image:url(\'${r.icon_url}\')"></span>`;\n  return `<span class="room-icon">${esc(r.icon || "💬")}</span>`;\n}\nfunction fillRoomBucket(elId, rooms) {\n  const el = document.getElementById(elId);\n  el.innerHTML = "";\n  rooms.forEach(r => {\n    const li = document.createElement("li");\n    li.className = "room-item" + (currentRoom && currentRoom.id === r.id ? " active" : "");\n    li.innerHTML = `${roomIconHtml(r)}<span class="room-name">${esc(r.name)}</span>${r.is_private ? \'<span class="room-lock">🔒</span>\' : \'\'}<span class="room-online">${r.online}</span>`;\n    li.onclick = () => joinRoom(r);\n    el.appendChild(li);\n  });\n}\nfunction joinRoom(r) {\n  // روم\u200cهای خصوصی که توی لیست کاربر می\u200cبینه یعنی قبلاً عضوشون شده (با کد یا چون خودش ساخته)،\n  // پس دیگه نیازی به وارد کردن چیزی نیست — سرور خودش این رو چک می\u200cکنه.\n  send({ type: "join_room", room_id: r.id });\n}\nfunction onJoinedRoom(m) {\n  if (voiceActive && currentRoom && currentRoom.id !== m.room.id) {\n    leaveVoiceChat();\n  }\n  currentRoom = m.room;\n  document.getElementById("chTitle").textContent = `${m.room.icon} ${m.room.name}`;\n  document.getElementById("roomManageBtn").classList.toggle("hid", !m.room.can_manage);\n\n  const isVoiceRoom = m.room.kind === "voice";\n  document.getElementById("callScreen").classList.toggle("hid", !isVoiceRoom);\n  document.getElementById("chatViewWrap").classList.toggle("hid", isVoiceRoom);\n  if (isVoiceRoom) {\n    const callIcon = document.getElementById("callRoomIcon");\n    if (m.room.icon_url) {\n      callIcon.style.backgroundImage = `url(\'${m.room.icon_url}\')`;\n      callIcon.textContent = "";\n    } else {\n      callIcon.style.backgroundImage = "";\n      callIcon.textContent = m.room.icon || "🎙️";\n    }\n    document.getElementById("callRoomTitle").textContent = m.room.name;\n    refreshCallButtons();\n  }\n\n  clrMsgs();\n  m.history.forEach(appendMsg);\n  scrollBot();\n  renderRoomList(roomsCache);\n  closeRailMobile();\n}\nfunction onJoinRoomResult(m) {\n  if (!m.ok) {\n    const map = { PRIVATE_NEEDS_CODE: "این روم خصوصیه — برای ورود باید کد دعوتش رو داشته باشی" };\n    toast(map[m.msg] || "نمی\u200cتونی وارد این روم بشی");\n  }\n}\nfunction openCreateRoom() {\n  document.getElementById("createRoomModal").classList.remove("hid");\n  const isAdmin = sess && sess.role === "admin";\n  document.getElementById("newRoomKindWrap").classList.toggle("hid", !isAdmin);\n  document.getElementById("newRoomKindLockedNote").classList.toggle("hid", isAdmin);\n  if (!isAdmin) document.getElementById("newRoomKind").value = "private";\n  document.getElementById("newRoomIconUrl").value = "";\n  document.getElementById("newRoomIconPreview").style.backgroundImage = "";\n  document.getElementById("newRoomIconPreview").textContent = document.getElementById("newRoomIcon").value || "🎮";\n  onRoomKindChange();\n}\nfunction closeCreateRoom() {\n  document.getElementById("createRoomModal").classList.add("hid");\n  document.getElementById("createRoomErr").textContent = "";\n}\nfunction onRoomKindChange() {\n  const kind = document.getElementById("newRoomKind").value;\n  const isAdmin = sess && sess.role === "admin";\n  document.getElementById("newRoomPrivateNote").classList.toggle("hid", !(kind === "private" && isAdmin));\n}\nfunction onNewRoomEmojiInput() {\n  document.getElementById("newRoomIconUrl").value = "";\n  document.getElementById("newRoomIconPreview").style.backgroundImage = "";\n  document.getElementById("newRoomIconPreview").textContent = document.getElementById("newRoomIcon").value.trim() || "🎮";\n}\nfunction submitCreateRoom() {\n  const name = document.getElementById("newRoomName").value.trim();\n  const kind = document.getElementById("newRoomKind").value;\n  const icon = document.getElementById("newRoomIcon").value.trim() || "🎮";\n  const icon_url = document.getElementById("newRoomIconUrl").value || null;\n  if (!name) { document.getElementById("createRoomErr").textContent = "اسم روم را وارد کن"; return; }\n  send({ type: "create_room", name, kind, icon, icon_url });\n}\nfunction onCreateRoomResult(m) {\n  if (m.ok) {\n    closeCreateRoom();\n    document.getElementById("newRoomName").value = "";\n    if (m.invite_code) {\n      toastHTML(`🔑 روم خصوصی ساخته شد! کد دعوتش: <b style="font-family:var(--font-disp);letter-spacing:2px">${esc(m.invite_code)}</b><br>این کد رو به هرکی خواستی بدی تا وارد روم بشه.`);\n    }\n  } else {\n    const map = { NAME_REQUIRED: "اسم روم را وارد کن" };\n    document.getElementById("createRoomErr").textContent = map[m.msg] || "خطا در ساخت روم";\n  }\n}\n\n// ── ورود به روم خصوصی با کد دعوت ─────────────────────────────────────────\nfunction openJoinByCodeModal() {\n  document.getElementById("joinByCodeModal").classList.remove("hid");\n  document.getElementById("joinCodeErr").textContent = "";\n  document.getElementById("joinCodeInput").value = "";\n  closeRailMobile();\n}\nfunction closeJoinByCodeModal() {\n  document.getElementById("joinByCodeModal").classList.add("hid");\n}\nfunction submitJoinByCode() {\n  const code = document.getElementById("joinCodeInput").value.trim();\n  if (!code) { document.getElementById("joinCodeErr").textContent = "کد رو وارد کن"; return; }\n  send({ type: "join_by_code", code });\n}\nfunction onJoinByCodeResult(m) {\n  if (m.ok) {\n    closeJoinByCodeModal();\n    toast(`✅ وارد روم «${m.room_name}» شدی`);\n  } else {\n    const map = { EMPTY_CODE: "کد رو وارد کن", CODE_NOT_FOUND: "این کد معتبر نیست" };\n    document.getElementById("joinCodeErr").textContent = map[m.msg] || "خطا در ورود به روم";\n  }\n}\n\n// ── مدیریت روم (ادیت، حذف، تغییر کد) — فقط برای مالک روم یا ادمین ───────\nlet pendingManageIconUrl = null;\nfunction fillRoomManageForm() {\n  if (!currentRoom) return;\n  document.getElementById("manageRoomName").value = currentRoom.name || "";\n  document.getElementById("manageRoomIcon").value = currentRoom.icon || "";\n  pendingManageIconUrl = currentRoom.icon_url || null;\n  updateManageIconPreview();\n  const isPrivate = currentRoom.kind === "private";\n  document.getElementById("manageInviteCodeWrap").classList.toggle("hid", !isPrivate);\n  if (isPrivate) {\n    document.getElementById("manageInviteCode").textContent = currentRoom.invite_code || "------";\n  }\n}\nfunction updateManageIconPreview() {\n  const prev = document.getElementById("manageRoomIconPreview");\n  if (pendingManageIconUrl) {\n    prev.style.backgroundImage = `url(\'${pendingManageIconUrl}\')`;\n    prev.textContent = "";\n  } else {\n    prev.style.backgroundImage = "";\n    prev.textContent = document.getElementById("manageRoomIcon").value.trim() || "💬";\n  }\n}\nfunction onManageRoomEmojiInput() {\n  pendingManageIconUrl = null;\n  updateManageIconPreview();\n}\nfunction saveRoomEdit() {\n  if (!currentRoom) return;\n  const name = document.getElementById("manageRoomName").value.trim();\n  const icon = document.getElementById("manageRoomIcon").value.trim();\n  send({ type: "room_update", room_id: currentRoom.id, name, icon, icon_url: pendingManageIconUrl });\n}\nfunction onRoomUpdateResult(m) {\n  if (!m.ok) { toast("❌ خطا در ویرایش روم"); return; }\n  if (currentRoom) {\n    currentRoom.name = m.name;\n    currentRoom.icon = m.icon;\n    currentRoom.icon_url = m.icon_url;\n    document.getElementById("chTitle").textContent = `${m.icon} ${m.name}`;\n  }\n  toast("💾 روم ویرایش شد");\n}\nfunction onRoomRenamed(m) {\n  if (currentRoom && currentRoom.id === m.room_id) {\n    currentRoom.name = m.name;\n    currentRoom.icon = m.icon;\n    currentRoom.icon_url = m.icon_url;\n    document.getElementById("chTitle").textContent = `${m.icon} ${m.name}`;\n  }\n}\nfunction copyInviteCode() {\n  const code = document.getElementById("manageInviteCode").textContent;\n  if (navigator.clipboard) {\n    navigator.clipboard.writeText(code).then(() => toast("📋 کد کپی شد")).catch(() => toast(`کد: ${code}`));\n  } else {\n    toast(`کد: ${code}`);\n  }\n}\nfunction regenerateRoomCode() {\n  if (!currentRoom) return;\n  if (!confirm("کد قبلی دیگه کار نمی\u200cکنه. مطمئنی؟")) return;\n  send({ type: "room_regenerate_code", room_id: currentRoom.id });\n}\nfunction onRoomRegenerateCodeResult(m) {\n  if (!m.ok) { toast("❌ خطا در تغییر کد"); return; }\n  if (currentRoom) currentRoom.invite_code = m.invite_code;\n  document.getElementById("manageInviteCode").textContent = m.invite_code;\n  toast("🔄 کد جدید ساخته شد");\n}\nfunction deleteRoomConfirm() {\n  if (!currentRoom) return;\n  if (!confirm(`روم «${currentRoom.name}» و همه\u200cی پیام\u200cهاش برای همیشه حذف می\u200cشه. مطمئنی؟`)) return;\n  send({ type: "room_delete", room_id: currentRoom.id });\n}\nfunction onRoomDeleteResult(m) {\n  if (m.ok) {\n    closeAllPanels();\n    toast("🗑️ روم حذف شد");\n  } else {\n    const map = { ROOM_PROTECTED: "این روم پایه\u200cایه و نمی\u200cشه حذفش کرد" };\n    toast(map[m.msg] || "❌ نمی\u200cتونی این روم رو حذف کنی");\n  }\n}\nfunction onRoomDeleted(m) {\n  toast("⚠️ این روم توسط مالکش حذف شد");\n}\n\n\n// ── CHAT MESSAGES ─────────────────────────────────────────────────────────\nfunction onIncomingMessage(m) {\n  if (m.dm_key) {\n    handleIncomingDm(m);\n    return;\n  }\n  if (!currentRoom || m.room_id !== currentRoom.id) return;\n  appendMsg(m);\n  scrollBot();\n  if (m.user_id !== sess?.id && soundEnabled) beep();\n}\nfunction onSystemMessage(m) {\n  if (!currentRoom || m.room_id !== currentRoom.id) return;\n  appendSys(m.text);\n  scrollBot();\n}\n\nfunction sendMsg() {\n  const i = document.getElementById("mi"), tx = i.value.trim();\n  if (!tx || !sess || !currentRoom) return;\n  send({ type: "chat", text: tx, reply_to: replyTarget?.id || null });\n  i.value = ""; cancelReply();\n}\nfunction hkey(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); } }\nfunction styping() {\n  if (tThrottle || !currentRoom) return;\n  tThrottle = true; send({ type: "typing" });\n  setTimeout(() => { tThrottle = false; }, 2000);\n}\nfunction showTyping(name, uid) {\n  if (uid === sess?.id) return;\n  const b = document.getElementById("tbar");\n  b.textContent = `${name} در حال تایپ...`;\n  b.classList.remove("h");\n  clearTimeout(tTimer);\n  tTimer = setTimeout(() => b.classList.add("h"), 3000);\n}\n\nfunction formatText(raw) {\n  let s = esc(raw);\n  s = s.replace(/```([^`]+)```/g, \'<code>$1</code>\');\n  s = s.replace(/`([^`]+)`/g, \'<code>$1</code>\');\n  s = s.replace(/\\*\\*([^*]+)\\*\\*/g, \'<b>$1</b>\');\n  s = s.replace(/\\|\\|([^|]+)\\|\\|/g, \'<span class="spoiler" onclick="this.classList.toggle(\\\'revealed\\\')">$1</span>\');\n  s = s.replace(/\\n/g, \'<br>\');\n  return s;\n}\n\nfunction appendMsg(m) {\n  const c = document.getElementById("msgs"), me = m.user_id === sess?.id;\n  const d = document.createElement("div");\n  d.className = `msg${me ? " me" : ""}`;\n  d.dataset.msgId = m.id;\n  if (m.deleted) { d.innerHTML = `<div class="msys">پیام حذف شده توسط ادمین</div>`; c.appendChild(d); return; }\n\n  const av = avatarHtml(m, "mav");\n  const roleClass = m.role === "admin" ? "role-admin" : "";\n\n  let replyHtml = "";\n  if (m.reply) {\n    replyHtml = `<div class="mreply" onclick="scrollToMsg(${m.reply.id})"><b>${esc(m.reply.display_name)}</b>: ${esc(m.reply.text)}</div>`;\n  }\n  let mediaHtml = "";\n  if (m.media_url) {\n    mediaHtml = `<img class="mmedia" src="${m.media_url}" onclick="openLightbox(\'${m.media_url}\')" loading="lazy"/>`;\n  }\n\n  const bubbleContent = m.is_sticker\n    ? `<span class="sticker-msg">${esc(m.text)}</span>`\n    : `${m.text ? formatText(m.text) : ""}${mediaHtml}`;\n  const bubbleClass = m.is_sticker ? "mbub mbub-sticker" : "mbub";\n\n  d.innerHTML = `\n    ${av}\n    <div class="mbub-wrap">\n      <div class="mmeta">\n        <span class="mname ${roleClass}" onclick="openUserCard(${m.user_id})">${esc(m.display_name)}</span>\n        <span class="mtime">${fmtTime(m.ts)}</span>\n      </div>\n      ${replyHtml}\n      <div class="${bubbleClass}">${bubbleContent}</div>\n      <div class="mreactions" id="rx-${m.id}"></div>\n      <div class="mactions">\n        <button class="mact-btn" onclick="toggleEmojiPicker(${m.id}, event)" title="ری\u200cاکشن">😀</button>\n        <button class="mact-btn" onclick="startReply(${m.id}, \'${escAttr(m.display_name)}\', \'${escAttr((m.text||\'\').slice(0,80))}\')" title="ریپلای">↩️</button>\n        <button class="mact-btn" onclick="reportMessage(${m.id}, ${m.user_id})" title="گزارش">🚩</button>\n        ${sess && sess.role === \'admin\' ? `<button class="mact-btn" onclick="adminDeleteMsg(${m.id})" title="حذف">🗑️</button>` : \'\'}\n      </div>\n    </div>`;\n  c.appendChild(d);\n  renderReactions(m.id, m.reactions || {});\n}\n\nfunction avatarHtml(m, cls) {\n  const init = (m.display_name || m.username || "?")[0].toUpperCase();\n  if (m.avatar_url) {\n    return `<div class="${cls}" style="background-image:url(\'${m.avatar_url}\')" onclick="openUserCard(${m.user_id})"></div>`;\n  }\n  return `<div class="${cls}" style="background:${m.avatar_color}" onclick="openUserCard(${m.user_id})">${init}</div>`;\n}\n\nfunction appendSys(tx) {\n  const c = document.getElementById("msgs"), d = document.createElement("div");\n  d.className = "msys"; d.textContent = tx; c.appendChild(d);\n}\nfunction clrMsgs() { document.getElementById("msgs").innerHTML = ""; }\nfunction scrollBot() { const m = document.getElementById("msgs"); m.scrollTop = m.scrollHeight; }\nfunction scrollToMsg(id) {\n  const el = document.querySelector(`[data-msg-id="${id}"]`);\n  if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.style.outline = "1px solid var(--ac)"; setTimeout(() => el.style.outline = "", 1200); }\n}\n\n// ── REPLY ─────────────────────────────────────────────────────────────────\nfunction startReply(id, name, text) {\n  replyTarget = { id, name, text };\n  document.getElementById("rpName").textContent = name;\n  document.getElementById("rpText").textContent = text;\n  document.getElementById("replyPreview").classList.remove("hid");\n  document.getElementById("mi").focus();\n}\nfunction cancelReply() { replyTarget = null; document.getElementById("replyPreview").classList.add("hid"); }\n\n// ── REACTIONS ─────────────────────────────────────────────────────────────\nfunction toggleEmojiPicker(msgId, evt) {\n  evt.stopPropagation();\n  document.querySelectorAll(".emoji-picker").forEach(p => p.remove());\n  const picker = document.createElement("div");\n  picker.className = "emoji-picker";\n  picker.innerHTML = QUICK_EMOJI.map(e => `<span onclick="reactTo(${msgId}, \'${e}\')">${e}</span>`).join("");\n  evt.target.closest(".mactions").appendChild(picker);\n  setTimeout(() => document.addEventListener("click", () => picker.remove(), { once: true }), 0);\n}\nfunction reactTo(msgId, emoji) {\n  send({ type: "react", message_id: msgId, emoji });\n  document.querySelectorAll(".emoji-picker").forEach(p => p.remove());\n}\nfunction onReactionUpdate(m) {\n  renderReactions(m.message_id, m.reactions);\n}\nfunction renderReactions(msgId, rx) {\n  const el = document.getElementById(`rx-${msgId}`);\n  if (!el) return;\n  el.innerHTML = Object.entries(rx).map(([emoji, users]) => {\n    const mine = sess && users.includes(sess.id);\n    return `<span class="mreaction${mine ? \' mine\' : \'\'}" onclick="reactTo(${msgId}, \'${emoji}\')">${emoji} ${users.length}</span>`;\n  }).join("");\n}\n\nfunction onMessageDeleted(m) {\n  const el = document.querySelector(`[data-msg-id="${m.message_id}"]`);\n  if (el) el.innerHTML = `<div class="msys">پیام حذف شده توسط ادمین</div>`;\n}\n\n// ── MEDIA UPLOAD ──────────────────────────────────────────────────────────\nfunction triggerMediaUpload(ctx) {\n  pendingMediaContext = ctx || \'chat\';\n  document.getElementById("mediaInput").click();\n}\nasync function onMediaSelected(evt) {\n  const file = evt.target.files[0];\n  evt.target.value = "";\n  if (!file) return;\n  if (file.size > 6 * 1024 * 1024) { toast("⚠️ حجم فایل باید کمتر از ۶ مگابایت باشد"); return; }\n  const fd = new FormData();\n  const uploadKind = pendingMediaContext === "avatar" ? "avatar"\n    : (pendingMediaContext === "room_icon_new" || pendingMediaContext === "room_icon_manage") ? "room_icon"\n    : "media";\n  fd.append("kind", uploadKind);\n  fd.append("file", file);\n  toast("⏳ در حال آپلود...");\n  try {\n    const res = await fetch("/upload", { method: "POST", body: fd });\n    const data = await res.json();\n    if (!data.ok) { toast("❌ آپلود ناموفق بود"); return; }\n    if (pendingMediaContext === "avatar") {\n      document.getElementById("profAvBig").style.backgroundImage = `url(\'${data.url}\')`;\n      document.getElementById("profAvBig").textContent = "";\n      send({ type: "update_profile", avatar_url: data.url });\n    } else if (pendingMediaContext === "room_icon_new") {\n      document.getElementById("newRoomIconUrl").value = data.url;\n      document.getElementById("newRoomIconPreview").style.backgroundImage = `url(\'${data.url}\')`;\n      document.getElementById("newRoomIconPreview").textContent = "";\n    } else if (pendingMediaContext === "room_icon_manage") {\n      pendingManageIconUrl = data.url;\n      updateManageIconPreview();\n      toast("📷 عکس انتخاب شد — برای ثبت نهایی «ذخیره تغییرات» رو بزن");\n    } else if (pendingMediaContext === "dm") {\n      if (!activeDmUserId) return;\n      send({ type: "dm_send", user_id: activeDmUserId, text: "", media_url: data.url, media_type: data.media_type, reply_to: dmReplyTarget?.id || null });\n    } else {\n      send({ type: "chat", text: "", media_url: data.url, media_type: data.media_type, reply_to: replyTarget?.id || null });\n      cancelReply();\n    }\n  } catch (e) { toast("❌ خطا در آپلود فایل"); }\n}\n\nfunction openLightbox(url) {\n  document.getElementById("lightboxImg").src = url;\n  document.getElementById("lightbox").classList.remove("hid");\n}\nfunction closeLightbox() { document.getElementById("lightbox").classList.add("hid"); }\n\n// ── TEXT FORMATTING TOOLBAR ──────────────────────────────────────────────\nfunction wrapSelection(open, close) {\n  const i = document.getElementById("mi");\n  const start = i.selectionStart, end = i.selectionEnd;\n  const val = i.value;\n  const selected = val.slice(start, end) || "متن";\n  i.value = val.slice(0, start) + open + selected + close + val.slice(end);\n  i.focus();\n  i.selectionStart = start + open.length;\n  i.selectionEnd = start + open.length + selected.length;\n}\nfunction wrapCode() { wrapSelection("`", "`"); }\nfunction insertEmoji() {\n  const i = document.getElementById("mi");\n  i.value += "😀";\n  i.focus();\n}\n\n// ── STICKERS ──────────────────────────────────────────────────────────────\nfunction toggleStickerPicker() {\n  const el = document.getElementById("stickerPicker");\n  const willShow = el.classList.contains("hid");\n  el.classList.toggle("hid");\n  if (willShow) renderStickerPicker();\n}\nfunction renderStickerPicker() {\n  const el = document.getElementById("stickerPicker");\n  el.innerHTML = Object.entries(STICKER_PACKS).map(([packName, stickers]) => `\n    <div class="sticker-pack">\n      <div class="sticker-pack-name">${esc(packName)}</div>\n      <div class="sticker-pack-grid">\n        ${stickers.map(s => `<button class="sticker-btn" onclick="sendSticker(\'${s}\')">${s}</button>`).join("")}\n      </div>\n    </div>\n  `).join("");\n}\nfunction sendSticker(sticker) {\n  if (!sess) return;\n  if (currentRoom) {\n    send({ type: "chat", text: sticker, is_sticker: true, reply_to: replyTarget?.id || null });\n    cancelReply();\n  } else if (activeDmUserId) {\n    send({ type: "dm_send", user_id: activeDmUserId, text: sticker, is_sticker: true, reply_to: dmReplyTarget?.id || null });\n  }\n  document.getElementById("stickerPicker").classList.add("hid");\n}\n\n// ── ROOM MEMBERS (right sidebar) ─────────────────────────────────────────\nfunction renderSiteOnline(count) {\n  const badge = document.getElementById("siteOnlineBadge");\n  if (badge) badge.textContent = `🌐 ${count}`;\n  const chip = document.getElementById("siteOnlineChipMobile");\n  if (chip) chip.textContent = `🌐 ${count} نفر آنلاین در کل سایت`;\n}\n\nlet lastRoomUsersList = [];\nfunction renderRoomUsers(m) {\n  if (!currentRoom || m.room_id !== currentRoom.id) {\n    renderRoomList(roomsCache);\n    return;\n  }\n  lastRoomUsersList = m.users;\n  document.getElementById("ocnt").textContent = `${m.users.length} آنلاین`;\n  const html = m.users.map(u => {\n    const av = u.avatar_url\n      ? `<div class="uav" style="background-image:url(\'${u.avatar_url}\')"></div>`\n      : `<div class="uav" style="background:${u.avatar_color}">${u.display_name[0].toUpperCase()}</div>`;\n    return `<li class="ui" onclick="openUserCard(${u.id})">${av}<div class="ui-info"><span class="ui-name">${esc(u.display_name)}</span>${u.current_game ? `<span class="ui-game">🎮 ${esc(u.current_game)}</span>` : \'\'}</div><span class="udot"></span></li>`;\n  }).join("");\n  const ul = document.getElementById("ulist");\n  if (ul) ul.innerHTML = html;\n  const ulMobile = document.getElementById("ulistMobile");\n  if (ulMobile) ulMobile.innerHTML = html;\n}\n\n// ── USER CARD (mini profile + actions) ───────────────────────────────────\nfunction openUserCard(uid) {\n  if (uid === sess?.id) { openPanel("profilePanel"); return; }\n  const actions = [\n    `<button class="btn-tiny" onclick="closeAllPanels();openDmWith(${uid})">💬 پیام خصوصی</button>`,\n    `<button class="btn-tiny" onclick="sendFriendRequest(${uid})">➕ افزودن دوست</button>`,\n  ];\n  if (sess && sess.role === "admin") {\n    actions.push(`<button class="btn-tiny danger" onclick="adminAction(\'admin_mute\', {user_id:${uid}, minutes:10})">🔇 میوت</button>`);\n    actions.push(`<button class="btn-tiny danger" onclick="adminAction(\'admin_kick\', {user_id:${uid}})">👋 کیک</button>`);\n    actions.push(`<button class="btn-tiny danger" onclick="adminAction(\'admin_ban\', {user_id:${uid}})">⛔ بن</button>`);\n  }\n  toastHTML(actions.join(" "));\n}\n\nfunction sendFriendRequest(uid) {\n  send({ type: "friend_request", user_id: uid });\n  toast("✅ درخواست دوستی ارسال شد");\n}\n\n// ── PROFILE ───────────────────────────────────────────────────────────────\nfunction openPanel(id) {\n  document.getElementById("overlay").classList.remove("hid");\n  document.querySelectorAll(".panel").forEach(p => p.classList.add("hid"));\n  document.getElementById(id).classList.remove("hid");\n  if (id === "profilePanel") fillProfileForm();\n  if (id === "friendsPanel") send({ type: "get_friends" });\n  if (id === "adminPanel") send({ type: "admin_get_reports" });\n  if (id === "roomManagePanel") fillRoomManageForm();\n}\nfunction closeAllPanels() {\n  document.getElementById("overlay").classList.add("hid");\n  document.querySelectorAll(".panel").forEach(p => p.classList.add("hid"));\n}\nfunction toggleRail() {\n  document.querySelector(".rail")?.classList.toggle("open");\n  document.getElementById("railBackdrop")?.classList.toggle("show");\n}\nfunction closeRailMobile() {\n  document.querySelector(".rail")?.classList.remove("open");\n  document.getElementById("railBackdrop")?.classList.remove("show");\n}\n\nfunction fillProfileForm() {\n  if (!sess) return;\n  document.getElementById("profDisplayName").value = sess.display_name || "";\n  document.getElementById("profStatus").value = sess.status || "";\n  document.getElementById("profBio").value = sess.bio || "";\n  document.getElementById("profGame").value = sess.current_game || "";\n  const avBig = document.getElementById("profAvBig");\n  if (sess.avatar_url) { avBig.style.backgroundImage = `url(\'${sess.avatar_url}\')`; avBig.textContent = ""; }\n  else { avBig.style.background = sess.avatar_color; avBig.textContent = (sess.display_name || "?")[0].toUpperCase(); }\n\n  const colorRow = document.getElementById("colorRow");\n  colorRow.innerHTML = AVATAR_COLORS.map(c => `<span class="color-dot${c === sess.avatar_color ? \' sel\' : \'\'}" style="background:${c}" onclick="pickColor(\'${c}\')"></span>`).join("");\n\n  const stats = document.getElementById("profStats");\n  stats.innerHTML = `\n    <div class="stat-chip"><b>${sess.message_count}</b>پیام</div>\n  `;\n\n  document.getElementById("adminRedeemBox")?.classList.toggle("hid", sess.role === "admin");\n}\nfunction redeemAdminCode() {\n  const code = document.getElementById("profAdminCode").value.trim();\n  if (!code) return;\n  send({ type: "redeem_admin_code", admin_code: code });\n}\nfunction onRedeemAdminResult(m) {\n  if (m.ok) {\n    sess = { ...sess, ...m.user };\n    document.getElementById("adminNavBtn").style.display = "flex";\n    document.getElementById("adminRedeemBox")?.classList.add("hid");\n    document.getElementById("profAdminCode").value = "";\n    toast("👑 تبریک! حالا ادمین هستی");\n  } else {\n    toast("❌ کد ادمین اشتباهه");\n  }\n}\nfunction pickColor(c) {\n  document.querySelectorAll(".color-dot").forEach(d => d.classList.remove("sel"));\n  event.target.classList.add("sel");\n  sess._pendingColor = c;\n}\nfunction saveProfile() {\n  const payload = {\n    type: "update_profile",\n    display_name: document.getElementById("profDisplayName").value,\n    status: document.getElementById("profStatus").value,\n    bio: document.getElementById("profBio").value,\n  };\n  if (sess._pendingColor) payload.avatar_color = sess._pendingColor;\n  send(payload);\n  const game = document.getElementById("profGame").value.trim();\n  send({ type: "set_game", game });\n  toast("💾 پروفایل ذخیره شد");\n}\nfunction onProfileUpdated(m) {\n  sess = { ...sess, ...m.user };\n  document.getElementById("cusr").textContent = sess.display_name;\n  renderMiniAvatar();\n}\nfunction renderMiniAvatar() {\n  const el = document.getElementById("myAvMini");\n  if (!el) return;\n  if (sess.avatar_url) { el.style.backgroundImage = `url(\'${sess.avatar_url}\')`; el.textContent = ""; }\n  else { el.style.background = sess.avatar_color; el.textContent = (sess.display_name || "?")[0].toUpperCase(); }\n}\n\n// ── FRIENDS ───────────────────────────────────────────────────────────────\nfunction renderFriends(m) {\n  renderFriendBucket("friendsIncoming", m.incoming, true);\n  renderFriendBucket("friendsOutgoing", m.outgoing, false);\n  renderFriendBucket("friendsList", m.friends, false, true);\n}\nfunction renderFriendBucket(elId, list, isIncoming, isFriend) {\n  const el = document.getElementById(elId);\n  if (!list.length) { el.innerHTML = `<div class="msys" style="align-self:flex-start">چیزی نیست</div>`; return; }\n  el.innerHTML = list.map(u => {\n    let actions = "";\n    if (isIncoming) {\n      actions = `<button class="btn-tiny" onclick="respondFriend(${u.id}, true)">✅ قبول</button><button class="btn-tiny danger" onclick="respondFriend(${u.id}, false)">❌ رد</button>`;\n    } else if (isFriend) {\n      actions = `<button class="btn-tiny" onclick="closeAllPanels();openDmWith(${u.id})">💬 پیام</button>`;\n    }\n    const av = u.avatar_url ? `<div class="uav" style="background-image:url(\'${u.avatar_url}\')"></div>` : `<div class="uav" style="background:${u.avatar_color}">${u.display_name[0].toUpperCase()}</div>`;\n    return `<div class="friend-row">${av}<span class="ui-name">${esc(u.display_name)} ${isFriend && u.online ? \'<span class="udot" style="display:inline-block;margin:0 0 0 4px"></span>\' : \'\'}</span><div class="friend-actions">${actions}</div></div>`;\n  }).join("");\n}\nfunction respondFriend(uid, accept) {\n  send({ type: "friend_respond", user_id: uid, accept });\n}\n\n// ── DMs ───────────────────────────────────────────────────────────────────\nconst dmHistories = {}; // uid -> [messages]\nfunction openDmWith(uid) {\n  openPanel("dmPanel");\n  send({ type: "dm_open", user_id: uid });\n}\nfunction onDmOpened(m) {\n  activeDmUserId = m.with.id;\n  dmHistories[m.with.id] = m.history;\n  document.getElementById("dmActive").classList.remove("hid");\n  document.getElementById("dmActiveName").textContent = m.with.display_name;\n  const av = document.getElementById("dmActiveAv");\n  if (m.with.avatar_url) { av.style.backgroundImage = `url(\'${m.with.avatar_url}\')`; av.textContent = ""; }\n  else { av.style.background = m.with.avatar_color; av.textContent = m.with.display_name[0].toUpperCase(); }\n  const box = document.getElementById("dmMsgs");\n  box.innerHTML = "";\n  m.history.forEach(msg => appendDmMsg(msg));\n  box.scrollTop = box.scrollHeight;\n}\nfunction appendDmMsg(m) {\n  const box = document.getElementById("dmMsgs");\n  const me = m.user_id === sess?.id;\n  const d = document.createElement("div");\n  d.className = `msg${me ? " me" : ""}`;\n  let mediaHtml = m.media_url ? `<img class="mmedia" src="${m.media_url}" onclick="openLightbox(\'${m.media_url}\')"/>` : "";\n  let replyHtml = m.reply ? `<div class="mreply"><b>${esc(m.reply.display_name)}</b>: ${esc(m.reply.text)}</div>` : "";\n  const bubbleContent = m.is_sticker ? `<span class="sticker-msg">${esc(m.text)}</span>` : `${m.text ? formatText(m.text) : ""}${mediaHtml}`;\n  const bubbleClass = m.is_sticker ? "mbub mbub-sticker" : "mbub";\n  d.innerHTML = `<div class="mbub-wrap"><div class="mmeta"><span class="mtime">${fmtTime(m.ts)}</span></div>${replyHtml}<div class="${bubbleClass}">${bubbleContent}</div></div>`;\n  box.appendChild(d);\n}\nfunction handleIncomingDm(m) {\n  const otherId = m.user_id === sess?.id ? null : m.user_id;\n  if (activeDmUserId && (m.user_id === activeDmUserId || m.user_id === sess?.id)) {\n    appendDmMsg(m);\n    const box = document.getElementById("dmMsgs");\n    box.scrollTop = box.scrollHeight;\n  } else if (otherId) {\n    toast(`💬 پیام جدید از ${m.display_name}`);\n  }\n}\nfunction sendDm() {\n  const i = document.getElementById("dmInput"), tx = i.value.trim();\n  if (!tx || !activeDmUserId) return;\n  send({ type: "dm_send", user_id: activeDmUserId, text: tx, reply_to: dmReplyTarget?.id || null });\n  i.value = ""; dmReplyTarget = null;\n}\nfunction dmKey(e) { if (e.key === "Enter") sendDm(); }\nfunction showDmTyping(fromId, name) {\n  if (fromId === activeDmUserId) toast(`${name} در حال نوشتن پیام است...`);\n}\nlet dmSearchTimer = null;\nfunction searchDmUsers(q) {\n  clearTimeout(dmSearchTimer);\n  const resultsEl = document.getElementById("dmSearchResults");\n  if (!q.trim()) { resultsEl.innerHTML = ""; return; }\n  // Search among currently visible room members + friends as a lightweight local search\n  dmSearchTimer = setTimeout(() => {\n    const ul = document.getElementById("ulist");\n    const matches = [...ul.querySelectorAll(".ui")].filter(li => li.textContent.toLowerCase().includes(q.toLowerCase()));\n    resultsEl.innerHTML = matches.length ? matches.map(li => li.outerHTML).join("") : `<div class="msys" style="align-self:flex-start">پیدا نشد</div>`;\n  }, 200);\n}\n\n// ── چت صوتی / تماس (WebRTC mesh، سیگنالینگ از طریق سرور) ──────────────────\nfunction refreshCallButtons() {\n  document.getElementById("callJoinBtn").classList.toggle("hid", voiceActive);\n  document.getElementById("callLeaveBtn").classList.toggle("hid", !voiceActive);\n  document.getElementById("callStatus").textContent = voiceActive ? "توی تماس هستی" : "برای شروع، وارد تماس شو";\n}\n\nasync function joinVoiceChat() {\n  try {\n    localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });\n  } catch (e) {\n    toast("⚠️ نتونستم به میکروفون دسترسی پیدا کنم. اجازه\u200cی دسترسی رو بده.");\n    return;\n  }\n  voiceActive = true;\n  refreshCallButtons();\n  send({ type: "voice_join" });\n}\n\nfunction leaveVoiceChat() {\n  voiceActive = false;\n  refreshCallButtons();\n  send({ type: "voice_leave" });\n  if (localStream) {\n    localStream.getTracks().forEach(t => t.stop());\n    localStream = null;\n  }\n  Object.keys(peerConnections).forEach(closePeerConnection);\n  updateCallParticipants([]);\n}\n\nfunction closePeerConnection(uid) {\n  const pc = peerConnections[uid];\n  if (pc) { pc.close(); delete peerConnections[uid]; }\n  const audioEl = remoteAudioEls[uid];\n  if (audioEl) { audioEl.remove(); delete remoteAudioEls[uid]; }\n}\n\nfunction createPeerConnection(otherUid) {\n  const pc = new RTCPeerConnection(RTC_CONFIG);\n  if (localStream) {\n    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));\n  }\n  pc.onicecandidate = (e) => {\n    if (e.candidate) {\n      send({ type: "voice_signal", to: otherUid, signal: { type: "ice", candidate: e.candidate } });\n    }\n  };\n  pc.ontrack = (e) => {\n    let audioEl = remoteAudioEls[otherUid];\n    if (!audioEl) {\n      audioEl = document.createElement("audio");\n      audioEl.autoplay = true;\n      document.body.appendChild(audioEl);\n      remoteAudioEls[otherUid] = audioEl;\n    }\n    audioEl.srcObject = e.streams[0];\n  };\n  peerConnections[otherUid] = pc;\n  return pc;\n}\n\nasync function onVoicePresence(m) {\n  if (!currentRoom || m.room_id !== currentRoom.id) return;\n  updateCallParticipants(m.members);\n  if (!voiceActive || !sess) return;\n\n  const others = m.members.filter(uid => uid !== sess.id);\n  // فقط با کسایی که هنوز کانکشن نداریم وصل می\u200cشیم؛ برای جلوگیری از وصل دوبل،\n  // فقط طرفی که آیدی عددیش کوچیک\u200cتره offer می\u200cفرسته.\n  for (const otherUid of others) {\n    if (peerConnections[otherUid]) continue;\n    if (sess.id < otherUid) {\n      const pc = createPeerConnection(otherUid);\n      const offer = await pc.createOffer();\n      await pc.setLocalDescription(offer);\n      send({ type: "voice_signal", to: otherUid, signal: { type: "offer", sdp: offer.sdp } });\n    }\n  }\n  // اگه کسی روم صوتی رو ترک کرده، کانکشنش رو ببند\n  Object.keys(peerConnections).map(Number).forEach(uid => {\n    if (!m.members.includes(uid)) closePeerConnection(uid);\n  });\n}\n\nasync function onVoiceSignal(m) {\n  const fromUid = m.from;\n  const signal = m.signal;\n  if (!signal) return;\n\n  if (signal.type === "offer") {\n    if (!voiceActive) return; // ما خودمون توی ویس نیستیم، نادیده بگیر\n    const pc = peerConnections[fromUid] || createPeerConnection(fromUid);\n    await pc.setRemoteDescription({ type: "offer", sdp: signal.sdp });\n    const answer = await pc.createAnswer();\n    await pc.setLocalDescription(answer);\n    send({ type: "voice_signal", to: fromUid, signal: { type: "answer", sdp: answer.sdp } });\n  } else if (signal.type === "answer") {\n    const pc = peerConnections[fromUid];\n    if (pc) await pc.setRemoteDescription({ type: "answer", sdp: signal.sdp });\n  } else if (signal.type === "ice") {\n    const pc = peerConnections[fromUid];\n    if (pc && signal.candidate) {\n      try { await pc.addIceCandidate(signal.candidate); } catch (e) {}\n    }\n  }\n}\n\nfunction updateCallParticipants(members) {\n  const el = document.getElementById("callParticipants");\n  if (!el) return;\n  if (!members.length) { el.innerHTML = ""; return; }\n  el.innerHTML = members.map(uid => {\n    const u = lastRoomUsersList.find(x => x.id === uid);\n    if (u) {\n      const av = u.avatar_url\n        ? `<div class="call-participant-av" style="background-image:url(\'${u.avatar_url}\')"></div>`\n        : `<div class="call-participant-av" style="background:${u.avatar_color}">${u.display_name[0].toUpperCase()}</div>`;\n      return `<div class="call-participant">${av}<span>${esc(u.display_name)}</span></div>`;\n    }\n    return `<div class="call-participant"><div class="call-participant-av">🎤</div><span>کاربر</span></div>`;\n  }).join("");\n}\n\n// ── REPORTING ─────────────────────────────────────────────────────────────\nfunction reportMessage(msgId, targetUserId) {\n  const reason = prompt("دلیل گزارش این پیام را بنویس:");\n  if (reason === null) return;\n  send({ type: "report", message_id: msgId, target_user_id: targetUserId, reason });\n}\n\n// ── ADMIN ─────────────────────────────────────────────────────────────────\nfunction flashAdminNav() {\n  const btn = document.getElementById("adminNavBtn");\n  if (btn) { btn.style.color = "var(--red)"; setTimeout(() => btn.style.color = "", 2000); }\n}\nfunction renderReports(reports) {\n  const el = document.getElementById("reportsList");\n  if (!reports.length) { el.innerHTML = `<div class="msys" style="align-self:flex-start">گزارشی موجود نیست</div>`; return; }\n  el.innerHTML = reports.map(r => `\n    <div class="admin-report-row">\n      <div><b>گزارش\u200cدهنده:</b> ${esc(r.reporter)} ${r.target ? `← <b>هدف:</b> ${esc(r.target)}` : ""}</div>\n      ${r.reason ? `<div style="color:var(--dim);margin-top:3px">${esc(r.reason)}</div>` : ""}\n      <div style="margin-top:6px;display:flex;gap:6px">\n        ${r.message_id ? `<button class="btn-tiny danger" onclick="adminDeleteMsg(${r.message_id})">🗑️ حذف پیام</button>` : ""}\n        ${r.target_id ? `<button class="btn-tiny danger" onclick="adminAction(\'admin_ban\', {user_id:${r.target_id}})">⛔ بن کاربر</button>` : ""}\n      </div>\n    </div>`).join("");\n}\nfunction renderBannedWords(words) {\n  const el = document.getElementById("bannedWordsList");\n  el.innerHTML = words.map(w => `<span class="word-tag">${esc(w)}<button onclick="adminAction(\'admin_remove_word\', {word:\'${escAttr(w)}\'})">✕</button></span>`).join("");\n}\nfunction addBannedWord() {\n  const i = document.getElementById("newBannedWord");\n  const w = i.value.trim();\n  if (!w) return;\n  send({ type: "admin_add_word", word: w });\n  i.value = "";\n  setTimeout(() => send({ type: "admin_get_reports" }), 200);\n}\nfunction adminAction(type, data) {\n  send({ type, ...data });\n}\nfunction adminDeleteMsg(id) { send({ type: "admin_delete_msg", message_id: id }); }\nfunction adminActOnUsername(actionType) {\n  const username = document.getElementById("adminTargetUsername").value.trim();\n  if (!username) return;\n  const extra = actionType === "admin_mute" ? { minutes: 10 } : {};\n  send({ type: actionType, username, ...extra });\n}\nfunction onAdminActionResult(m) {\n  toast("✅ اقدام انجام شد");\n  if (m.action.startsWith("admin_")) send({ type: "admin_get_reports" });\n}\n\n// ── THEME ─────────────────────────────────────────────────────────────────\nfunction setTheme(t) {\n  document.body.dataset.theme = t;\n  localStorage.setItem("lc_theme", t);\n  document.querySelectorAll(".theme-opt").forEach(b => b.classList.toggle("on", b.dataset.theme === t));\n}\n(function initTheme() {\n  const saved = localStorage.getItem("lc_theme");\n  if (saved) document.body.dataset.theme = saved;\n})();\n\nfunction toggleMusic() {\n  const audio = document.getElementById("bgMusic");\n  const btn = document.getElementById("musicBtn");\n  if (audio.paused) { audio.play().catch(() => {}); btn.textContent = "🔊"; }\n  else { audio.pause(); btn.textContent = "🔇"; }\n}\nlet particlesOn = false;\nfunction toggleParticles(on) {\n  particlesOn = on;\n  document.getElementById("cv").style.display = on ? "block" : "none";\n  if (on) startParticles();\n}\n\n// ── BOT QUICK ACTIONS (shortcuts row could call these) ───────────────────\n// (Bot commands like !dice/!coin/!quiz are typed directly into chat — see README)\n\n// ── PARTICLE NETWORK BACKGROUND (off by default for a calmer, basic look) ──\nlet startParticles = () => {};\n(function () {\n  const c = document.getElementById("cv"), x = c.getContext("2d");\n  let W, H, P = [], running = false;\n  const COL = ["#00d4ff", "#7c3aed", "#39ff14", "#ff6b35", "#ffd700"];\n  function rs() { W = c.width = innerWidth; H = c.height = innerHeight; }\n  function init() {\n    P = [];\n    for (let i = 0; i < 55; i++) P.push({ x: Math.random() * W, y: Math.random() * H, vx: (Math.random() - .5) * .4, vy: (Math.random() - .5) * .4, r: Math.random() * 2 + 1, col: COL[Math.floor(Math.random() * 5)], a: Math.random() * .5 + .3 });\n  }\n  function draw() {\n    if (!particlesOn) { running = false; return; }\n    x.clearRect(0, 0, W, H);\n    for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {\n      const dx = P[i].x - P[j].x, dy = P[i].y - P[j].y, d = Math.sqrt(dx * dx + dy * dy);\n      if (d < 110) { x.beginPath(); x.strokeStyle = `rgba(0,212,255,${(1 - d / 110) * .12})`; x.lineWidth = .5; x.moveTo(P[i].x, P[i].y); x.lineTo(P[j].x, P[j].y); x.stroke(); }\n    }\n    P.forEach(p => {\n      x.beginPath(); x.arc(p.x, p.y, p.r, 0, Math.PI * 2); x.fillStyle = p.col; x.globalAlpha = p.a; x.fill(); x.globalAlpha = 1;\n      p.x += p.vx; p.y += p.vy;\n      if (p.x < 0) p.x = W; if (p.x > W) p.x = 0; if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;\n    });\n    requestAnimationFrame(draw);\n  }\n  window.addEventListener("resize", () => { if (particlesOn) { rs(); init(); } });\n  rs(); init();\n  startParticles = () => {\n    if (running) return;\n    running = true;\n    draw();\n  };\n})();\n\n// ── HELPERS ───────────────────────────────────────────────────────────────\nfunction esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }\nfunction escAttr(s) { return String(s ?? "").replace(/\'/g, "\\\\\'").replace(/"/g, "&quot;"); }\nfunction show(id) { document.querySelectorAll(".scr").forEach(s => s.classList.remove("on")); document.getElementById(id).classList.add("on"); }\nfunction stab(t) {\n  document.getElementById("fL").classList.toggle("hid", t !== "L");\n  document.getElementById("fR").classList.toggle("hid", t !== "R");\n  document.getElementById("tL").classList.toggle("on", t === "L");\n  document.getElementById("tR").classList.toggle("on", t !== "L");\n}\nfunction setErr(id, m) { document.getElementById(id).textContent = m; }\nfunction fmtTime(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }\nfunction toast(msg) {\n  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;\n  document.body.appendChild(t); setTimeout(() => t.remove(), 3000);\n}\nfunction toastHTML(html) {\n  const t = document.createElement("div"); t.className = "toast"; t.innerHTML = html;\n  document.body.appendChild(t); setTimeout(() => t.remove(), 6000);\n}\nfunction beep() {\n  try {\n    const c = new (window.AudioContext || window.webkitAudioContext)(), o = c.createOscillator(), g = c.createGain();\n    o.connect(g); g.connect(c.destination); o.frequency.value = 880;\n    g.gain.setValueAtTime(.08, c.currentTime); g.gain.exponentialRampToValueAtTime(.001, c.currentTime + .2);\n    o.start(); o.stop(c.currentTime + .2);\n  } catch (_) {}\n}\n\ndocument.getElementById("lp")?.addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });\ndocument.getElementById("rp")?.addEventListener("keydown", e => { if (e.key === "Enter") doReg(); });\n'


# ════════════════════════════════════════════════════════════════════════
#  Server logic (originally server.py) — unchanged except routes now serve
#  the strings above instead of reading from disk.
# ════════════════════════════════════════════════════════════════════════
BASE_DIR = Path(__file__).parent
PERSIST_DIR = Path(os.environ.get("PERSIST_DIR", BASE_DIR))
UPLOADS_AVATAR = PERSIST_DIR / "uploads" / "avatars"
UPLOADS_MEDIA = PERSIST_DIR / "uploads" / "media"
UPLOADS_ROOM_ICON = PERSIST_DIR / "uploads" / "room_icons"
UPLOADS_AVATAR.mkdir(parents=True, exist_ok=True)
UPLOADS_MEDIA.mkdir(parents=True, exist_ok=True)
UPLOADS_ROOM_ICON.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 6 * 1024 * 1024  # 6MB
# توجه: svg از لیست کنار گذاشته شده چون می‌تونه جاوااسکریپت مخفی داشته باشه و
# اگه مستقیم باز شه خطر امنیتی داره؛ بقیه‌ی فرمت‌های عکس مشکلی ندارن.
ALLOWED_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".tiff", ".tif", ".heic", ".heif", ".avif", ".jfif", ".ico", ".jpe",
}

# ── کد مخصوص ادمین ──────────────────────────────────────────────────────────
# هرکس این کد رو موقع ثبت‌نام وارد کنه (یا بعداً از پروفایلش وارد کنه) ادمین می‌شه.
# می‌تونی این رو با environment variable به نام ADMIN_CODE هم عوض کنی، یا همینجا
# مقدارش رو دستی تغییر بده. اگه خالی بمونه، هیچکس نمی‌تونه ادمین بشه.
ADMIN_CODE = os.environ.get("ADMIN_CODE", "line-admin-2026")

# ── in-memory live state (per-process; persisted data lives in SQLite) ──────
connected = {}          # ws -> {user_id, username, display_name, room_id, ...}
sockets_by_user = {}     # user_id -> set of ws (multi-tab support)


def now():
    return time.time()


def public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "avatar_color": row["avatar_color"],
        "avatar_url": row["avatar_url"],
        "bio": row["bio"],
        "status": row["status"],
        "current_game": row["current_game"],
        "role": row["role"],
        "message_count": row["message_count"],
        "banned": bool(row["banned"]),
    }


def get_user_by_id(uid):
    conn = db.get_conn()
    return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def get_user_by_username(name):
    conn = db.get_conn()
    return conn.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()


def is_admin(uid):
    u = get_user_by_id(uid)
    return bool(u and u["role"] == "admin")


def contains_banned_word(text):
    conn = db.get_conn()
    words = [r["word"].lower() for r in conn.execute("SELECT word FROM banned_words")]
    low = text.lower()
    for w in words:
        if w and w in low:
            return w
    return None


def filter_text(text):
    """Replace banned words with asterisks instead of blocking outright."""
    conn = db.get_conn()
    words = [r["word"] for r in conn.execute("SELECT word FROM banned_words")]
    out = text
    for w in words:
        if not w:
            continue
        out = re.sub(re.escape(w), "*" * len(w), out, flags=re.IGNORECASE)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  WebSocket message handlers
# ══════════════════════════════════════════════════════════════════════════

async def send_to(ws, payload):
    try:
        await ws.send_str(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


async def send_to_user(uid, payload):
    for ws in list(sockets_by_user.get(uid, [])):
        await send_to(ws, payload)


async def broadcast_room(room_id, payload, exclude_ws=None):
    for ws, sess in list(connected.items()):
        if ws is exclude_ws:
            continue
        if sess.get("room_id") == room_id:
            await send_to(ws, payload)


async def broadcast_all(payload, exclude_ws=None):
    for ws in list(connected):
        if ws is exclude_ws:
            continue
        await send_to(ws, payload)


async def broadcast_room_presence(room_id):
    members = []
    for sess in connected.values():
        if sess.get("room_id") == room_id:
            u = get_user_by_id(sess["user_id"])
            if u:
                members.append(public_user(u))
    await broadcast_room(room_id, {"type": "room_users", "room_id": room_id, "users": members})


def serialize_message(row, viewer_id=None):
    conn = db.get_conn()
    user = get_user_by_id(row["user_id"])
    reply_payload = None
    if row["reply_to"]:
        rr = conn.execute("SELECT * FROM messages WHERE id=?", (row["reply_to"],)).fetchone()
        if rr and not rr["deleted"]:
            ru = get_user_by_id(rr["user_id"])
            reply_payload = {
                "id": rr["id"],
                "text": (rr["text"] or "")[:120],
                "display_name": ru["display_name"] if ru else "?",
            }
    # reactions grouped
    rx_rows = conn.execute(
        "SELECT emoji, user_id FROM reactions WHERE message_id=?", (row["id"],)
    ).fetchall()
    rx = {}
    for r in rx_rows:
        rx.setdefault(r["emoji"], []).append(r["user_id"])

    return {
        "type": "message",
        "id": row["id"],
        "room_id": row["room_id"],
        "dm_key": row["dm_key"],
        "user_id": row["user_id"],
        "username": user["username"] if user else "?",
        "display_name": user["display_name"] if user else "?",
        "avatar_color": user["avatar_color"] if user else "#888",
        "avatar_url": user["avatar_url"] if user else None,
        "role": user["role"] if user else "member",
        "text": row["text"] or "",
        "media_url": row["media_url"],
        "media_type": row["media_type"],
        "is_sticker": bool(row["is_sticker"]) if "is_sticker" in row.keys() else False,
        "reply": reply_payload,
        "reactions": rx,
        "ts": row["created_at"],
        "deleted": bool(row["deleted"]),
    }


# پیام‌ها فقط تا این مدت نگه داشته می‌شن، بعدش به‌صورت خودکار حذف می‌شن (به ثانیه)
MESSAGE_RETENTION_SECONDS = 24 * 60 * 60


def fetch_room_history(room_id, limit=50):
    conn = db.get_conn()
    cutoff = now() - MESSAGE_RETENTION_SECONDS
    rows = conn.execute(
        "SELECT * FROM messages WHERE room_id=? AND deleted=0 AND created_at>=? ORDER BY created_at DESC LIMIT ?",
        (room_id, cutoff, limit),
    ).fetchall()
    return [serialize_message(r) for r in reversed(rows)]


def fetch_dm_history(key, limit=50):
    conn = db.get_conn()
    cutoff = now() - MESSAGE_RETENTION_SECONDS
    rows = conn.execute(
        "SELECT * FROM messages WHERE dm_key=? AND deleted=0 AND created_at>=? ORDER BY created_at DESC LIMIT ?",
        (key, cutoff, limit),
    ).fetchall()
    return [serialize_message(r) for r in reversed(rows)]


def list_rooms(viewer_id=None):
    """
    روم‌های عمومی/گیمینگ همیشه برای همه نشون داده می‌شن.
    روم‌های خصوصی و چت‌صوتی (تماس) فقط برای کسایی نشون داده می‌شن که:
      - عضوش هستن (یعنی قبلاً با کد وارد شدن یا خودشون ساختنش)، یا
      - مالک روم هستن، یا
      - ادمین سایت هستن (برای مدیریت)
    """
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM rooms ORDER BY kind, id").fetchall()

    member_room_ids = set()
    if viewer_id:
        member_room_ids = {
            r["room_id"] for r in conn.execute(
                "SELECT room_id FROM room_members WHERE user_id=?", (viewer_id,)
            )
        }
    viewer_is_admin = bool(viewer_id) and is_admin(viewer_id)

    out = []
    for r in rows:
        is_private = r["kind"] in ("private", "voice")
        if is_private:
            has_access = (
                r["id"] in member_room_ids
                or r["owner_id"] == viewer_id
                or viewer_is_admin
            )
            if not has_access:
                continue

        member_count = 0
        for sess in connected.values():
            if sess.get("room_id") == r["id"]:
                member_count += 1
        out.append({
            "id": r["id"], "name": r["name"], "slug": r["slug"], "kind": r["kind"],
            "icon": r["icon"], "icon_url": r["icon_url"], "is_private": is_private,
            "owner_id": r["owner_id"], "online": member_count,
        })
    return out


async def push_room_list():
    """به هرکسی که الان وصله، لیست روم‌های مخصوص خودش رو می‌فرسته (نه یه لیست یکسان برای همه)،
    چون روم‌های خصوصی برای هر کاربر فرق می‌کنه."""
    for ws, sess in list(connected.items()):
        if sess.get("user_id"):
            await send_to(ws, {"type": "room_list", "rooms": list_rooms(sess["user_id"])})


async def broadcast_site_online():
    """تعداد کل کاربران آنلاین در کل سایت رو برای همه پخش می‌کنه (نه فقط اعضای یه روم خاص)."""
    count = len(sockets_by_user)
    await broadcast_all({"type": "site_online", "count": count})


async def push_friend_lists(uid):
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT u.*, f.status as friend_status, f.user_id as req_from FROM friendships f
           JOIN users u ON (u.id = CASE WHEN f.user_id=? THEN f.friend_id ELSE f.user_id END)
           WHERE (f.user_id=? OR f.friend_id=?)""",
        (uid, uid, uid),
    ).fetchall()
    friends, incoming, outgoing = [], [], []
    for r in rows:
        pu = public_user(r)
        pu["online"] = r["id"] in sockets_by_user and len(sockets_by_user[r["id"]]) > 0
        if r["friend_status"] == "accepted":
            friends.append(pu)
        elif r["req_from"] == uid:
            outgoing.append(pu)
        else:
            incoming.append(pu)
    await send_to_user(uid, {"type": "friends", "friends": friends, "incoming": incoming, "outgoing": outgoing})


ROLL_EMOJI = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
QUIZ_BANK = [
    {"q": "کدوم بازی محصول استودیو Riot Games نیست؟", "options": ["Valorant", "League of Legends", "Minecraft", "Wild Rift"], "a": 2},
    {"q": "اولین کنسول‌بازی پلی‌استیشن مال چه سالی بود؟", "options": ["1994", "1998", "2000", "1990"], "a": 0},
    {"q": "نام شخصیت اصلی بازی Minecraft چیه؟", "options": ["Notch", "Steve", "Alex", "Herobrine"], "a": 1},
    {"q": "کدوم شرکت سازنده‌ی بازی GTA است؟", "options": ["EA", "Ubisoft", "Rockstar Games", "Activision"], "a": 2},
    {"q": "بازی Among Us توسط کدوم استودیو ساخته شد؟", "options": ["Innersloth", "Mediatonic", "Supercell", "Mojang"], "a": 0},
]


async def handle_bot_command(ws, sess, text):
    """!dice  !coin  !quiz"""
    cmd = text.strip().lower()
    room_id = sess.get("room_id")
    if cmd in ("!dice", "!roll"):
        n = random.randint(1, 6)
        await emit_system(room_id, f"🎲 {sess['display_name']} تاس انداخت و عدد {ROLL_EMOJI[n]} {n} آورد!")
    elif cmd in ("!coin", "!flip"):
        side = random.choice(["شیر 🪙", "خط 🪙"])
        await emit_system(room_id, f"🪙 {sess['display_name']} سکه انداخت: {side}")
    elif cmd == "!quiz":
        q = random.choice(QUIZ_BANK)
        opts = "\n".join(f"{i+1}. {o}" for i, o in enumerate(q["options"]))
        await emit_system(room_id, f"🧠 کوییز برای {sess['display_name']}:\n{q['q']}\n{opts}\n(پاسخ صحیح بعد از ۱۰ ثانیه فاش می‌شود)")

        async def reveal():
            await asyncio.sleep(10)
            await emit_system(room_id, f"✅ پاسخ صحیح: {q['options'][q['a']]}")
        asyncio.create_task(reveal())
    else:
        return False
    return True


async def emit_system(room_id, text):
    payload = {"type": "system", "text": text, "ts": now(), "room_id": room_id}
    await broadcast_room(room_id, payload)


# ══════════════════════════════════════════════════════════════════════════
#  Core dispatch
# ══════════════════════════════════════════════════════════════════════════

async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)
    sess = {"user_id": None}
    connected[ws] = sess

    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            t = data.get("type")

            # ── AUTH ──────────────────────────────────────────────────────
            if t == "register":
                await on_register(ws, sess, data)
            elif t == "login":
                await on_login(ws, sess, data)
            elif t == "logout":
                await on_disconnect(ws, sess, silent=True)
                sess["user_id"] = None

            # everything below requires auth
            elif not sess.get("user_id"):
                await send_to(ws, {"type": "error", "msg": "NOT_AUTHENTICATED"})

            elif t == "join_room":
                await on_join_room(ws, sess, data)
            elif t == "create_room":
                await on_create_room(ws, sess, data)
            elif t == "join_by_code":
                await on_join_by_code(ws, sess, data)
            elif t == "room_update":
                await on_room_update(ws, sess, data)
            elif t == "room_delete":
                await on_room_delete(ws, sess, data)
            elif t == "room_regenerate_code":
                await on_room_regenerate_code(ws, sess, data)
            elif t == "chat":
                await on_chat(ws, sess, data)
            elif t == "dm_open":
                await on_dm_open(ws, sess, data)
            elif t == "dm_send":
                await on_dm_send(ws, sess, data)
            elif t == "typing":
                await on_typing(ws, sess, data)
            elif t == "react":
                await on_react(ws, sess, data)
            elif t == "update_profile":
                await on_update_profile(ws, sess, data)
            elif t == "set_game":
                await on_set_game(ws, sess, data)
            elif t == "friend_request":
                await on_friend_request(ws, sess, data)
            elif t == "friend_respond":
                await on_friend_respond(ws, sess, data)
            elif t == "get_friends":
                await push_friend_lists(sess["user_id"])
            elif t == "report":
                await on_report(ws, sess, data)
            elif t == "redeem_admin_code":
                await on_redeem_admin_code(ws, sess, data)
            elif t == "voice_join":
                await on_voice_join(ws, sess, data)
            elif t == "voice_leave":
                await on_voice_leave(ws, sess, data)
            elif t == "voice_signal":
                await on_voice_signal(ws, sess, data)
            elif t in ("admin_ban", "admin_kick", "admin_mute", "admin_unban", "admin_delete_msg", "admin_add_word", "admin_remove_word", "admin_get_reports"):
                await on_admin_action(ws, sess, t, data)
    finally:
        await on_disconnect(ws, sess)
        connected.pop(ws, None)

    return ws


# ── AUTH HANDLERS ────────────────────────────────────────────────────────

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u0600-\u06FF]{3,20}$")


async def on_register(ws, sess, data):
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    display_name = str(data.get("display_name") or username).strip()[:30]
    admin_code = str(data.get("admin_code", "")).strip()

    if not USERNAME_RE.match(username):
        await send_to(ws, {"type": "register_result", "ok": False, "msg": "INVALID_USERNAME"})
        return
    if len(password) < 4:
        await send_to(ws, {"type": "register_result", "ok": False, "msg": "WEAK_PASSWORD"})
        return

    conn = db.get_conn()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        await send_to(ws, {"type": "register_result", "ok": False, "msg": "USERNAME_TAKEN"})
        return

    granted_admin = bool(ADMIN_CODE) and admin_code == ADMIN_CODE
    role = "admin" if granted_admin else "member"
    with db.tx() as c:
        c.execute(
            """INSERT INTO users(username, password_hash, display_name, avatar_color, created_at, role)
               VALUES (?,?,?,?,?,?)""",
            (username, db.hash_password(password), display_name,
             random.choice(db.AVATAR_COLORS), now(), role),
        )
    await send_to(ws, {"type": "register_result", "ok": True, "first_admin": granted_admin})


async def on_redeem_admin_code(ws, sess, data):
    """Lets an already-registered (logged-in) user become admin if they know the secret code."""
    code = str(data.get("admin_code", "")).strip()
    if not ADMIN_CODE or code != ADMIN_CODE:
        await send_to(ws, {"type": "redeem_admin_result", "ok": False, "msg": "WRONG_CODE"})
        return
    with db.tx() as c:
        c.execute("UPDATE users SET role='admin' WHERE id=?", (sess["user_id"],))
    u = get_user_by_id(sess["user_id"])
    await send_to(ws, {"type": "redeem_admin_result", "ok": True, "user": public_user(u)})



async def on_login(ws, sess, data):
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    u = get_user_by_username(username)
    if not u:
        await send_to(ws, {"type": "login_result", "ok": False, "msg": "USER_NOT_FOUND"})
        return
    if not db.verify_password(password, u["password_hash"]):
        await send_to(ws, {"type": "login_result", "ok": False, "msg": "WRONG_PASSWORD"})
        return
    if u["banned"]:
        await send_to(ws, {"type": "login_result", "ok": False, "msg": "BANNED"})
        return

    sess["user_id"] = u["id"]
    sess["username"] = u["username"]
    sess["display_name"] = u["display_name"]
    sess["room_id"] = None
    sockets_by_user.setdefault(u["id"], set()).add(ws)

    await send_to(ws, {"type": "login_result", "ok": True, "user": public_user(u)})
    await send_to(ws, {"type": "room_list", "rooms": list_rooms(u["id"])})
    await push_friend_lists(u["id"])
    await broadcast_site_online()

    # auto-join general room
    general = db.get_conn().execute("SELECT * FROM rooms WHERE slug='general'").fetchone()
    if general:
        await join_room_internal(ws, sess, general["id"])


async def on_disconnect(ws, sess, silent=False):
    uid = sess.get("user_id")
    room_id = sess.get("room_id")
    if uid and ws in sockets_by_user.get(uid, set()):
        sockets_by_user[uid].discard(ws)
        still_online = len(sockets_by_user.get(uid, set())) > 0
        if room_id and not silent:
            await emit_system(room_id, f"👋 {sess.get('display_name')} اتاق را ترک کرد")
        if room_id:
            await broadcast_room_presence(room_id)
        if not still_online:
            # اگه کاربر کلا آفلاین شد، از هر روم صوتی‌ای که توش بود هم خارجش کن
            affected_voice_rooms = remove_from_all_voice_rooms(uid)
            for vr in affected_voice_rooms:
                await broadcast_voice_presence(vr)
            await push_room_list()
            await broadcast_site_online()
            # notify friends of offline status
            conn = db.get_conn()
            friends = conn.execute(
                """SELECT CASE WHEN user_id=? THEN friend_id ELSE user_id END as fid
                   FROM friendships WHERE (user_id=? OR friend_id=?) AND status='accepted'""",
                (uid, uid, uid),
            ).fetchall()
            for f in friends:
                await push_friend_lists(f["fid"])


# ── ROOMS ────────────────────────────────────────────────────────────────

async def join_room_internal(ws, sess, room_id):
    conn = db.get_conn()
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        return
    old_room = sess.get("room_id")
    sess["room_id"] = room_id
    with db.tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO room_members(room_id, user_id, joined_at) VALUES (?,?,?)",
            (room_id, sess["user_id"], now()),
        )
    can_manage = (room["owner_id"] == sess["user_id"]) or is_admin(sess["user_id"])
    await send_to(ws, {
        "type": "joined_room", "room": {
            "id": room["id"], "name": room["name"], "slug": room["slug"],
            "kind": room["kind"], "icon": room["icon"], "icon_url": room["icon_url"],
            "owner_id": room["owner_id"],
            "can_manage": can_manage,
            "invite_code": room["invite_code"] if (can_manage and room["kind"] in ("private", "voice")) else None,
        },
        "history": fetch_room_history(room_id),
    })
    if old_room and old_room != room_id:
        await broadcast_room_presence(old_room)
    await broadcast_room_presence(room_id)
    await emit_system(room_id, f"🎮 {sess['display_name']} وارد اتاق شد")
    await push_room_list()


async def on_join_room(ws, sess, data):
    room_id = data.get("room_id")
    conn = db.get_conn()
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        await send_to(ws, {"type": "error", "msg": "ROOM_NOT_FOUND"})
        return
    if room["kind"] in ("private", "voice"):
        is_member = conn.execute(
            "SELECT 1 FROM room_members WHERE room_id=? AND user_id=?",
            (room_id, sess["user_id"]),
        ).fetchone()
        has_access = bool(is_member) or room["owner_id"] == sess["user_id"] or is_admin(sess["user_id"])
        if not has_access:
            await send_to(ws, {"type": "join_room_result", "ok": False, "msg": "PRIVATE_NEEDS_CODE"})
            return
    await join_room_internal(ws, sess, room_id)


async def on_create_room(ws, sess, data):
    name = str(data.get("name", "")).strip()[:40]
    kind = data.get("kind", "private")
    icon = str(data.get("icon", "💬"))[:4] or "💬"
    icon_url = data.get("icon_url") or None
    if kind not in ("public", "game", "voice", "private"):
        kind = "private"
    # روم‌های عمومی/گیمینگ بخش رسمی سایت هستن و فقط ادمین می‌تونه بسازه.
    # روم خصوصی و چت‌صوتی (تماس) رو هر کاربری می‌تونه برای خودش بسازه —
    # دقیقاً مثل ساختن یه گروه خصوصی یا شروع یه تماس توی تلگرام.
    if kind in ("public", "game") and not is_admin(sess["user_id"]):
        kind = "private"
    if not name:
        await send_to(ws, {"type": "create_room_result", "ok": False, "msg": "NAME_REQUIRED"})
        return
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or uuid.uuid4().hex[:8]
    conn = db.get_conn()
    base_slug = slug
    n = 1
    while conn.execute("SELECT 1 FROM rooms WHERE slug=?", (slug,)).fetchone():
        n += 1
        slug = f"{base_slug}-{n}"

    # روم‌های خصوصی و چت‌صوتی هر دو با کد دعوت کار می‌کنن (مثل لینک دعوت/تماس تلگرام)
    invite_code = generate_unique_invite_code(conn) if kind in ("private", "voice") else None
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO rooms(name, slug, kind, owner_id, invite_code, icon, icon_url, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, slug, kind, sess["user_id"], invite_code, icon, icon_url, now()),
        )
        room_id = cur.lastrowid
    await send_to(ws, {"type": "create_room_result", "ok": True, "room_id": room_id, "invite_code": invite_code})
    await push_room_list()
    await join_room_internal(ws, sess, room_id)


def generate_unique_invite_code(conn):
    while True:
        code = db.generate_invite_code()
        if not conn.execute("SELECT 1 FROM rooms WHERE invite_code=?", (code,)).fetchone():
            return code


async def on_join_by_code(ws, sess, data):
    """ورود به یه روم خصوصی فقط با داشتن کدش — دقیقاً مثل لینک دعوت تلگرام،
    حتی نیازی به دونستن اسم روم هم نیست."""
    code = str(data.get("code", "")).strip().upper()
    if not code:
        await send_to(ws, {"type": "join_by_code_result", "ok": False, "msg": "EMPTY_CODE"})
        return
    conn = db.get_conn()
    room = conn.execute("SELECT * FROM rooms WHERE invite_code=?", (code,)).fetchone()
    if not room:
        await send_to(ws, {"type": "join_by_code_result", "ok": False, "msg": "CODE_NOT_FOUND"})
        return
    await send_to(ws, {"type": "join_by_code_result", "ok": True, "room_id": room["id"], "room_name": room["name"]})
    await join_room_internal(ws, sess, room["id"])


# روم‌های پایه‌ای که نباید حذف بشن (حداقل یه روم عمومی برای ورود خودکار کاربرهای جدید لازمه)
PROTECTED_ROOM_SLUGS = {"general"}


def _room_can_manage(room, sess):
    return room["owner_id"] == sess["user_id"] or is_admin(sess["user_id"])


async def on_room_update(ws, sess, data):
    """ویرایش اسم/آیکون یه روم — فقط برای مالک روم یا ادمین."""
    room_id = data.get("room_id")
    conn = db.get_conn()
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        await send_to(ws, {"type": "error", "msg": "ROOM_NOT_FOUND"})
        return
    if not _room_can_manage(room, sess):
        await send_to(ws, {"type": "error", "msg": "NOT_ROOM_OWNER"})
        return

    fields = {}
    name = str(data.get("name", "")).strip()[:40]
    icon = str(data.get("icon", "")).strip()[:4]
    if name:
        fields["name"] = name
    if icon:
        fields["icon"] = icon
    if "icon_url" in data:
        # می‌تونه یه URL واقعی باشه (آپلود عکس جدید) یا None/"" باشه (یعنی برگرد به ایموجی)
        fields["icon_url"] = data.get("icon_url") or None

    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        with db.tx() as c:
            c.execute(f"UPDATE rooms SET {sets} WHERE id=?", (*fields.values(), room_id))

    updated = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    await send_to(ws, {"type": "room_update_result", "ok": True, "name": updated["name"], "icon": updated["icon"], "icon_url": updated["icon_url"]})
    await broadcast_room(room_id, {"type": "room_renamed", "room_id": room_id, "name": updated["name"], "icon": updated["icon"], "icon_url": updated["icon_url"]})
    await push_room_list()


async def on_room_delete(ws, sess, data):
    """حذف کامل یه روم — فقط برای مالک روم یا ادمین. کاربرهای داخل روم به چت عمومی منتقل می‌شن."""
    room_id = data.get("room_id")
    conn = db.get_conn()
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room:
        await send_to(ws, {"type": "error", "msg": "ROOM_NOT_FOUND"})
        return
    if room["slug"] in PROTECTED_ROOM_SLUGS:
        await send_to(ws, {"type": "room_delete_result", "ok": False, "msg": "ROOM_PROTECTED"})
        return
    if not _room_can_manage(room, sess):
        await send_to(ws, {"type": "error", "msg": "NOT_ROOM_OWNER"})
        return

    general = conn.execute("SELECT * FROM rooms WHERE slug='general'").fetchone()
    affected = [(w2, s2) for w2, s2 in connected.items() if s2.get("room_id") == room_id]

    with db.tx() as c:
        c.execute("DELETE FROM reports WHERE message_id IN (SELECT id FROM messages WHERE room_id=?)", (room_id,))
        c.execute("DELETE FROM messages WHERE room_id=?", (room_id,))
        c.execute("DELETE FROM room_members WHERE room_id=?", (room_id,))
        c.execute("DELETE FROM rooms WHERE id=?", (room_id,))

    voice_members.pop(room_id, None)

    for w2, s2 in affected:
        if w2 is not ws:
            await send_to(w2, {"type": "room_deleted", "room_id": room_id})
        if general:
            await join_room_internal(w2, s2, general["id"])

    await send_to(ws, {"type": "room_delete_result", "ok": True})
    await push_room_list()


async def on_room_regenerate_code(ws, sess, data):
    """کد دعوت یه روم خصوصی رو عوض می‌کنه (کد قبلی دیگه کار نمی‌کنه) — فقط برای مالک یا ادمین."""
    room_id = data.get("room_id")
    conn = db.get_conn()
    room = conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not room or room["kind"] not in ("private", "voice"):
        await send_to(ws, {"type": "error", "msg": "ROOM_NOT_FOUND"})
        return
    if not _room_can_manage(room, sess):
        await send_to(ws, {"type": "error", "msg": "NOT_ROOM_OWNER"})
        return

    new_code = generate_unique_invite_code(conn)
    with db.tx() as c:
        c.execute("UPDATE rooms SET invite_code=? WHERE id=?", (new_code, room_id))
    await send_to(ws, {"type": "room_regenerate_code_result", "ok": True, "invite_code": new_code})


# ── CHAT (room messages) ─────────────────────────────────────────────────

def increment_message_count(uid):
    with db.tx() as c:
        c.execute(
            "UPDATE users SET message_count = message_count + 1 WHERE id=?",
            (uid,),
        )


async def on_chat(ws, sess, data):
    room_id = sess.get("room_id")
    if not room_id:
        return
    u = get_user_by_id(sess["user_id"])
    if u["banned"]:
        return
    if u["muted_until"] and u["muted_until"] > now():
        await send_to(ws, {"type": "error", "msg": "MUTED"})
        return

    text = str(data.get("text", "")).strip()[:1500]
    media_url = data.get("media_url")
    media_type = data.get("media_type")
    reply_to = data.get("reply_to")
    is_sticker = 1 if data.get("is_sticker") else 0

    if not text and not media_url:
        return

    # bot commands
    if text.startswith("!"):
        handled = await handle_bot_command(ws, sess, text)
        if handled:
            return

    text = filter_text(text) if (text and not is_sticker) else text

    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO messages(room_id, user_id, text, media_url, media_type, reply_to, is_sticker, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (room_id, sess["user_id"], text, media_url, media_type, reply_to, is_sticker, now()),
        )
        msg_id = cur.lastrowid

    increment_message_count(sess["user_id"])

    row = db.get_conn().execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    payload = serialize_message(row)
    await broadcast_room(room_id, payload)


async def on_typing(ws, sess, data):
    room_id = sess.get("room_id")
    dm_with = data.get("dm_with")
    if dm_with:
        await send_to_user(dm_with, {"type": "typing_dm", "from": sess["user_id"], "display_name": sess["display_name"]})
    elif room_id:
        await broadcast_room(room_id, {"type": "typing", "display_name": sess["display_name"], "user_id": sess["user_id"]}, exclude_ws=ws)


# ── چت صوتی (ساده، WebRTC peer-to-peer؛ سرور فقط پیام‌های سیگنالینگ رو رله می‌کنه) ──
voice_members = {}  # room_id -> set of user_id currently in voice chat


async def broadcast_voice_presence(room_id):
    members = sorted(voice_members.get(room_id, set()))
    await broadcast_room(room_id, {"type": "voice_presence", "room_id": room_id, "members": members})


async def on_voice_join(ws, sess, data):
    room_id = sess.get("room_id")
    if not room_id:
        return
    voice_members.setdefault(room_id, set()).add(sess["user_id"])
    await broadcast_voice_presence(room_id)


async def on_voice_leave(ws, sess, data):
    room_id = sess.get("room_id")
    if room_id and room_id in voice_members:
        voice_members[room_id].discard(sess["user_id"])
        if not voice_members[room_id]:
            del voice_members[room_id]
    await broadcast_voice_presence(room_id)


async def on_voice_signal(ws, sess, data):
    """Relay a WebRTC offer/answer/ICE-candidate message to a specific peer."""
    target_id = data.get("to")
    if not target_id:
        return
    await send_to_user(target_id, {
        "type": "voice_signal",
        "from": sess["user_id"],
        "signal": data.get("signal"),
    })


def remove_from_all_voice_rooms(uid):
    """Used on disconnect — make sure a departing user isn't stuck in a voice room."""
    affected = []
    for room_id, members in list(voice_members.items()):
        if uid in members:
            members.discard(uid)
            affected.append(room_id)
            if not members:
                del voice_members[room_id]
    return affected


async def on_react(ws, sess, data):
    msg_id = data.get("message_id")
    emoji = str(data.get("emoji", ""))[:8]
    if not emoji or not msg_id:
        return
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not row:
        return
    existing = conn.execute(
        "SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",
        (msg_id, sess["user_id"], emoji),
    ).fetchone()
    with db.tx() as c:
        if existing:
            c.execute("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?", (msg_id, sess["user_id"], emoji))
        else:
            c.execute("INSERT INTO reactions(message_id, user_id, emoji) VALUES (?,?,?)", (msg_id, sess["user_id"], emoji))

    rx_rows = conn.execute("SELECT emoji, user_id FROM reactions WHERE message_id=?", (msg_id,)).fetchall()
    rx = {}
    for r in rx_rows:
        rx.setdefault(r["emoji"], []).append(r["user_id"])
    payload = {"type": "reaction_update", "message_id": msg_id, "reactions": rx}

    if row["room_id"]:
        await broadcast_room(row["room_id"], payload)
    elif row["dm_key"]:
        a, b = row["dm_key"].split(":")
        await send_to_user(int(a), payload)
        await send_to_user(int(b), payload)


# ── DMs ──────────────────────────────────────────────────────────────────

async def on_dm_open(ws, sess, data):
    other_id = data.get("user_id")
    other = get_user_by_id(other_id)
    if not other:
        return
    key = db.dm_key(sess["user_id"], other_id)
    await send_to(ws, {
        "type": "dm_opened",
        "with": public_user(other),
        "history": fetch_dm_history(key),
    })


async def on_dm_send(ws, sess, data):
    other_id = data.get("user_id")
    other = get_user_by_id(other_id)
    if not other:
        return
    text = str(data.get("text", "")).strip()[:1500]
    media_url = data.get("media_url")
    media_type = data.get("media_type")
    reply_to = data.get("reply_to")
    is_sticker = 1 if data.get("is_sticker") else 0
    if not text and not media_url:
        return
    me = get_user_by_id(sess["user_id"])
    if me["banned"]:
        return
    text = filter_text(text) if (text and not is_sticker) else text
    key = db.dm_key(sess["user_id"], other_id)

    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO messages(dm_key, user_id, text, media_url, media_type, reply_to, is_sticker, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (key, sess["user_id"], text, media_url, media_type, reply_to, is_sticker, now()),
        )
        msg_id = cur.lastrowid

    row = db.get_conn().execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    payload = serialize_message(row)
    await send_to_user(sess["user_id"], payload)
    await send_to_user(other_id, payload)


# ── PROFILE ──────────────────────────────────────────────────────────────

async def on_update_profile(ws, sess, data):
    fields = {}
    if "display_name" in data:
        fields["display_name"] = str(data["display_name"]).strip()[:30] or sess["display_name"]
    if "bio" in data:
        fields["bio"] = str(data["bio"]).strip()[:200]
    if "status" in data:
        fields["status"] = str(data["status"]).strip()[:60]
    if "avatar_url" in data:
        fields["avatar_url"] = data["avatar_url"]
    if "avatar_color" in data and data["avatar_color"] in db.AVATAR_COLORS:
        fields["avatar_color"] = data["avatar_color"]

    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        with db.tx() as c:
            c.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), sess["user_id"]))
        if "display_name" in fields:
            sess["display_name"] = fields["display_name"]

    u = get_user_by_id(sess["user_id"])
    await send_to(ws, {"type": "profile_updated", "user": public_user(u)})
    room_id = sess.get("room_id")
    if room_id:
        await broadcast_room_presence(room_id)


async def on_set_game(ws, sess, data):
    game = str(data.get("game", "")).strip()[:40]
    with db.tx() as c:
        c.execute("UPDATE users SET current_game=? WHERE id=?", (game, sess["user_id"]))
    room_id = sess.get("room_id")
    if room_id:
        await broadcast_room_presence(room_id)


# ── FRIENDS ──────────────────────────────────────────────────────────────

async def on_friend_request(ws, sess, data):
    target_id = data.get("user_id")
    if not target_id or target_id == sess["user_id"]:
        return
    conn = db.get_conn()
    existing = conn.execute(
        "SELECT * FROM friendships WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
        (sess["user_id"], target_id, target_id, sess["user_id"]),
    ).fetchone()
    if existing:
        return
    with db.tx() as c:
        c.execute(
            "INSERT INTO friendships(user_id, friend_id, status, created_at) VALUES (?,?,?,?)",
            (sess["user_id"], target_id, "pending", now()),
        )
    await push_friend_lists(sess["user_id"])
    await push_friend_lists(target_id)


async def on_friend_respond(ws, sess, data):
    requester_id = data.get("user_id")
    accept = bool(data.get("accept"))
    conn = db.get_conn()
    if accept:
        with db.tx() as c:
            c.execute(
                "UPDATE friendships SET status='accepted' WHERE user_id=? AND friend_id=?",
                (requester_id, sess["user_id"]),
            )
    else:
        with db.tx() as c:
            c.execute(
                "DELETE FROM friendships WHERE user_id=? AND friend_id=?",
                (requester_id, sess["user_id"]),
            )
    await push_friend_lists(sess["user_id"])
    await push_friend_lists(requester_id)


# ── REPORTS & ADMIN ──────────────────────────────────────────────────────

async def on_report(ws, sess, data):
    message_id = data.get("message_id")
    target_user_id = data.get("target_user_id")
    reason = str(data.get("reason", ""))[:300]
    with db.tx() as c:
        c.execute(
            "INSERT INTO reports(reporter_id, message_id, target_user_id, reason, created_at) VALUES (?,?,?,?,?)",
            (sess["user_id"], message_id, target_user_id, reason, now()),
        )
    await send_to(ws, {"type": "report_result", "ok": True})
    # notify admins live
    conn = db.get_conn()
    admin_ids = [r["id"] for r in conn.execute("SELECT id FROM users WHERE role='admin'")]
    for aid in admin_ids:
        await send_to_user(aid, {"type": "new_report"})


async def on_admin_action(ws, sess, t, data):
    if not is_admin(sess["user_id"]):
        await send_to(ws, {"type": "error", "msg": "NOT_ADMIN"})
        return
    conn = db.get_conn()

    # Allow targeting by username as a convenience (admin panel quick-action box)
    if "user_id" not in data and data.get("username"):
        target_row = get_user_by_username(str(data["username"]).strip())
        if not target_row:
            await send_to(ws, {"type": "error", "msg": "USER_NOT_FOUND"})
            return
        data["user_id"] = target_row["id"]

    if t == "admin_ban":
        target = data.get("user_id")
        with db.tx() as c:
            c.execute("UPDATE users SET banned=1 WHERE id=?", (target,))
        await send_to_user(target, {"type": "banned"})
        for w in list(sockets_by_user.get(target, [])):
            await w.close()

    elif t == "admin_unban":
        target = data.get("user_id")
        with db.tx() as c:
            c.execute("UPDATE users SET banned=0 WHERE id=?", (target,))

    elif t == "admin_kick":
        target = data.get("user_id")
        for w in list(sockets_by_user.get(target, [])):
            await send_to(w, {"type": "kicked"})
            await w.close()

    elif t == "admin_mute":
        target = data.get("user_id")
        minutes = int(data.get("minutes", 10))
        with db.tx() as c:
            c.execute("UPDATE users SET muted_until=? WHERE id=?", (now() + minutes * 60, target))
        await send_to_user(target, {"type": "muted", "minutes": minutes})

    elif t == "admin_delete_msg":
        msg_id = data.get("message_id")
        row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        if row:
            with db.tx() as c:
                c.execute("UPDATE messages SET deleted=1 WHERE id=?", (msg_id,))
            payload = {"type": "message_deleted", "message_id": msg_id}
            if row["room_id"]:
                await broadcast_room(row["room_id"], payload)
            elif row["dm_key"]:
                a, b = row["dm_key"].split(":")
                await send_to_user(int(a), payload)
                await send_to_user(int(b), payload)
            # also confirm directly to the acting admin even if they're not currently
            # viewing the room/DM the message belonged to
            await send_to(ws, payload)

    elif t == "admin_add_word":
        word = str(data.get("word", "")).strip().lower()
        if word:
            with db.tx() as c:
                c.execute("INSERT OR IGNORE INTO banned_words(word) VALUES (?)", (word,))

    elif t == "admin_remove_word":
        word = str(data.get("word", "")).strip().lower()
        with db.tx() as c:
            c.execute("DELETE FROM banned_words WHERE word=?", (word,))

    elif t == "admin_get_reports":
        rows = conn.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 100").fetchall()
        out = []
        for r in rows:
            reporter = get_user_by_id(r["reporter_id"])
            target = get_user_by_id(r["target_user_id"]) if r["target_user_id"] else None
            out.append({
                "id": r["id"],
                "reporter": reporter["display_name"] if reporter else "?",
                "target": target["display_name"] if target else None,
                "target_id": r["target_user_id"],
                "message_id": r["message_id"],
                "reason": r["reason"],
                "ts": r["created_at"],
                "resolved": bool(r["resolved"]),
            })
        await send_to(ws, {"type": "reports_list", "reports": out})
        words = [r["word"] for r in conn.execute("SELECT word FROM banned_words")]
        await send_to(ws, {"type": "banned_words_list", "words": words})
        return

    await send_to(ws, {"type": "admin_action_result", "ok": True, "action": t})


# ══════════════════════════════════════════════════════════════════════════
#  HTTP routes
# ══════════════════════════════════════════════════════════════════════════

async def http_index(request):
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def http_upload(request):
    """Generic file upload (avatars + chat media). multipart/form-data with field 'file' and 'kind'."""
    reader = await request.multipart()
    field = await reader.next()
    kind = "media"
    file_bytes = b""
    filename = "upload"
    while field is not None:
        if field.name == "kind":
            kind = (await field.read()).decode().strip() or "media"
        elif field.name == "file":
            filename = field.filename or "upload"
            size = 0
            chunks = []
            while True:
                chunk = await field.read_chunk(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    return web.json_response({"ok": False, "msg": "FILE_TOO_LARGE"}, status=413)
                chunks.append(chunk)
            file_bytes = b"".join(chunks)
        field = await reader.next()

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return web.json_response({"ok": False, "msg": "INVALID_FILE_TYPE"}, status=400)
    if not file_bytes:
        return web.json_response({"ok": False, "msg": "EMPTY_FILE"}, status=400)

    target_dir = {"avatar": UPLOADS_AVATAR, "room_icon": UPLOADS_ROOM_ICON}.get(kind, UPLOADS_MEDIA)
    new_name = f"{uuid.uuid4().hex}{ext}"
    (target_dir / new_name).write_bytes(file_bytes)

    sub = {"avatar": "avatars", "room_icon": "room_icons"}.get(kind, "media")
    media_type = "gif" if ext == ".gif" else "image"
    url = f"/uploads/{sub}/{new_name}"
    return web.json_response({"ok": True, "url": url, "media_type": media_type})


async def http_health(request):
    return web.json_response({"ok": True, "status": "running"})


def purge_old_messages():
    """پیام‌های قدیمی‌تر از ۲۴ ساعت رو واقعاً از دیتابیس حذف می‌کنه (نه فقط مخفی‌شون می‌کنه)."""
    conn = db.get_conn()
    cutoff = now() - MESSAGE_RETENTION_SECONDS
    with db.tx() as c:
        cur = c.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
    return cur.rowcount


async def purge_old_messages_loop():
    while True:
        try:
            deleted = await asyncio.get_event_loop().run_in_executor(None, purge_old_messages)
            if deleted:
                print(f"🧹 {deleted} پیام قدیمی‌تر از ۲۴ ساعت حذف شد")
        except Exception as e:
            print("purge_old_messages_loop error:", e)
        await asyncio.sleep(30 * 60)  # هر ۳۰ دقیقه یه بار چک می‌کنه


async def start_background_tasks(app):
    app["purge_task"] = asyncio.create_task(purge_old_messages_loop())


async def cleanup_background_tasks(app):
    app["purge_task"].cancel()
    try:
        await app["purge_task"]
    except asyncio.CancelledError:
        pass



async def http_css(request):
    return web.Response(text=STYLE_CSS, content_type="text/css")


async def http_js(request):
    return web.Response(text=APP_JS, content_type="application/javascript")


def create_app():
    db.init_db()
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES + 1024)
    app.router.add_get("/", http_index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/upload", http_upload)
    app.router.add_get("/health", http_health)
    app.router.add_get("/static/css/style.css", http_css)
    app.router.add_get("/static/js/app.js", http_js)
    app.router.add_static("/uploads/", path=str(PERSIST_DIR / "uploads"), name="uploads")
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🎮 LINE Chat running → http://localhost:{port}\n")
    web.run_app(app, host="0.0.0.0", port=port)
