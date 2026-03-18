"""
╔══════════════════════════════════════════════════════════════╗
║          TELEGRAM GRUP YÖNETİM BOTU  v3.1                   ║
║  • Tüm işlemler DM'deki inline butonlardan yapılır           ║
║  • Bot senden adım adım bilgi ister (ID, miktar vs.)         ║
║  • Açıklayıcı, uzun panel metinleri                          ║
║  • Grupta /komut yazınca BotFather listesi görünür           ║
║  FIX v3.1:                                                   ║
║  • Kalıcı depolama (data.json)                               ║
║  • Webhook desteği (Railway için)                            ║
║  • Dinamik prompt'lar (banned_words / notes listeleri)       ║
║  • UTC-aware datetime                                        ║
║  • Admin hata bildirimi                                      ║
║  • asyncio.ensure_future → create_task                       ║
╚══════════════════════════════════════════════════════════════╝
"""

import json
import logging
import os
import re as _re
import asyncio
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    ForceReply,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes,
)
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError

# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ADMIN_ID     = int(os.environ["ADMIN_ID"])
GROUP_ID     = int(os.environ["GROUP_ID"])
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "")   # Boşsa polling kullanılır
PORT         = int(os.environ.get("PORT", 8080))
DATA_FILE    = "data.json"

# ──────────────────────────────────────────────────────────────
# UYGULAMA DURUMU (varsayılan değerler)
# ──────────────────────────────────────────────────────────────
warnings_db    : dict[int, int]      = {}
muted_users    : dict[int, datetime] = {}
banned_words   : list[str]           = []
notes          : dict[str, str]      = {}
welcome_msg    : str  = (
    "🎉 <b>KriptoDrop TR</b> Kanalımıza Hoş Geldiniz, {name}! 🎁\n\n"
    "🚀 Güncel airdroplardan anında haberdar olmak için\n\n"
    "🔔 <b>KriptoDrop TR DUYURU</b> 📢 Kanalımıza katılmayı ve "
    "kanal bildirimlerini açmayı unutmayın!\n\n"
    "\U0001F48E Bol kazançlar dileriz!"
)
auto_delete_sec: int  = 0
antiflood_on   : bool = True
antiflood_buf  : dict[int, list]     = {}   # RAM-only, kasıtlı kalıcı değil
group_locked   : bool = False
slowmode_sec   : int  = 0

invite_tracker : dict[int, dict]     = {}

scheduled_msg_text : str = (
    "📢 <b>KriptoDrop TR — Günlük Hatırlatma</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🚀 Bugünün güncel airdroplarını kaçırmamak için\n"
    "🔔 <b>KriptoDrop TR DUYURU</b> kanalımıza abone olun ve bildirimleri açın!\n\n"
    "\U0001F48E Bol kazançlar dileriz!"
)
scheduled_msg_hour  : int  = 9
scheduled_msg_min   : int  = 0
scheduled_msg_on    : bool = True

stats: dict[str, int] = {
    "total_messages"  : 0,
    "deleted_messages": 0,
    "banned_users"    : 0,
    "warned_users"    : 0,
}

pending     : dict[int, dict] = {}
select_start: dict[int, int]  = {}

# ──────────────────────────────────────────────────────────────
# KALICI DEPOLAMA
# ──────────────────────────────────────────────────────────────
def load_data():
    """Bot başlangıcında data.json'dan tüm kalıcı verileri yükler."""
    global warnings_db, banned_words, notes, welcome_msg, auto_delete_sec
    global antiflood_on, group_locked, slowmode_sec, invite_tracker
    global scheduled_msg_text, scheduled_msg_hour, scheduled_msg_min, scheduled_msg_on
    global stats, muted_users

    if not os.path.exists(DATA_FILE):
        logger.info("data.json bulunamadı, varsayılan değerlerle başlatılıyor.")
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)

        warnings_db = {int(k): v for k, v in d.get("warnings_db", {}).items()}
        banned_words[:] = d.get("banned_words", [])
        notes.update(d.get("notes", {}))
        welcome_msg        = d.get("welcome_msg", welcome_msg)
        auto_delete_sec    = d.get("auto_delete_sec", auto_delete_sec)
        antiflood_on       = d.get("antiflood_on", antiflood_on)
        group_locked       = d.get("group_locked", group_locked)
        slowmode_sec       = d.get("slowmode_sec", slowmode_sec)
        invite_tracker     = {int(k): v for k, v in d.get("invite_tracker", {}).items()}
        scheduled_msg_text = d.get("scheduled_msg_text", scheduled_msg_text)
        scheduled_msg_hour = d.get("scheduled_msg_hour", scheduled_msg_hour)
        scheduled_msg_min  = d.get("scheduled_msg_min", scheduled_msg_min)
        scheduled_msg_on   = d.get("scheduled_msg_on", scheduled_msg_on)
        stats.update(d.get("stats", {}))

        # muted_users: ISO string → UTC-aware datetime
        muted_users.update({
            int(k): datetime.fromisoformat(v)
            for k, v in d.get("muted_users", {}).items()
        })
        logger.info("✅ data.json başarıyla yüklendi.")
    except Exception as e:
        logger.error(f"Veri yükleme hatası: {e}")


def save_data():
    """Tüm kalıcı verileri data.json'a yazar."""
    try:
        d = {
            "warnings_db"        : {str(k): v for k, v in warnings_db.items()},
            "banned_words"       : banned_words,
            "notes"              : notes,
            "welcome_msg"        : welcome_msg,
            "auto_delete_sec"    : auto_delete_sec,
            "antiflood_on"       : antiflood_on,
            "group_locked"       : group_locked,
            "slowmode_sec"       : slowmode_sec,
            "invite_tracker"     : {str(k): v for k, v in invite_tracker.items()},
            "scheduled_msg_text" : scheduled_msg_text,
            "scheduled_msg_hour" : scheduled_msg_hour,
            "scheduled_msg_min"  : scheduled_msg_min,
            "scheduled_msg_on"   : scheduled_msg_on,
            "stats"              : stats,
            "muted_users"        : {
                str(k): v.isoformat() for k, v in muted_users.items()
            },
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Veri kaydetme hatası: {e}")


# Başlangıçta yükle
load_data()

# ──────────────────────────────────────────────────────────────
# YARDIMCILAR
# ──────────────────────────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def fmt(user) -> str:
    return f'<a href="tg://user?id={user.id}">{user.full_name}</a>'

def utcnow() -> datetime:
    """Timezone-aware UTC şu anki zaman."""
    return datetime.now(timezone.utc)

async def notify_admin(ctx, text: str):
    await ctx.bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)

async def auto_delete(ctx, chat_id: int, msg_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
    except TelegramError:
        pass

async def _bulk_delete(ctx, chat_id: int, from_id: int, to_id: int) -> int:
    """from_id ile to_id arasındaki (her ikisi dahil) mesajları 100'lük batch'lerle siler."""
    start_id = min(from_id, to_id)
    end_id   = max(from_id, to_id)
    all_ids  = list(range(start_id, end_id + 1))
    deleted  = 0

    for i in range(0, len(all_ids), 100):
        batch = all_ids[i:i + 100]
        try:
            await ctx.bot.delete_messages(chat_id, batch)
            deleted += len(batch)
        except TelegramError:
            for mid in batch:
                try:
                    await ctx.bot.delete_message(chat_id, mid)
                    deleted += 1
                except TelegramError:
                    pass
        await asyncio.sleep(0.05)

    return deleted

def back_btn(target="main") -> InlineKeyboardMarkup:
    labels = {
        "main"    : "🏠 Ana Menü",
        "users"   : "◀️ Kullanıcı Yönetimi",
        "msgs"    : "◀️ Mesaj Yönetimi",
        "settings": "◀️ Grup Ayarları",
        "security": "◀️ Güvenlik",
        "notes"   : "◀️ Not Sistemi",
        "info"    : "◀️ Bilgi & İstatistik",
    }
    return InlineKeyboardMarkup([[InlineKeyboardButton(labels.get(target, "◀️ Geri"), callback_data=f"menu_{target}")]])

# ──────────────────────────────────────────────────────────────
# ANA MENÜ
# ──────────────────────────────────────────────────────────────
MAIN_MENU_TEXT = (
    "🤖 <b>Grup Yönetim Paneli — v3.1</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Bu panel üzerinden grubunu <b>tek tıkla</b> yönetebilirsin.\n"
    "Aşağıdaki kategorilerden birini seç ve işlemini gerçekleştir.\n\n"
    "💡 <b>Nasıl çalışır?</b>\n"
    "Bir kategoriye tıkla → İşlem butonlarını gör → Butona bas → "
    "Bot senden gerekli bilgiyi ister → İşlem tamamlanır.\n\n"
    "📌 Grupta komut da kullanabilirsin (<code>/ban</code>, <code>/mute</code> vb.) "
    "ama bu panel çok daha pratik! 😎"
)

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 Kullanıcı Yönetimi", callback_data="menu_users"),
            InlineKeyboardButton("📢 Mesaj Yönetimi",     callback_data="menu_msgs"),
        ],
        [
            InlineKeyboardButton("⚙️ Grup Ayarları",      callback_data="menu_settings"),
            InlineKeyboardButton("🛡️ Güvenlik",           callback_data="menu_security"),
        ],
        [
            InlineKeyboardButton("📝 Not Sistemi",         callback_data="menu_notes"),
            InlineKeyboardButton("📊 Bilgi & İstatistik",  callback_data="menu_info"),
        ],
        [
            InlineKeyboardButton("📣 Gruba Duyuru Gönder", callback_data="menu_broadcast"),
        ],
        [
            InlineKeyboardButton("🏆 Davet Liderlik Tablosu", callback_data="menu_invites"),
            InlineKeyboardButton("⏰ Zamanlı Duyuru",         callback_data="menu_scheduled"),
        ],
    ])

# ──────────────────────────────────────────────────────────────
# KATEGORİ MENÜLERİ
# ──────────────────────────────────────────────────────────────
def users_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "👥 <b>Kullanıcı Yönetimi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Bu bölümden gruptaki kullanıcıları yönetebilirsin.\n\n"
        "🔨 <b>Banla</b> — Kullanıcıyı kalıcı olarak gruptan kovar ve bir daha girmesini engeller.\n"
        "✅ <b>Ban Kaldır</b> — Daha önce banlanan kullanıcının yasağını kaldırır, gruba tekrar girebilir.\n"
        "👢 <b>At (Kick)</b> — Kullanıcıyı gruptan atar, ancak davet linki ile tekrar girebilir.\n"
        "🔇 <b>Sustur (Mute)</b> — Kullanıcının mesaj göndermesini belirli bir süre engeller.\n"
        "🔊 <b>Sesi Aç</b> — Daha önce susturulan kullanıcıyı tekrar konuşturur.\n"
        "⚠️ <b>Uyarı Ver</b> — Kullanıcıya uyarı gönderir. <b>3 uyarıda otomatik ban!</b>\n"
        "🔄 <b>Uyarı Sıfırla</b> — Kullanıcının tüm uyarı geçmişini temizler.\n"
        "📊 <b>Uyarı Sorgula</b> — Bir kullanıcının kaç uyarısı olduğunu gösterir.\n"
        "⬆️ <b>Admin Yap</b> — Kullanıcıyı grup yöneticisi yapar.\n"
        "⬇️ <b>Admin'den Al</b> — Kullanıcının yönetici yetkilerini iptal eder.\n"
        "👤 <b>Kullanıcı Bilgisi</b> — ID, kullanıcı adı, grup durumu ve uyarı sayısını gösterir.\n\n"
        "💡 Bir işleme tıkladıktan sonra bot senden <b>kullanıcı ID'sini</b> gönder."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔨 Banla",          callback_data="act_ban"),
            InlineKeyboardButton("✅ Ban Kaldır",      callback_data="act_unban"),
            InlineKeyboardButton("👢 At",              callback_data="act_kick"),
        ],
        [
            InlineKeyboardButton("🔇 Sustur",          callback_data="act_mute"),
            InlineKeyboardButton("🔊 Sesi Aç",         callback_data="act_unmute"),
        ],
        [
            InlineKeyboardButton("⚠️ Uyarı Ver",       callback_data="act_warn"),
            InlineKeyboardButton("🔄 Uyarı Sıfırla",   callback_data="act_unwarn"),
            InlineKeyboardButton("📊 Uyarı Sorgula",   callback_data="act_warnings"),
        ],
        [
            InlineKeyboardButton("⬆️ Admin Yap",       callback_data="act_promote"),
            InlineKeyboardButton("⬇️ Admin'den Al",    callback_data="act_demote"),
        ],
        [
            InlineKeyboardButton("👤 Kullanıcı Bilgisi", callback_data="act_info"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def msgs_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📢 <b>Mesaj Yönetimi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Gruptaki mesajları bu bölümden yönetebilirsin.\n\n"
        "📌 <b>Mesaj Sabitle</b> — Gruba gidip bir mesajı yanıtla, sonra bu butona bas.\n"
        "📌 <b>Sabitlemeyi Kaldır</b> — Aktif sabitlenmiş mesajı kaldırır.\n"
        "🗑️ <b>Mesaj Sil</b> — Belirli bir mesajı grubun içinden kaldırır.\n"
        "🧹 <b>Son N Mesajı Sil</b> — İstediğin kadar mesajı toplu siler.\n"
        "💣 <b>Son 100 Mesajı Sil</b> — Grubun son 100 mesajını tek seferde temizler.\n"
        "⏩ <b>Şu Mesajdan Sonrasını Sil</b> — Grupta bir mesajı <b>yanıtlayıp</b> "
        "<code>/purgefrom</code> yaz.\n"
        "📣 <b>Duyuru Gönder</b> — Gruba resmi formatta bir duyuru mesajı gönderir.\n"
        "📊 <b>Anket Oluştur</b> — Grup içinde interaktif bir anket başlatır.\n\n"
        "⚠️ <b>Dikkat:</b> Silme işlemleri geri alınamaz!"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📌 Mesaj Sabitle",      callback_data="act_pin"),
            InlineKeyboardButton("📌 Sabitlemeyi Kaldır", callback_data="act_unpin"),
        ],
        [
            InlineKeyboardButton("🗑️ Tek Mesaj Sil",     callback_data="act_delete"),
            InlineKeyboardButton("🧹 Son N Mesajı Sil",   callback_data="act_purge_ask"),
        ],
        [
            InlineKeyboardButton("💣 Son 100 Mesajı Sil", callback_data="act_clearall"),
            InlineKeyboardButton("⏩ Mesajdan Sonrasını Sil", callback_data="act_purge_after"),
        ],
        [
            InlineKeyboardButton("📣 Duyuru Gönder",      callback_data="act_broadcast"),
            InlineKeyboardButton("📊 Anket Oluştur",      callback_data="act_poll"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def settings_menu() -> tuple[str, InlineKeyboardMarkup]:
    lock_icon  = "🔓 Grubu Aç" if group_locked else "🔒 Grubu Kilitle"
    lock_cb    = "act_unlock"  if group_locked else "act_lock"
    flood_icon = "🌊 Anti-Flood: ✅" if antiflood_on else "🌊 Anti-Flood: ❌"
    text = (
        "⚙️ <b>Grup Ayarları</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Grubun genel davranışını bu bölümden özelleştirebilirsin.\n\n"
        "👋 <b>Karşılama Mesajı</b> — Gruba yeni üye katıldığında otomatik gönderilir. "
        "Metinde <code>{name}</code>, <code>{group}</code>, <code>{id}</code> kullanabilirsin.\n"
        "🔒 <b>Grubu Kilitle</b> — Sadece adminlerin yazabildiği mod.\n"
        "🔓 <b>Grubu Aç</b> — Kilidi kaldırır, herkes tekrar yazabilir.\n"
        "🐌 <b>Yavaş Mod</b> — Üyeler arasına saniye cinsinden bekleme ekler.\n"
        "⏱️ <b>Otomatik Mesaj Silme</b> — Her mesaj belirtilen süre sonra silinir. "
        "0 girerek kapatabilirsin.\n"
        f"🌊 <b>Anti-Flood</b> — Şu an: <b>{'Aktif ✅' if antiflood_on else 'Pasif ❌'}</b>. "
        "10 saniye içinde 5'ten fazla mesaj atan üyeyi 5 dakika susturur.\n"
        "🔗 <b>Yeni Davet Linki</b> — Mevcut linki geçersiz kılar, yeni link oluşturur.\n\n"
        f"📌 <b>Mevcut Durum:</b> Kilit: {'🔒 Kilitli' if group_locked else '🔓 Açık'} | "
        f"Yavaş mod: {slowmode_sec}sn | Otomatik silme: {auto_delete_sec}sn"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👋 Karşılama Mesajı Ayarla", callback_data="act_setwelcome")],
        [
            InlineKeyboardButton(lock_icon,                  callback_data=lock_cb),
            InlineKeyboardButton("🐌 Yavaş Mod Ayarla",      callback_data="act_slowmode"),
        ],
        [
            InlineKeyboardButton("⏱️ Otomatik Silme Süresi", callback_data="act_autodelete"),
            InlineKeyboardButton(flood_icon,                  callback_data="act_toggle_flood"),
        ],
        [InlineKeyboardButton("🔗 Yeni Davet Linki Oluştur", callback_data="act_newlink")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def security_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "🛡️ <b>Güvenlik & Filtreler</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Grubunu istenmeyen içeriklerden korumak için filtreler ve otomatik önlemler.\n\n"
        "🚫 <b>Yasaklı Kelime Ekle</b> — Eklediğin kelimeyi içeren her mesaj otomatik silinir.\n"
        "✅ <b>Yasaklı Kelime Sil</b> — Listeden bir kelimeyi kaldırır.\n"
        "📋 <b>Yasaklı Kelime Listesi</b> — Aktif tüm filtre kelimelerini listeler.\n\n"
        "🤖 <b>Otomatik Güvenlik Sistemleri:</b>\n\n"
        "   🌊 <b>Anti-Flood</b> — 10 saniyede 5+ mesaj atan üye 5 dakika susturulur.\n"
        "   ⚠️ <b>Uyarı Sistemi</b> — 3 uyarıda otomatik ban.\n"
        "   🔤 <b>Kelime Filtresi</b> — Yasaklı kelime içeren mesaj silinir, uyarı verilir.\n"
        "   👤 <b>Yeni Üye Bildirimi</b> — Katılım anında admin'e DM bildirim.\n\n"
        f"📊 <b>Aktif Filtre Sayısı:</b> {len(banned_words)} kelime"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Kelime Filtresi Ekle", callback_data="act_addban"),
            InlineKeyboardButton("✅ Filtre Sil",            callback_data="act_removeban"),
        ],
        [InlineKeyboardButton("📋 Filtre Listesini Gör",    callback_data="act_listban")],
        [
            InlineKeyboardButton(
                f"🌊 Anti-Flood: {'✅ Aktif' if antiflood_on else '❌ Pasif'} → Değiştir",
                callback_data="act_toggle_flood"
            ),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def notes_menu() -> tuple[str, InlineKeyboardMarkup]:
    note_count = len(notes)
    note_list  = ", ".join(f"#{k}" for k in list(notes.keys())[:10]) or "Henüz not yok"
    text = (
        "📝 <b>Not Sistemi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Sık kullandığın metinleri, kuralları, linkleri not olarak kaydet.\n\n"
        "💾 <b>Not Kaydet</b> — Not adı ve içeriğini gir.\n"
        "📖 <b>Notu Gruba Gönder</b> — Seçtiğin notu gruba iletir.\n"
        "📋 <b>Tüm Notları Listele</b> — Kayıtlı tüm notların adlarını görürsün.\n"
        "🗑️ <b>Not Sil</b> — Artık kullanmadığın bir notu listeden kaldırır.\n\n"
        "💡 <b>Kısayol:</b> Grupta <code>#notadı</code> yazarsan bot otomatik gönderir!\n\n"
        f"📊 <b>Kayıtlı Not Sayısı:</b> {note_count}\n"
        f"📌 <b>Notlar:</b> {note_list}"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Yeni Not Kaydet",     callback_data="act_savenote"),
            InlineKeyboardButton("📖 Notu Gruba Gönder",   callback_data="act_sendnote"),
        ],
        [
            InlineKeyboardButton("📋 Tüm Notları Listele", callback_data="act_notes"),
            InlineKeyboardButton("🗑️ Not Sil",            callback_data="act_deletenote"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def info_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "📊 <b>Bilgi & İstatistik</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 <b>Kullanıcı Bilgisi</b> — Ad, ID, kullanıcı adı, rol ve uyarı sayısı.\n"
        "🏘️ <b>Grup Bilgisi</b> — Grubun adı, ID, üye sayısı, davet linki, kilit/yavaş mod durumu.\n"
        "👥 <b>Üye Sayısı</b> — Anlık üye sayısını gösterir.\n"
        "📈 <b>Bot İstatistikleri</b> — İşlenen/silinen mesaj, banlanan/uyarılan kullanıcı sayıları.\n"
        "🆔 <b>ID Göster</b> — Kendi Telegram ID'n ve chat ID'sini gösterir."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Kullanıcı Bilgisi",  callback_data="act_info"),
            InlineKeyboardButton("🏘️ Grup Bilgisi",       callback_data="act_groupinfo"),
        ],
        [
            InlineKeyboardButton("👥 Üye Sayısı",          callback_data="act_membercount"),
            InlineKeyboardButton("📈 Bot İstatistikleri",  callback_data="act_stats"),
        ],
        [InlineKeyboardButton("🆔 ID Göster",              callback_data="act_id")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def invites_menu() -> tuple[str, InlineKeyboardMarkup]:
    if not invite_tracker:
        board = "📭 Henüz davet verisi yok.\n\nBir üye gruba davet linki ile katıldığında burada görünür."
    else:
        sorted_inv = sorted(invite_tracker.items(), key=lambda x: x[1]["count"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, data) in enumerate(sorted_inv[:20]):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} <a href='tg://user?id={uid}'>{data['name']}</a> — <b>{data['count']}</b> davet")
        board = "\n".join(lines)
    text = (
        "🏆 <b>Davet Liderlik Tablosu</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{board}\n\n"
        "💡 Tabloyu sıfırlamak için Sıfırla butonuna bas."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Tabloyu Yenile",       callback_data="menu_invites"),
            InlineKeyboardButton("🗑️ Tabloyu Sıfırla",     callback_data="invite_reset"),
        ],
        [InlineKeyboardButton("📤 Tabloya Gruba Gönder",    callback_data="invite_send_group")],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

def scheduled_menu() -> tuple[str, InlineKeyboardMarkup]:
    status_icon  = "✅ Aktif" if scheduled_msg_on else "❌ Pasif"
    toggle_label = "⏸️ Duyuruyu Durdur" if scheduled_msg_on else "▶️ Duyuruyu Başlat"
    text = (
        "⏰ <b>Zamanlı Duyuru Ayarları</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Her gün belirlediğin saatte gruba otomatik duyuru gönderilir.\n\n"
        f"🕐 Gönderim saati (UTC): <b>{scheduled_msg_hour:02d}:{scheduled_msg_min:02d}</b>\n"
        f"   (Türkiye saati ≈ {(scheduled_msg_hour + 3) % 24:02d}:{scheduled_msg_min:02d})\n"
        f"📌 Durum: <b>{status_icon}</b>\n\n"
        f"📝 <b>Duyuru Metni Önizleme:</b>\n"
        f"<i>{scheduled_msg_text[:250]}{'...' if len(scheduled_msg_text) > 250 else ''}</i>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Metni Düzenle",  callback_data="act_set_scheduled_text"),
            InlineKeyboardButton("🕐 Saati Değiştir", callback_data="act_set_scheduled_time"),
        ],
        [
            InlineKeyboardButton(toggle_label,         callback_data="scheduled_toggle"),
            InlineKeyboardButton("📤 Şimdi Gönder",   callback_data="scheduled_send_now"),
        ],
        [InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_main")],
    ])
    return text, kb

# ──────────────────────────────────────────────────────────────
# DİNAMİK ACTION PROMPT'LAR
# ──────────────────────────────────────────────────────────────
# act_removeban, act_sendnote, act_deletenote çalışma zamanında
# güncel listeleri göstermesi gerektiği için fonksiyon olarak tanımlandı.
_STATIC_PROMPTS: dict[str, str] = {
    "act_ban"       : "🔨 <b>Kullanıcı Banla</b>\n\nBanlamak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n(Opsiyonel: ID'nin ardından boşluk bırakıp neden yazabilirsin)\n\nÖrnek: <code>123456789 spam yapıyor</code>",
    "act_unban"     : "✅ <b>Ban Kaldır</b>\n\nBanını kaldırmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_kick"      : "👢 <b>Kullanıcı At</b>\n\nAtmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_mute"      : "🔇 <b>Kullanıcı Sustur</b>\n\nSusturmak istediğin kullanıcının <b>ID ve dakika süresini</b> gönder.\n\nÖrnek: <code>123456789 30</code>\n(Süre girmezsen varsayılan 60 dakika)",
    "act_unmute"    : "🔊 <b>Sesi Aç</b>\n\nSusturmasını kaldırmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_warn"      : "⚠️ <b>Uyarı Ver</b>\n\nUyarmak istediğin kullanıcının <b>ID ve uyarı nedenini</b> gönder.\n⚡ 3 uyarıda otomatik ban!\n\nÖrnek: <code>123456789 kurallara uymadı</code>",
    "act_unwarn"    : "🔄 <b>Uyarı Sıfırla</b>\n\nUyarılarını sıfırlamak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_warnings"  : "📊 <b>Uyarı Sorgula</b>\n\nUyarılarını sorgulamak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_promote"   : "⬆️ <b>Admin Yap</b>\n\nAdmin yapmak istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_demote"    : "⬇️ <b>Admin'den Al</b>\n\nYetkilerini iptal etmek istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_info"      : "👤 <b>Kullanıcı Bilgisi</b>\n\nBilgilerini görmek istediğin kullanıcının <b>Telegram ID'sini</b> gönder.\n\nÖrnek: <code>123456789</code>",
    "act_pin"       : "📌 <b>Mesaj Sabitle</b>\n\nSabitlemek istediğin mesajın <b>mesaj ID'sini</b> gönder.\n\nÖrnek: <code>1234</code>",
    "act_delete"    : "🗑️ <b>Mesaj Sil</b>\n\nSilmek istediğin mesajın <b>mesaj ID'sini</b> gönder.\n\nÖrnek: <code>1234</code>",
    "act_purge_ask" : "🧹 <b>Son N Mesajı Sil</b>\n\nKaç mesaj silmek istediğini yaz.\n📌 Maksimum: 200 mesaj\n⚠️ Bu işlem geri alınamaz!\n\nÖrnek: <code>20</code>",
    "act_purge_after": "⏩ <b>Şu Mesajdan Sonrasını Sil</b>\n\nGruba git, silmenin başlamasını istediğin mesajı <b>yanıtla (reply)</b> ve şunu yaz:\n\n<code>/purgefrom</code>\n\nYa da mesaj ID'sini sayı olarak gönderebilirsin.",
    "act_broadcast" : "📣 <b>Gruba Duyuru Gönder</b>\n\nDuyuru metnini yaz.\n\nHTML etiketlerini kullanabilirsin: <code>&lt;b&gt;kalın&lt;/b&gt;</code>, <code>&lt;i&gt;italik&lt;/i&gt;</code>",
    "act_poll"      : "📊 <b>Anket Oluştur</b>\n\nSoru ve seçenekleri <b>| (boru çizgisi)</b> ile ayırarak gönder.\n\nFormat: <code>Soru?|Seçenek1|Seçenek2|Seçenek3</code>",
    "act_setwelcome": "👋 <b>Karşılama Mesajı Ayarla</b>\n\nYeni karşılama metnini yaz. HTML formatı desteklenir.\n\n🔑 <b>Değişkenler:</b>\n• <code>{name}</code> → Üyenin adı\n• <code>{id}</code> → Üyenin ID'si\n• <code>{group}</code> → Grubun adı",
    "act_slowmode"  : "🐌 <b>Yavaş Mod Ayarla</b>\n\nKaç saniyelik yavaş mod istiyorsun? Sıfır (0) girerek kapatabilirsin.\n\n• <code>0</code> → Kapat\n• <code>10</code> → 10 saniye\n• <code>30</code> → 30 saniye\n• <code>60</code> → 1 dakika",
    "act_autodelete": "⏱️ <b>Otomatik Mesaj Silme</b>\n\nKaç saniye sonra mesajlar otomatik silinsin? Sıfır (0) girerek kapatabilirsin.\n\n• <code>0</code> → Kapat\n• <code>3600</code> → 1 saat\n• <code>86400</code> → 1 gün",
    "act_addban"    : "🚫 <b>Yasaklı Kelime Ekle</b>\n\nFiltrelemek istediğin kelimeyi yaz.\n⚠️ Bu kelimeyi içeren her mesaj otomatik silinecek!\n\nKelimeyi yaz:",
    "act_savenote"  : "💾 <b>Not Kaydet</b>\n\nÖnce not adını, sonra bir boşluk bırakıp içeriğini yaz.\n\nFormat: <code>notadı Not içeriği buraya</code>",
    "act_set_scheduled_text": (
        "✏️ <b>Zamanlı Duyuru Metnini Düzenle</b>\n\n"
        "Yeni duyuru metnini yaz. HTML formatı desteklenir.\n"
        "⚠️ Duyuru butonları (Duyuru Kanalı, Kurallar, SSS) otomatik eklenir.\n\n"
        "Yeni metin:"
    ),
    "act_set_scheduled_time": (
        "🕐 <b>Zamanlı Duyuru Saatini Değiştir</b>\n\n"
        "Duyurunun gönderileceği saati <b>UTC</b> olarak gir.\n"
        "Format: <code>SS:DD</code>\n\n"
        "• <code>06:00</code> → Türkiye 09:00\n"
        "• <code>09:00</code> → Türkiye 12:00\n"
        "• <code>15:00</code> → Türkiye 18:00\n\n"
        "Saat (UTC olarak SS:DD):"
    ),
}

# Geçerli action listesi (callback handler kontrolü için)
VALID_ACTIONS = set(_STATIC_PROMPTS.keys()) | {"act_removeban", "act_sendnote", "act_deletenote"}

def get_action_prompt(action: str) -> str:
    """Verilen action için prompt metnini döndürür.
    Dinamik prompt gerektiren action'lar çalışma zamanında üretilir."""
    if action == "act_removeban":
        word_list = ", ".join(f"<code>{w}</code>" for w in banned_words) or "Liste boş"
        return f"✅ <b>Yasaklı Kelime Kaldır</b>\n\nListeden kaldırmak istediğin kelimeyi yaz.\n\nMevcut kelimeler: {word_list}"
    if action == "act_sendnote":
        note_list = ", ".join(f"<code>#{k}</code>" for k in list(notes.keys())[:15]) or "Henüz not yok"
        return f"📖 <b>Notu Gruba Gönder</b>\n\nGruba göndermek istediğin notun adını yaz.\n\nMevcut notlar: {note_list}"
    if action == "act_deletenote":
        note_list = ", ".join(f"<code>#{k}</code>" for k in list(notes.keys())[:15]) or "Henüz not yok"
        return f"🗑️ <b>Not Sil</b>\n\nSilmek istediğin notun adını yaz.\n\nMevcut notlar: {note_list}"
    return _STATIC_PROMPTS.get(action, "❓ Bilinmeyen işlem.")

# ──────────────────────────────────────────────────────────────
# /start  /help
# ──────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Bu bot yalnızca grup sahibi tarafından kullanılabilir.")
        return
    await update.message.reply_text(MAIN_MENU_TEXT, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            await update.message.delete()
        except TelegramError:
            pass
        return
    await cmd_start(update, ctx)

# ──────────────────────────────────────────────────────────────
# CALLBACK HANDLER
# ──────────────────────────────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    uid  = q.from_user.id
    await q.answer()

    if not is_admin(uid):
        await q.answer("⛔ Yetkisiz erişim!", show_alert=True)
        return

    # ── Menü navigasyonu ────────────────────────────────────
    if data == "menu_main":
        await q.message.edit_text(MAIN_MENU_TEXT, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        return

    if data == "menu_users":
        txt, kb = users_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_msgs":
        txt, kb = msgs_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_settings":
        txt, kb = settings_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_security":
        txt, kb = security_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_notes":
        txt, kb = notes_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_info":
        txt, kb = info_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "menu_broadcast":
        pending[uid] = {"action": "act_broadcast"}
        await q.message.edit_text(
            get_action_prompt("act_broadcast"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="menu_main")]]),
        )
        return

    if data == "menu_invites":
        txt, kb = invites_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "invite_reset":
        invite_tracker.clear()
        save_data()
        await q.answer("✅ Davet tablosu sıfırlandı!", show_alert=True)
        txt, kb = invites_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "invite_send_group":
        if not invite_tracker:
            await q.answer("📭 Gösterilecek davet verisi yok.", show_alert=True)
            return
        sorted_inv = sorted(invite_tracker.items(), key=lambda x: x[1]["count"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (iuid, idata) in enumerate(sorted_inv[:20]):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} <a href='tg://user?id={iuid}'>{idata['name']}</a> — <b>{idata['count']}</b> davet")
        board_text = "\n".join(lines)
        try:
            await ctx.bot.send_message(
                GROUP_ID,
                f"🏆 <b>Davet Liderlik Tablosu</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{board_text}",
                parse_mode=ParseMode.HTML,
            )
            await q.answer("✅ Tablo gruba gönderildi!", show_alert=True)
        except TelegramError as e:
            await q.answer(f"❌ Hata: {e}", show_alert=True)
        return

    if data == "menu_scheduled":
        txt, kb = scheduled_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "scheduled_toggle":
        global scheduled_msg_on
        scheduled_msg_on = not scheduled_msg_on
        save_data()
        status = "▶️ Başlatıldı" if scheduled_msg_on else "⏸️ Durduruldu"
        await q.answer(f"Zamanlı duyuru {status}", show_alert=True)
        txt, kb = scheduled_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "scheduled_send_now":
        await _send_scheduled_msg(ctx)
        await q.answer("✅ Duyuru şimdi gönderildi!", show_alert=True)
        return

    if data == "act_unpin":
        await _exec_unpin(q.message, ctx)
        return

    if data == "act_lock":
        await _exec_lock(q.message, ctx, lock=True)
        return

    if data == "act_unlock":
        await _exec_lock(q.message, ctx, lock=False)
        return

    if data == "act_toggle_flood":
        global antiflood_on
        antiflood_on = not antiflood_on
        save_data()
        status = "✅ Aktif" if antiflood_on else "❌ Pasif"
        await q.answer(f"Anti-Flood şimdi: {status}", show_alert=True)
        txt, kb = settings_menu()
        await q.message.edit_text(txt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    if data == "act_newlink":
        try:
            link = await ctx.bot.export_chat_invite_link(GROUP_ID)
            await q.message.reply_text(
                f"🔗 <b>Yeni Davet Linki Oluşturuldu</b>\n\nEski link artık geçersiz.\nYeni link:\n{link}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await q.message.reply_text(f"❌ Hata: {e}")
        return

    if data == "act_listban":
        if not banned_words:
            await q.message.reply_text("📋 Yasaklı kelime listesi boş.")
        else:
            await q.message.reply_text(
                "📋 <b>Aktif Kelime Filtreleri</b>\n━━━━━━━━━━━━━━━━\n" +
                "\n".join(f"🚫 <code>{w}</code>" for w in banned_words),
                parse_mode=ParseMode.HTML,
            )
        return

    if data == "act_notes":
        if not notes:
            await q.message.reply_text("📋 Kayıtlı not bulunamadı.")
        else:
            await q.message.reply_text(
                "📝 <b>Kayıtlı Notlar</b>\n━━━━━━━━━━━━━━━━\n" +
                "\n".join(f"• <code>#{k}</code>" for k in notes.keys()) +
                "\n\n💡 Grupta <code>#notadı</code> yazarak gösterebilirsin.",
                parse_mode=ParseMode.HTML,
            )
        return

    if data == "act_groupinfo":
        await _exec_groupinfo(q.message, ctx)
        return

    if data == "act_membercount":
        try:
            count = await ctx.bot.get_chat_member_count(GROUP_ID)
            await q.message.reply_text(f"👥 Anlık üye sayısı: <b>{count}</b>", parse_mode=ParseMode.HTML)
        except TelegramError as e:
            await q.message.reply_text(f"❌ Hata: {e}")
        return

    if data == "act_stats":
        await _exec_stats(q.message)
        return

    if data == "act_id":
        await q.message.reply_text(
            f"🆔 <b>ID Bilgisi</b>\n\n"
            f"👤 Senin ID'n: <code>{uid}</code>\n"
            f"💬 Grup ID: <code>{GROUP_ID}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "act_clearall":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Evet, 100 Mesajı Sil!", callback_data="clearall_confirm"),
            InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
        ]])
        await q.message.edit_text(
            "⚠️ <b>UYARI — Toplu Mesaj Silme</b>\n\n"
            "Grubun son <b>100 mesajını</b> silmek üzeresin.\n\n"
            "• Bu işlem <b>geri alınamaz!</b>\n"
            "Devam etmek istiyor musun?",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    if data == "clearall_confirm":
        chat_id = q.message.chat.id
        await q.message.edit_text("🗑️ Silme işlemi başladı, lütfen bekleyin...")
        try:
            sentinel = await ctx.bot.send_message(chat_id, "🧹")
            last_id  = sentinel.message_id
            await ctx.bot.delete_message(chat_id, last_id)
        except TelegramError as e:
            await q.message.edit_text(f"❌ Hata: {e}")
            return
        deleted = await _bulk_delete(ctx, chat_id, last_id - 1, last_id - 100)
        stats["deleted_messages"] += deleted
        save_data()
        result_msg = await ctx.bot.send_message(
            chat_id, f"✅ <b>{deleted}</b> mesaj silindi.", parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, result_msg.message_id, 5))
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return

    if data.startswith("purge_confirm:"):
        n       = int(data.split(":")[1])
        chat_id = q.message.chat.id
        await q.message.edit_text(f"🧹 Son <b>{n}</b> mesaj siliniyor...", parse_mode=ParseMode.HTML)
        try:
            sentinel = await ctx.bot.send_message(chat_id, "🧹")
            last_id  = sentinel.message_id
            await ctx.bot.delete_message(chat_id, last_id)
        except TelegramError as e:
            await q.message.edit_text(f"❌ Hata: {e}")
            return
        deleted = await _bulk_delete(ctx, chat_id, last_id - 1, last_id - n)
        stats["deleted_messages"] += deleted
        save_data()
        result_msg = await ctx.bot.send_message(
            chat_id, f"✅ <b>{deleted}</b> mesaj silindi.", parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, result_msg.message_id, 5))
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return

    if data.startswith("purge_after_confirm:"):
        from_id = int(data.split(":")[1])
        chat_id = q.message.chat.id
        await q.message.edit_text(
            f"⏩ Siliniyor... (mesaj ID {from_id}'den itibaren)", parse_mode=ParseMode.HTML
        )
        try:
            sentinel = await ctx.bot.send_message(chat_id, "🧹")
            last_id  = sentinel.message_id
            await ctx.bot.delete_message(chat_id, last_id)
        except TelegramError as e:
            await q.message.edit_text(f"❌ Hata: {e}")
            return
        deleted = await _bulk_delete(ctx, chat_id, last_id - 1, from_id)
        stats["deleted_messages"] += deleted
        save_data()
        result_msg = await ctx.bot.send_message(
            chat_id, f"✅ <b>{deleted}</b> mesaj silindi.", parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, result_msg.message_id, 5))
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return

    if data == "purgefrom_cancel":
        await q.message.delete()
        return

    if data.startswith("select_confirm:"):
        _, start_id, end_id = data.split(":")
        start_id = int(start_id)
        end_id   = int(end_id)
        chat_id  = q.message.chat.id
        await q.message.edit_text(
            f"🗑️ <code>{start_id}</code> → <code>{end_id}</code> arası siliniyor...",
            parse_mode=ParseMode.HTML,
        )
        deleted = await _bulk_delete(ctx, chat_id, start_id, end_id)
        stats["deleted_messages"] += deleted
        save_data()
        result_msg = await ctx.bot.send_message(
            chat_id,
            f"✅ Seçili aralıktan <b>{deleted}</b> mesaj silindi.",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, result_msg.message_id, 5))
        try:
            await q.message.delete()
        except TelegramError:
            pass
        return

    if data == "select_cancel":
        await q.message.delete()
        return

    if data == "rules":
        await q.message.reply_text(
            "📋 <b>Grup Kuralları</b>\n━━━━━━━━━━━━━━━━\n"
            "1️⃣ Saygılı ve nazik olun\n"
            "2️⃣ Spam ve flood yapmayın\n"
            "3️⃣ Reklam ve tanıtım yasaktır\n"
            "4️⃣ Hakaret ve küfür yasaktır\n"
            "5️⃣ Kurallara uymayanlar banlanır",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Girdi gerektiren işlemler → pending'e ekle ──────────
    if data in VALID_ACTIONS:
        pending[uid] = {"action": data}
        await q.message.edit_text(
            get_action_prompt(data),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="menu_main")]]),
        )
        return

# ──────────────────────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR (exec helpers)
# ──────────────────────────────────────────────────────────────
async def _exec_unpin(msg, ctx):
    try:
        await ctx.bot.unpin_chat_message(GROUP_ID)
        await msg.reply_text("📌 Sabitleme kaldırıldı.")
    except TelegramError as e:
        await msg.reply_text(f"❌ Hata: {e}")

async def _exec_lock(msg, ctx, lock: bool):
    global group_locked
    group_locked = lock
    save_data()
    perms = ChatPermissions(can_send_messages=not lock)
    try:
        await ctx.bot.set_chat_permissions(GROUP_ID, perms)
        icon = "🔒" if lock else "🔓"
        status = "kilitlendi" if lock else "açıldı"
        await msg.reply_text(f"{icon} Grup {status}.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await msg.reply_text(f"❌ Hata: {e}")

async def _exec_groupinfo(msg, ctx):
    try:
        chat  = await ctx.bot.get_chat(GROUP_ID)
        count = await ctx.bot.get_chat_member_count(GROUP_ID)
        await msg.reply_text(
            f"🏘️ <b>Grup Bilgisi</b>\n━━━━━━━━━━━━━━━━\n"
            f"📛 Ad: <b>{chat.title}</b>\n"
            f"🆔 ID: <code>{chat.id}</code>\n"
            f"👥 Üye sayısı: <b>{count}</b>\n"
            f"📝 Açıklama: {chat.description or 'Yok'}\n"
            f"🔗 Davet linki: {chat.invite_link or 'Yok'}\n"
            f"🔒 Kilit: {'Evet' if group_locked else 'Hayır'}\n"
            f"🐌 Yavaş mod: {slowmode_sec}sn\n"
            f"⏱️ Oto-silme: {auto_delete_sec}sn",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await msg.reply_text(f"❌ Hata: {e}")

async def _exec_stats(msg):
    await msg.reply_text(
        f"📈 <b>Bot İstatistikleri</b>\n━━━━━━━━━━━━━━━━\n"
        f"💬 İşlenen mesaj: <b>{stats['total_messages']}</b>\n"
        f"🗑️ Silinen mesaj: <b>{stats['deleted_messages']}</b>\n"
        f"🔨 Banlanan kullanıcı: <b>{stats['banned_users']}</b>\n"
        f"⚠️ Uyarılan kullanıcı: <b>{stats['warned_users']}</b>\n"
        f"🚫 Aktif filtre: <b>{len(banned_words)}</b>\n"
        f"📝 Kayıtlı not: <b>{len(notes)}</b>\n"
        f"🔒 Kilit: {'Aktif' if group_locked else 'Pasif'}\n"
        f"🌊 Anti-flood: {'Aktif' if antiflood_on else 'Pasif'}\n"
        f"🐌 Yavaş mod: {slowmode_sec}sn\n"
        f"⏱️ Oto-silme: {auto_delete_sec}sn",
        parse_mode=ParseMode.HTML,
    )

# ──────────────────────────────────────────────────────────────
# DM METİN HANDLER
# ──────────────────────────────────────────────────────────────
async def handle_dm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = (update.message.text or update.message.caption or "").strip()

    if not is_admin(uid):
        return

    # > ile gruba mesaj ilet
    if text.startswith(">") and not update.message.forward_date and not getattr(update.message, "forward_origin", None):
        msg = text[1:].strip()
        if msg:
            try:
                await ctx.bot.send_message(
                    GROUP_ID,
                    f"📢 <b>Yönetici Mesajı</b>\n━━━━━━━━━━━━\n{msg}",
                    parse_mode=ParseMode.HTML,
                )
                await update.message.reply_text("✅ Mesaj gruba iletildi.")
            except TelegramError as e:
                await update.message.reply_text(f"❌ Hata: {e}")
        return

    if uid not in pending:
        await update.message.reply_text(
            "💡 Paneli açmak için /start — Gruba mesaj iletmek için: <code>&gt; mesajın</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return

    action = pending[uid]["action"]

    # act_purge_after için iletilen mesajın ID'sini çek
    if action == "act_purge_after":
        fwd_msg_id = None
        fwd_err    = None
        m = update.message
        origin = getattr(m, "forward_origin", None)
        if origin:
            msg_id  = getattr(origin, "message_id", None)
            chat    = getattr(origin, "chat", None)
            chat_id = getattr(chat, "id", None) if chat else None
            if msg_id:
                if chat_id and chat_id != GROUP_ID:
                    fwd_err = f"⚠️ Bu mesaj <b>farklı bir gruptan</b> iletilmiş.\nLütfen <b>hedef grubunuzdaki</b> bir mesajı iletin."
                else:
                    fwd_msg_id = msg_id
            else:
                fwd_err = "⚠️ Bu mesaj bir <b>kullanıcıdan</b> iletilmiş, mesaj ID'si alınamıyor."
        elif getattr(m, "forward_from_chat", None) and getattr(m, "forward_from_message_id", None):
            if m.forward_from_chat.id == GROUP_ID:
                fwd_msg_id = m.forward_from_message_id
            else:
                fwd_err = "⚠️ Bu mesaj farklı bir gruptan iletilmiş."
        elif getattr(m, "forward_date", None):
            fwd_err = "⚠️ Mesaj ID'si alınamadı. Lütfen grubunuzdaki bir mesajı iletin."

        if fwd_msg_id:
            del pending[uid]
            await _process_action(update, ctx, action, str(fwd_msg_id))
            return
        elif fwd_err:
            await m.reply_text(fwd_err, parse_mode=ParseMode.HTML)
            return
        elif not m.text or not m.text.strip().isdigit():
            await m.reply_text(
                "⚠️ Mesaj algılanamadı.\n\n"
                "Grupta bir mesajı <b>İlet (Forward)</b> yapıp bota gönderin,\n"
                "ya da mesaj ID'sini rakam olarak yazın.",
                parse_mode=ParseMode.HTML,
            )
            return

    del pending[uid]
    await _process_action(update, ctx, action, text)

# ──────────────────────────────────────────────────────────────
# _process_action — tüm işlemlerin yürütücüsü
# ──────────────────────────────────────────────────────────────
async def _process_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE, action: str, text: str):
    msg = update.message

    async def get_uid_and_rest(default_reason="Belirtilmedi"):
        parts = text.strip().split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            await msg.reply_text("❌ Geçersiz format. Lütfen bir <b>kullanıcı ID</b> gir.", parse_mode=ParseMode.HTML)
            return None, None
        return int(parts[0]), parts[1] if len(parts) > 1 else default_reason

    # ── BAN ─────────────────────────────────────────────────
    if action == "act_ban":
        uid, reason = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.ban_chat_member(GROUP_ID, uid)
            stats["banned_users"] += 1
            save_data()
            await msg.reply_text(
                f"🔨 <b>Kullanıcı Banlandı</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 Kullanıcı: {fmt(member.user)}\n🆔 ID: <code>{uid}</code>\n📝 Neden: {reason}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── UNBAN ───────────────────────────────────────────────
    elif action == "act_unban":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            await ctx.bot.unban_chat_member(GROUP_ID, uid)
            await msg.reply_text(
                f"✅ <b>Ban Kaldırıldı</b>\n\n🆔 ID <code>{uid}</code> numaralı kullanıcının yasağı kaldırıldı.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── KICK ────────────────────────────────────────────────
    elif action == "act_kick":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.ban_chat_member(GROUP_ID, uid)
            await ctx.bot.unban_chat_member(GROUP_ID, uid)
            await msg.reply_text(
                f"👢 <b>Kullanıcı Atıldı</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 {fmt(member.user)}\n🆔 ID: <code>{uid}</code>\nℹ️ Davet linki ile tekrar girebilir.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── MUTE ────────────────────────────────────────────────
    elif action == "act_mute":
        parts = text.strip().split()
        if not parts or not parts[0].isdigit():
            await msg.reply_text("❌ Örnek: <code>123456789 30</code>", parse_mode=ParseMode.HTML)
            return
        uid     = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
        until   = utcnow() + timedelta(minutes=minutes)
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.restrict_chat_member(
                GROUP_ID, uid,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            muted_users[uid] = until
            save_data()
            await msg.reply_text(
                f"🔇 <b>Kullanıcı Susturuldu</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 {fmt(member.user)}\n🆔 ID: <code>{uid}</code>\n"
                f"⏱️ Süre: {minutes} dakika\n🕐 Bitiş: {until.strftime('%H:%M, %d.%m.%Y')} UTC",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── UNMUTE ──────────────────────────────────────────────
    elif action == "act_unmute":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.restrict_chat_member(
                GROUP_ID, uid,
                permissions=ChatPermissions(
                    can_send_messages=True, can_send_media_messages=True,
                    can_send_other_messages=True, can_add_web_page_previews=True,
                ),
            )
            muted_users.pop(uid, None)
            save_data()
            await msg.reply_text(
                f"🔊 <b>Susturma Kaldırıldı</b>\n\n👤 {fmt(member.user)} artık mesaj gönderebilir.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── WARN ────────────────────────────────────────────────
    elif action == "act_warn":
        uid, reason = await get_uid_and_rest("Kural ihlali")
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            warnings_db[uid] = warnings_db.get(uid, 0) + 1
            count = warnings_db[uid]
            stats["warned_users"] += 1
            if count >= 3:
                await ctx.bot.ban_chat_member(GROUP_ID, uid)
                stats["banned_users"] += 1
                warnings_db.pop(uid, None)
                save_data()
                await msg.reply_text(
                    f"🔨 <b>Otomatik Ban!</b>\n━━━━━━━━━━━━━━━━\n"
                    f"👤 {fmt(member.user)} 3 uyarıya ulaştı ve <b>otomatik banlandı!</b>\n"
                    f"📝 Son neden: {reason}",
                    parse_mode=ParseMode.HTML,
                )
            else:
                save_data()
                await msg.reply_text(
                    f"⚠️ <b>Uyarı Verildi</b>\n━━━━━━━━━━━━━━━━\n"
                    f"👤 {fmt(member.user)}\n📊 Uyarı sayısı: <b>{count}/3</b>\n📝 Neden: {reason}\n\n"
                    f"{'⚡ Bir daha uyarılırsa otomatik ban!' if count == 2 else f'{3-count} uyarı hakkı kaldı.'}",
                    parse_mode=ParseMode.HTML,
                )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── UNWARN ──────────────────────────────────────────────
    elif action == "act_unwarn":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        prev = warnings_db.pop(uid, 0)
        save_data()
        await msg.reply_text(
            f"🔄 <b>Uyarılar Sıfırlandı</b>\n\n🆔 ID <code>{uid}</code> — {prev} uyarı temizlendi.",
            parse_mode=ParseMode.HTML,
        )

    # ── WARNINGS ────────────────────────────────────────────
    elif action == "act_warnings":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        count = warnings_db.get(uid, 0)
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            uname  = fmt(member.user)
        except Exception:
            uname  = f"ID <code>{uid}</code>"
        await msg.reply_text(
            f"📊 <b>Uyarı Durumu</b>\n━━━━━━━━━━━━━━━━\n"
            f"👤 {uname}\n⚠️ Uyarı: <b>{count}/3</b>\n\n"
            f"{'🔴 Bir uyarı daha → otomatik ban!' if count == 2 else '🟢 Sorunsuz.' if count == 0 else '🟡 Dikkat gerekiyor.'}",
            parse_mode=ParseMode.HTML,
        )

    # ── PROMOTE ─────────────────────────────────────────────
    elif action == "act_promote":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.promote_chat_member(
                GROUP_ID, uid,
                can_delete_messages=True, can_restrict_members=True,
                can_pin_messages=True, can_manage_chat=True,
            )
            await msg.reply_text(
                f"⬆️ <b>Admin Yapıldı</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 {fmt(member.user)} artık grup yöneticisi.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── DEMOTE ──────────────────────────────────────────────
    elif action == "act_demote":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            await ctx.bot.promote_chat_member(
                GROUP_ID, uid,
                can_delete_messages=False, can_restrict_members=False,
                can_pin_messages=False, can_manage_chat=False,
            )
            await msg.reply_text(
                f"⬇️ <b>Yetkiler Alındı</b>\n\n👤 {fmt(member.user)} artık normal üye.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── INFO ────────────────────────────────────────────────
    elif action == "act_info":
        uid, _ = await get_uid_and_rest()
        if uid is None: return
        try:
            member = await ctx.bot.get_chat_member(GROUP_ID, uid)
            u = member.user
            status_map = {
                "creator": "👑 Kurucu", "administrator": "🛡️ Admin",
                "member": "👤 Üye", "restricted": "⛔ Kısıtlı",
                "left": "🚪 Ayrıldı", "kicked": "🔨 Banlı",
            }
            await msg.reply_text(
                f"👤 <b>Kullanıcı Profili</b>\n━━━━━━━━━━━━━━━━\n"
                f"👤 Ad: {fmt(u)}\n🆔 ID: <code>{u.id}</code>\n"
                f"📛 Kullanıcı adı: @{u.username or 'Yok'}\n"
                f"📊 Grup rolü: {status_map.get(member.status, member.status)}\n"
                f"⚠️ Uyarılar: {warnings_db.get(u.id, 0)}/3\n"
                f"🤖 Bot hesabı: {'Evet' if u.is_bot else 'Hayır'}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── PIN ─────────────────────────────────────────────────
    elif action == "act_pin":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>mesaj ID</b> gir.", parse_mode=ParseMode.HTML)
            return
        try:
            await ctx.bot.pin_chat_message(GROUP_ID, int(text.strip()))
            await msg.reply_text("📌 Mesaj sabitlendi.")
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── DELETE ──────────────────────────────────────────────
    elif action == "act_delete":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>mesaj ID</b> gir.", parse_mode=ParseMode.HTML)
            return
        try:
            await ctx.bot.delete_message(GROUP_ID, int(text.strip()))
            stats["deleted_messages"] += 1
            save_data()
            await msg.reply_text("✅ Mesaj silindi.")
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── PURGE (N mesaj) ─────────────────────────────────────
    elif action == "act_purge_ask":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>sayı</b> gir.", parse_mode=ParseMode.HTML)
            return
        n = min(int(text.strip()), 200)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Evet, {n} mesajı sil!", callback_data=f"purge_confirm:{n}"),
            InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
        ]])
        await msg.reply_text(
            f"⚠️ <b>Onay Gerekiyor</b>\n\nGrubun son <b>{n} mesajını</b> silmek üzeresin.\n"
            f"Bu işlem <b>geri alınamaz!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    # ── PURGE AFTER ─────────────────────────────────────────
    elif action == "act_purge_after":
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>mesaj ID'si</b> gir.", parse_mode=ParseMode.HTML)
            return
        from_id = int(text.strip())
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Evet, {from_id}'den itibaren sil!", callback_data=f"purge_after_confirm:{from_id}"),
            InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
        ]])
        await msg.reply_text(
            f"⚠️ <b>Onay Gerekiyor</b>\n\nMesaj <code>{from_id}</code>'den en sona kadar silinecek.\n"
            f"Bu işlem <b>geri alınamaz!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    # ── BROADCAST ───────────────────────────────────────────
    elif action == "act_broadcast":
        try:
            await ctx.bot.send_message(
                GROUP_ID,
                f"📢 <b>DUYURU</b>\n━━━━━━━━━━━━━━━━\n{text}",
                parse_mode=ParseMode.HTML,
            )
            await msg.reply_text("✅ Duyuru başarıyla gruba gönderildi.", reply_markup=main_menu_kb())
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── POLL ────────────────────────────────────────────────
    elif action == "act_poll":
        parts = text.split("|")
        if len(parts) < 3:
            await msg.reply_text("❌ Format: <code>Soru|Seçenek1|Seçenek2</code>", parse_mode=ParseMode.HTML)
            return
        question = parts[0].strip()
        options  = [p.strip() for p in parts[1:] if p.strip()]
        try:
            await ctx.bot.send_poll(GROUP_ID, question, options, is_anonymous=False)
            await msg.reply_text(
                f"✅ <b>Anket Oluşturuldu!</b>\n\n❓ Soru: {question}\n📊 Seçenek: {len(options)}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── SETWELCOME ──────────────────────────────────────────
    elif action == "act_setwelcome":
        global welcome_msg
        welcome_msg = text
        save_data()
        await msg.reply_text(
            f"✅ <b>Karşılama Mesajı Güncellendi</b>\n\nYeni mesaj:\n<i>{welcome_msg}</i>",
            parse_mode=ParseMode.HTML,
        )

    # ── ZAMANLANAN DUYURU METNİ ─────────────────────────────
    elif action == "act_set_scheduled_text":
        global scheduled_msg_text
        scheduled_msg_text = text
        save_data()
        await msg.reply_text(
            f"✅ <b>Zamanlı Duyuru Metni Güncellendi</b>\n\n<i>{scheduled_msg_text[:300]}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏰ Duyuru Ayarlarına Dön", callback_data="menu_scheduled")]]),
        )

    # ── ZAMANLANAN DUYURU SAATİ ─────────────────────────────
    elif action == "act_set_scheduled_time":
        global scheduled_msg_hour, scheduled_msg_min
        m_obj = _re.match(r"^(\d{1,2}):(\d{2})$", text.strip())
        if not m_obj:
            await msg.reply_text("❌ Geçersiz format. Örnek: <code>09:00</code>", parse_mode=ParseMode.HTML)
            return
        h, mi = int(m_obj.group(1)), int(m_obj.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            await msg.reply_text("❌ Geçersiz saat.", parse_mode=ParseMode.HTML)
            return
        scheduled_msg_hour = h
        scheduled_msg_min  = mi
        save_data()
        _reschedule(ctx)
        await msg.reply_text(
            f"✅ <b>Zamanlı Duyuru Saati Güncellendi</b>\n\n"
            f"🕐 Yeni saat (UTC): <b>{h:02d}:{mi:02d}</b>\n"
            f"🇹🇷 Türkiye ≈ <b>{(h+3)%24:02d}:{mi:02d}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏰ Duyuru Ayarlarına Dön", callback_data="menu_scheduled")]]),
        )

    # ── SLOWMODE ────────────────────────────────────────────
    elif action == "act_slowmode":
        global slowmode_sec
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>saniye değeri</b> gir.", parse_mode=ParseMode.HTML)
            return
        slowmode_sec = int(text.strip())
        try:
            await ctx.bot.set_chat_slow_mode_delay(GROUP_ID, slowmode_sec)
            save_data()
            status = f"{slowmode_sec} saniye" if slowmode_sec else "Kapalı"
            await msg.reply_text(
                f"🐌 <b>Yavaş Mod Güncellendi</b>\n\nYeni değer: <b>{status}</b>",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── AUTODELETE ──────────────────────────────────────────
    elif action == "act_autodelete":
        global auto_delete_sec
        if not text.strip().isdigit():
            await msg.reply_text("❌ Geçerli bir <b>saniye değeri</b> gir.", parse_mode=ParseMode.HTML)
            return
        auto_delete_sec = int(text.strip())
        save_data()
        status = f"{auto_delete_sec} saniye sonra" if auto_delete_sec else "Kapalı"
        await msg.reply_text(
            f"⏱️ <b>Otomatik Silme Güncellendi</b>\n\nYeni değer: <b>{status}</b>",
            parse_mode=ParseMode.HTML,
        )

    # ── ADDBAN ──────────────────────────────────────────────
    elif action == "act_addban":
        word = text.strip().lower()
        if not word:
            await msg.reply_text("❌ Geçerli bir kelime gir.")
            return
        if word not in banned_words:
            banned_words.append(word)
            save_data()
            await msg.reply_text(
                f"✅ <b>Filtre Eklendi</b>\n\n🚫 <code>{word}</code> artık yasaklı.\n"
                f"📊 Toplam aktif filtre: {len(banned_words)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await msg.reply_text(f"ℹ️ <code>{word}</code> zaten listede.", parse_mode=ParseMode.HTML)

    # ── REMOVEBAN ───────────────────────────────────────────
    elif action == "act_removeban":
        word = text.strip().lower()
        if word in banned_words:
            banned_words.remove(word)
            save_data()
            await msg.reply_text(
                f"✅ <b>Filtre Kaldırıldı</b>\n\n<code>{word}</code> artık filtrelenmeyecek.\n"
                f"Kalan filtre sayısı: {len(banned_words)}",
                parse_mode=ParseMode.HTML,
            )
        else:
            await msg.reply_text(f"❌ <code>{word}</code> listede bulunamadı.", parse_mode=ParseMode.HTML)

    # ── SAVENOTE ────────────────────────────────────────────
    elif action == "act_savenote":
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await msg.reply_text("❌ Format: <code>notadı İçerik</code>", parse_mode=ParseMode.HTML)
            return
        name, content = parts[0].lower(), parts[1]
        notes[name] = content
        save_data()
        await msg.reply_text(
            f"✅ <b>Not Kaydedildi</b>\n\n📝 <code>#{name}</code>\n\n"
            f"💡 Grupta <code>#{name}</code> yazarak gösterebilirsin.",
            parse_mode=ParseMode.HTML,
        )

    # ── SENDNOTE ────────────────────────────────────────────
    elif action == "act_sendnote":
        name = text.strip().lower().lstrip("#")
        if name not in notes:
            await msg.reply_text(f"❌ <code>#{name}</code> bulunamadı.", parse_mode=ParseMode.HTML)
            return
        try:
            await ctx.bot.send_message(
                GROUP_ID,
                f"📝 <b>{name}</b>\n━━━━━━━━━━\n{notes[name]}",
                parse_mode=ParseMode.HTML,
            )
            await msg.reply_text(f"✅ <code>#{name}</code> notu gruba gönderildi.")
        except TelegramError as e:
            await msg.reply_text(f"❌ Hata: {e}")

    # ── DELETENOTE ──────────────────────────────────────────
    elif action == "act_deletenote":
        name = text.strip().lower().lstrip("#")
        if name in notes:
            del notes[name]
            save_data()
            await msg.reply_text(f"✅ <code>#{name}</code> notu silindi.", parse_mode=ParseMode.HTML)
        else:
            await msg.reply_text(f"❌ <code>#{name}</code> bulunamadı.", parse_mode=ParseMode.HTML)

# ──────────────────────────────────────────────────────────────
# KOMUT HANDLER'LARI
# ──────────────────────────────────────────────────────────────
async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /ban [id] [neden]"); return
    await _process_action(update, ctx, "act_ban", " ".join(ctx.args))

async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /unban [id]"); return
    await _process_action(update, ctx, "act_unban", ctx.args[0])

async def cmd_kick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /kick [id]"); return
    await _process_action(update, ctx, "act_kick", ctx.args[0])

async def cmd_mute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /mute [id] [dakika]"); return
    await _process_action(update, ctx, "act_mute", " ".join(ctx.args))

async def cmd_unmute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /unmute [id]"); return
    await _process_action(update, ctx, "act_unmute", ctx.args[0])

async def cmd_warn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /warn [id] [neden]"); return
    await _process_action(update, ctx, "act_warn", " ".join(ctx.args))

async def cmd_unwarn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /unwarn [id]"); return
    await _process_action(update, ctx, "act_unwarn", ctx.args[0])

async def cmd_warnings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /warnings [id]"); return
    await _process_action(update, ctx, "act_warnings", ctx.args[0])

async def cmd_promote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /promote [id]"); return
    await _process_action(update, ctx, "act_promote", ctx.args[0])

async def cmd_demote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /demote [id]"); return
    await _process_action(update, ctx, "act_demote", ctx.args[0])

async def cmd_pin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    reply = update.message.reply_to_message
    if reply:
        await _process_action(update, ctx, "act_pin", str(reply.message_id))
    elif ctx.args:
        await _process_action(update, ctx, "act_pin", ctx.args[0])
    else:
        await update.message.reply_text("Kullanım: Mesajı yanıtla → /pin  veya  /pin [mesaj_id]")

async def cmd_unpin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_unpin(update.message, ctx)

async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    reply = update.message.reply_to_message
    if reply:
        await _process_action(update, ctx, "act_delete", str(reply.message_id))
    elif ctx.args:
        await _process_action(update, ctx, "act_delete", ctx.args[0])
    else:
        await update.message.reply_text("Kullanım: Mesajı yanıtla → /delete  veya  /delete [id]")

async def cmd_purge(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Kullanım: /purge [n]"); return
    n       = min(int(ctx.args[0]), 200)
    chat_id = update.effective_chat.id
    try:
        sentinel = await ctx.bot.send_message(chat_id, "🧹")
        last_id  = sentinel.message_id
        await ctx.bot.delete_message(chat_id, last_id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Hata: {e}"); return
    deleted = await _bulk_delete(ctx, chat_id, last_id - 1, last_id - n)
    stats["deleted_messages"] += deleted
    save_data()
    m = await ctx.bot.send_message(chat_id, f"🗑️ {deleted} mesaj silindi.")
    asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 5))

async def cmd_purgefrom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    chat_id = update.effective_chat.id
    reply = update.message.reply_to_message
    if not reply:
        m = await update.message.reply_text(
            "ℹ️ Silmenin başlamasını istediğin mesajı <b>yanıtla</b> ve /purgefrom yaz.",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 5))
        asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 8))
        return
    from_id = reply.message_id
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Evet, sil!", callback_data=f"purge_after_confirm:{from_id}"),
        InlineKeyboardButton("❌ İptal",      callback_data="purgefrom_cancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ Mesaj <code>{from_id}</code>'den en sona kadar <b>tüm mesajlar</b> silinecek.\nGeri alınamaz!",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 1))

async def cmd_select(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    chat_id = update.effective_chat.id
    reply   = update.message.reply_to_message
    if not reply:
        m = await update.message.reply_text(
            "📌 Silmenin <b>başlayacağı</b> mesajı yanıtla → <code>/select</code>\n"
            "Silmenin <b>biteceği</b> mesajı yanıtla → <code>/selectend</code>",
            parse_mode=ParseMode.HTML,
        )
        asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 5))
        asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 10))
        return
    select_start[chat_id] = reply.message_id
    asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 3))
    m = await ctx.bot.send_message(
        chat_id,
        f"✅ Başlangıç noktası seçildi: <code>{reply.message_id}</code>\n"
        f"Bitiş mesajını yanıtlayıp <code>/selectend</code> yaz.",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 15))

async def cmd_selectend(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    chat_id = update.effective_chat.id
    reply   = update.message.reply_to_message
    asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 2))
    if chat_id not in select_start:
        m = await update.message.reply_text("❌ Önce <code>/select</code> ile başlangıç seçmelisin!", parse_mode=ParseMode.HTML)
        asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 8))
        return
    if not reply:
        m = await update.message.reply_text("❌ Bitiş noktası için bir mesajı yanıtlayıp <code>/selectend</code> yaz!", parse_mode=ParseMode.HTML)
        asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 8))
        return
    from_id  = select_start[chat_id]
    to_id    = reply.message_id
    start_id = min(from_id, to_id)
    end_id   = max(from_id, to_id)
    count    = end_id - start_id + 1
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Evet, {count} mesajı sil!", callback_data=f"select_confirm:{start_id}:{end_id}"),
        InlineKeyboardButton("❌ İptal",                      callback_data="select_cancel"),
    ]])
    await ctx.bot.send_message(
        chat_id,
        f"⚠️ <code>{start_id}</code> → <code>{end_id}</code> arasında <b>~{count} mesaj</b> silinecek.\n"
        f"Bu işlem <b>geri alınamaz!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    del select_start[chat_id]

async def cmd_selectcancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    chat_id = update.effective_chat.id
    asyncio.create_task(auto_delete(ctx, chat_id, update.message.message_id, 2))
    if chat_id in select_start:
        del select_start[chat_id]
        m = await update.message.reply_text("✅ Seçim iptal edildi.")
    else:
        m = await update.message.reply_text("ℹ️ Aktif seçim yok.")
    asyncio.create_task(auto_delete(ctx, chat_id, m.message_id, 5))

async def cmd_clearall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Evet, 100 mesajı sil!", callback_data="purge_confirm:100"),
        InlineKeyboardButton("❌ İptal", callback_data="menu_msgs"),
    ]])
    await update.message.reply_text(
        "⚠️ <b>Son 100 mesajı silmek istediğine emin misin?</b>\nBu işlem geri alınamaz!",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /broadcast [metin]"); return
    text = " ".join(ctx.args)
    await ctx.bot.send_message(GROUP_ID, f"📢 <b>DUYURU</b>\n━━━━━━━━━━━━━━━━\n{text}", parse_mode=ParseMode.HTML)
    await update.message.reply_text("✅ Duyuru gönderildi.")

async def cmd_poll(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /poll Soru|Seç1|Seç2"); return
    parts = " ".join(ctx.args).split("|")
    if len(parts) < 3: await update.message.reply_text("❌ En az 1 soru + 2 seçenek"); return
    await ctx.bot.send_poll(GROUP_ID, parts[0].strip(), [p.strip() for p in parts[1:] if p.strip()], is_anonymous=False)
    await update.message.reply_text("✅ Anket oluşturuldu.")

async def cmd_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_lock(update.message, ctx, lock=True)

async def cmd_unlock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_lock(update.message, ctx, lock=False)

async def cmd_slowmode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Kullanım: /slowmode [sn]"); return
    await _process_action(update, ctx, "act_slowmode", ctx.args[0])

async def cmd_setwelcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /setwelcome [metin]"); return
    await _process_action(update, ctx, "act_setwelcome", " ".join(ctx.args))

async def cmd_autodelete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Kullanım: /autodelete [sn]"); return
    await _process_action(update, ctx, "act_autodelete", ctx.args[0])

async def cmd_antiflood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global antiflood_on
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /antiflood [on/off]"); return
    antiflood_on = ctx.args[0].lower() == "on"
    save_data()
    await update.message.reply_text(f"🌊 Anti-flood: {'Aktif ✅' if antiflood_on else 'Pasif ❌'}")

async def cmd_addban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /addban [kelime]"); return
    await _process_action(update, ctx, "act_addban", " ".join(ctx.args))

async def cmd_removeban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /removeban [kelime]"); return
    await _process_action(update, ctx, "act_removeban", " ".join(ctx.args))

async def cmd_listban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not banned_words: await update.message.reply_text("📋 Liste boş."); return
    await update.message.reply_text(
        "📋 <b>Yasaklı Kelimeler</b>\n" + "\n".join(f"🚫 <code>{w}</code>" for w in banned_words),
        parse_mode=ParseMode.HTML,
    )

async def cmd_newlink(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    link = await ctx.bot.export_chat_invite_link(GROUP_ID)
    await update.message.reply_text(f"🔗 Yeni link:\n{link}")

async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("Kullanım: /note [ad]"); return
    name = ctx.args[0].lower()
    if name not in notes: await update.message.reply_text(f"❌ '{name}' bulunamadı."); return
    await update.message.reply_text(f"📝 <b>{name}</b>\n━━━━━━━━━━\n{notes[name]}", parse_mode=ParseMode.HTML)

async def cmd_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not notes: await update.message.reply_text("📋 Not yok."); return
    await update.message.reply_text("📋 Notlar:\n" + "\n".join(f"• #{k}" for k in notes.keys()))

async def cmd_savenote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args or len(ctx.args) < 2: await update.message.reply_text("Kullanım: /savenote [ad] [metin]"); return
    notes[ctx.args[0].lower()] = " ".join(ctx.args[1:])
    save_data()
    await update.message.reply_text("✅ Not kaydedildi.")

async def cmd_deletenote(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /deletenote [ad]"); return
    name = ctx.args[0].lower()
    if name in notes:
        del notes[name]
        save_data()
        await update.message.reply_text("✅ Silindi.")
    else:
        await update.message.reply_text("❌ Bulunamadı.")

async def cmd_groupinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_groupinfo(update.message, ctx)

async def cmd_membercount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    count = await ctx.bot.get_chat_member_count(GROUP_ID)
    await update.message.reply_text(f"👥 Üye sayısı: <b>{count}</b>", parse_mode=ParseMode.HTML)

async def cmd_topdavetci(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not invite_tracker:
        await update.message.reply_text("📭 Henüz davet verisi yok.")
        return
    sorted_inv = sorted(invite_tracker.items(), key=lambda x: x[1]["count"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, (iuid, idata) in enumerate(sorted_inv[:20]):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} <a href='tg://user?id={iuid}'>{idata['name']}</a> — <b>{idata['count']}</b> davet")
    await update.message.reply_text(
        f"🏆 <b>Davet Liderlik Tablosu</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await _exec_stats(update.message)

async def cmd_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not ctx.args: await update.message.reply_text("Kullanım: /info [id]"); return
    await _process_action(update, ctx, "act_info", ctx.args[0])

async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    c = update.effective_chat
    await update.message.reply_text(
        f"👤 Senin ID: <code>{u.id}</code>\n💬 Chat ID: <code>{c.id}</code>",
        parse_mode=ParseMode.HTML,
    )

# ──────────────────────────────────────────────────────────────
# YENİ ÜYE KARŞILAMA
# ──────────────────────────────────────────────────────────────
async def handle_new_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot: continue
        text = welcome_msg.format(
            name=member.full_name, id=member.id, group=update.effective_chat.title,
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📢 KriptoDrop TR DUYURU KANALI", url="https://t.me/kriptodropduyuru"),
                InlineKeyboardButton("📋 Kurallar", url="https://t.me/kriptodropduyuru/46"),
            ],
            [InlineKeyboardButton("❓ Sıkça Sorulan Sorular (SSS)", url="https://t.me/kriptodropduyuru/47")],
        ])
        m = await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        if auto_delete_sec > 0:
            asyncio.create_task(auto_delete(ctx, update.effective_chat.id, m.message_id, auto_delete_sec))
        await notify_admin(ctx, f"👤 Yeni üye: {fmt(member)} (ID: <code>{member.id}</code>) gruba katıldı.")

# ──────────────────────────────────────────────────────────────
# MESAJ FİLTRESİ
# ──────────────────────────────────────────────────────────────
async def filter_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg: return
    stats["total_messages"] += 1
    user = msg.from_user
    if not user or is_admin(user.id): return

    text       = msg.text or msg.caption or ""
    text_lower = text.lower()

    # #notadı kısayolu
    if text.startswith("#"):
        note_name = text[1:].strip().lower().split()[0]
        if note_name in notes:
            await msg.reply_text(
                f"📝 <b>{note_name}</b>\n━━━━━━━━━━\n{notes[note_name]}",
                parse_mode=ParseMode.HTML,
            )
        return

    # Yasaklı kelime filtresi
    for word in banned_words:
        if word in text_lower:
            try:
                await msg.delete()
                stats["deleted_messages"] += 1
                m = await ctx.bot.send_message(
                    msg.chat_id,
                    f"⚠️ {fmt(user)}, mesajın yasaklı içerik barındırdığı için silindi.",
                    parse_mode=ParseMode.HTML,
                )
                asyncio.create_task(auto_delete(ctx, msg.chat_id, m.message_id, 5))
                await notify_admin(ctx, f"🚫 Yasaklı kelime!\n👤 {fmt(user)}\n🔤 Kelime: <code>{word}</code>")
            except TelegramError:
                pass
            return

    # Anti-flood
    if antiflood_on:
        now = utcnow()
        uid = user.id
        antiflood_buf.setdefault(uid, [])
        antiflood_buf[uid] = [t for t in antiflood_buf[uid] if (now - t).total_seconds() < 10]
        antiflood_buf[uid].append(now)
        if len(antiflood_buf[uid]) > 5:
            try:
                await ctx.bot.restrict_chat_member(
                    msg.chat_id, uid,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=utcnow() + timedelta(minutes=5),
                )
                m = await ctx.bot.send_message(
                    msg.chat_id,
                    f"🌊 {fmt(user)} çok hızlı mesaj gönderdiği için <b>5 dakika susturuldu</b>.",
                    parse_mode=ParseMode.HTML,
                )
                asyncio.create_task(auto_delete(ctx, msg.chat_id, m.message_id, 10))
                await notify_admin(ctx, f"🌊 Flood koruması!\n👤 {fmt(user)} (ID: <code>{uid}</code>) 5dk susturuldu.")
                antiflood_buf[uid] = []
            except TelegramError:
                pass

    # Otomatik silme
    if auto_delete_sec > 0:
        asyncio.create_task(auto_delete(ctx, msg.chat_id, msg.message_id, auto_delete_sec))

# ──────────────────────────────────────────────────────────────
# ZAMANLANAN DUYURU
# ──────────────────────────────────────────────────────────────
_scheduler: AsyncIOScheduler | None = None

async def _send_scheduled_msg(ctx):
    if not scheduled_msg_on:
        return
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 KriptoDrop TR DUYURU KANALI", url="https://t.me/kriptodropduyuru"),
            InlineKeyboardButton("📋 Kurallar", url="https://t.me/kriptodropduyuru/46"),
        ],
        [InlineKeyboardButton("❓ Sıkça Sorulan Sorular (SSS)", url="https://t.me/kriptodropduyuru/47")],
    ])
    try:
        await ctx.bot.send_message(
            GROUP_ID, scheduled_msg_text,
            parse_mode=ParseMode.HTML, reply_markup=kb,
        )
        logger.info("✅ Zamanlı duyuru gönderildi.")
    except TelegramError as e:
        logger.error(f"Zamanlı duyuru hatası: {e}")

def _reschedule(ctx):
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.reschedule_job(
            "daily_msg",
            trigger=CronTrigger(hour=scheduled_msg_hour, minute=scheduled_msg_min),
        )
        logger.info(f"⏰ Zamanlı duyuru saati güncellendi: {scheduled_msg_hour:02d}:{scheduled_msg_min:02d} UTC")
    except Exception as e:
        logger.error(f"Reschedule hatası: {e}")

# ──────────────────────────────────────────────────────────────
# DAVET TAKİBİ
# ──────────────────────────────────────────────────────────────
from telegram.ext import ChatMemberHandler

async def handle_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result or result.chat.id != GROUP_ID:
        return
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    joined = (
        old_status in ("left", "kicked")
        and new_status in ("member", "administrator", "creator")
    )
    if not joined:
        return
    inviter     = result.from_user
    joined_user = result.new_chat_member.user
    if inviter and inviter.id != joined_user.id and not inviter.is_bot:
        iid = inviter.id
        if iid not in invite_tracker:
            invite_tracker[iid] = {"name": inviter.full_name, "count": 0}
        invite_tracker[iid]["count"] += 1
        invite_tracker[iid]["name"]   = inviter.full_name
        save_data()
        logger.info(f"📨 {inviter.full_name} davet etti → toplam {invite_tracker[iid]['count']}")

# ──────────────────────────────────────────────────────────────
# HATA HANDLER — admin'e bildirim gönderir
# ──────────────────────────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Hata: {ctx.error}", exc_info=ctx.error)
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            f"⚠️ <b>Bot Hatası</b>\n\n<code>{ctx.error}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────
# POST INIT
# ──────────────────────────────────────────────────────────────
async def post_init(app: Application):
    global _scheduler
    dm_cmds = [
        BotCommand("start",       "🤖 Yönetim Panelini Aç"),
        BotCommand("help",        "📋 Tüm Komutları Listele"),
        BotCommand("groupinfo",   "🏘️ Grup Bilgilerini Gör"),
        BotCommand("membercount", "👥 Üye Sayısını Gör"),
        BotCommand("stats",       "📈 Bot İstatistiklerini Gör"),
        BotCommand("notes",       "📝 Kayıtlı Notları Listele"),
        BotCommand("broadcast",   "📣 Gruba Duyuru Gönder"),
        BotCommand("topdavetci",  "🏆 Davet Liderlik Tablosu"),
    ]
    group_cmds = [
        BotCommand("topdavetci", "🏆 Davet Sıralaması"),
    ]
    await app.bot.set_my_commands(dm_cmds,    scope=BotCommandScopeAllPrivateChats())
    await app.bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
    logger.info("✅ Komut listeleri Telegram'a kaydedildi.")

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        lambda: asyncio.create_task(_send_scheduled_msg(app)),   # FIX: ensure_future → create_task
        trigger=CronTrigger(hour=scheduled_msg_hour, minute=scheduled_msg_min),
        id="daily_msg",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"⏰ Zamanlı duyuru aktif: her gün {scheduled_msg_hour:02d}:{scheduled_msg_min:02d} UTC")

# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("help",  cmd_help))

    for name, fn in [
        ("ban", cmd_ban), ("unban", cmd_unban), ("kick", cmd_kick),
        ("mute", cmd_mute), ("unmute", cmd_unmute),
        ("warn", cmd_warn), ("unwarn", cmd_unwarn), ("warnings", cmd_warnings),
        ("promote", cmd_promote), ("demote", cmd_demote),
        ("pin", cmd_pin), ("unpin", cmd_unpin),
        ("delete", cmd_delete), ("purge", cmd_purge),
        ("purgefrom", cmd_purgefrom), ("clearall", cmd_clearall),
        ("select", cmd_select), ("selectend", cmd_selectend), ("selectcancel", cmd_selectcancel),
        ("broadcast", cmd_broadcast), ("poll", cmd_poll),
        ("lock", cmd_lock), ("unlock", cmd_unlock),
        ("slowmode", cmd_slowmode), ("setwelcome", cmd_setwelcome),
        ("autodelete", cmd_autodelete), ("antiflood", cmd_antiflood),
        ("addban", cmd_addban), ("removeban", cmd_removeban), ("listban", cmd_listban),
        ("newlink", cmd_newlink),
        ("note", cmd_note), ("notes", cmd_notes),
        ("savenote", cmd_savenote), ("deletenote", cmd_deletenote),
        ("info", cmd_info), ("groupinfo", cmd_groupinfo),
        ("membercount", cmd_membercount), ("stats", cmd_stats), ("id", cmd_id),
        ("topdavetci", cmd_topdavetci),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND & (filters.TEXT | filters.FORWARDED),
        handle_dm,
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION),
        filter_messages,
    ))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        logger.info(f"🚀 Bot webhook ile başlatılıyor: {WEBHOOK_URL}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("🚀 Bot polling ile başlatılıyor...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
