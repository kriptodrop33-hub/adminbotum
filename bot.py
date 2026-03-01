import os
import re
import asyncio
import logging
import aiohttp
import json as _json
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand,
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler,
    CommandHandler, CallbackQueryHandler, filters,
)

TOKEN      = os.getenv("BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_IDS  = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())
GROUP_ID   = int(os.getenv("GROUP_ID", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

airdrops: list       = []
airdrop_counter: int = 0
daily_users:    set = set()
weekly_users:   set = set()
monthly_users:  set = set()
all_time_users: set = set()
join_log: list = []
today       = datetime.now().date()
week_start  = today - timedelta(days=today.weekday())
month_start = today.replace(day=1)
posted_news: set = set()

haber_ayarlari: dict = {
    "aktif"        : True,
    "interval_saat": 6,
    "son_dk_aktif" : True,
    "son_dk_esik"  : 20,
    "kanal_tag"    : "@KriptoDropTR",
    "ozet_stili"   : "standart",
}

bekleyen_haberler: dict = {}

OZET_STILLERI = {
    "standart": {
        "isim"  : "📝 Standart",
        "prompt": "Haberi Türkçe olarak 3-4 cümleyle özetle. Açık, akıcı ve bilgilendirici bir dil kullan. Teknik terimleri sadeleştir.",
    },
    "detayli": {
        "isim"  : "📄 Detaylı",
        "prompt": "Haberi Türkçe olarak 5-7 cümleyle detaylı özetle. Arka planı, nedenleri ve olası sonuçlarını da açıkla. Kripto piyasasına etkisini belirt.",
    },
    "kisaca": {
        "isim"  : "⚡ Kısaca",
        "prompt": "Haberi Türkçe olarak maksimum 2 kısa cümleyle özetle. Sadece en önemli bilgiyi ver. Çok kısa ve net ol.",
    },
    "bullet": {
        "isim"  : "📌 Madde Madde",
        "prompt": "Haberi Türkçe olarak 3-4 madde halinde özetle. Her madde tek cümle olsun. Yanıtta maddeleri '• ' ile başlat.",
    },
}

HABER_KAYNAKLARI = [
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://tr.cointelegraph.com/rss",         "isim": "CoinTelegraph TR"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://www.btchaber.com/feed/",           "isim": "BTCHaber"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://kriptokoin.com/feed/",             "isim": "KriptoKoin"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://cointurk.com/feed",               "isim": "CoinTurk"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://kriptopara.com/feed/",            "isim": "KriptoPara"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://coin-turk.com/feed",              "isim": "Coin-Turk"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://www.kriptom.com/feed/",           "isim": "Kriptom"},
    {"url": "https://api.rss2json.com/v1/api.json?rss_url=https://kripto.com.tr/feed/",             "isim": "Kripto.com.tr"},
]

def is_admin(uid): return uid in ADMIN_IDS

def reset_periods():
    global today, week_start, month_start, daily_users, weekly_users, monthly_users
    nd = datetime.now().date()
    if nd != today: daily_users.clear(); today = nd
    nw = nd - timedelta(days=nd.weekday())
    if nw != week_start: weekly_users.clear(); week_start = nw
    nm = nd.replace(day=1)
    if nm != month_start: monthly_users.clear(); month_start = nm

def register_user(user):
    reset_periods(); uid = user.id
    daily_users.add(uid); weekly_users.add(uid); monthly_users.add(uid); all_time_users.add(uid)
    join_log.append({"user_id": uid, "date": datetime.now().date(), "name": user.full_name})

def get_active_airdrops():
    now = datetime.now().date(); result = []
    for a in airdrops:
        if a["durum"] != "aktif": continue
        try:
            if datetime.strptime(a["bitis"], "%d.%m.%Y").date() < now: a["durum"] = "bitti"; continue
        except: pass
        result.append(a)
    return result

def get_airdrop_by_id(aid): return next((a for a in airdrops if a["id"] == aid), None)

def _bitis_gun(a):
    try: return datetime.strptime(a["bitis"], "%d.%m.%Y").date()
    except: return None

def puan_yildiz(p):
    t = int(p); y = 1 if (p-t) >= 0.5 else 0; b = 10-t-y
    return "⭐"*t + ("✨" if y else "") + "☆"*b

def puan_renk(p): return "🟢" if p >= 8 else "🟡" if p >= 5 else "🔴"

def kalan_gun(a):
    bg = _bitis_gun(a)
    if not bg: return ""
    k = (bg - datetime.now().date()).days
    if k < 0: return " *(Süresi doldu)*"
    if k == 0: return " ⚠️ *Bugün bitiyor!*"
    if k <= 3: return f" ⚡ *{k} gün kaldı!*"
    return f" ({k} gün)"

def airdrop_card(a, detay=False):
    d = "✅" if a["durum"] == "aktif" else "❌"
    p = a.get("puan", 0)
    s = [
        f"{d} *#{a['id']} — {a['baslik']}*",
        f"{puan_renk(p)} Puan: `{p}/10`  {puan_yildiz(p)}",
        f"💰 Ödül: {a['odül']}",
        f"📅 Başlangıç: {a.get('baslangic', '—')}",
        f"⏳ Bitiş: {a['bitis']}{kalan_gun(a)}",
        f"🔗 [Katıl]({a['link']})",
    ]
    return "\n".join(s)

def _parse_rss_date(s):
    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"]:
        try: return datetime.strptime(s, fmt).replace(tzinfo=None)
        except: pass
    return datetime.utcnow()

def _temizle(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

async def fetch_tek_kaynak(sess, kaynak):
    try:
        async with sess.get(kaynak["url"], timeout=aiohttp.ClientTimeout(total=12)) as r:
            data = await r.json(content_type=None)
        out = []
        for h in data.get("items", [])[:12]:
            link = h.get("link", ""); guid = h.get("guid", link)
            baslik = _temizle(h.get("title", ""))
            icerik = _temizle(h.get("description", h.get("content", "")))[:1500]
            if not baslik or not link: continue
            out.append({"id": guid, "baslik": baslik, "icerik": icerik, "url": link,
                        "kaynak": kaynak["isim"], "zaman": _parse_rss_date(h.get("pubDate", ""))})
        return out
    except Exception as e:
        log.warning(f"Kaynak {kaynak['isim']}: {e}"); return []

async def fetch_crypto_news(limit=30):
    async with aiohttp.ClientSession() as sess:
        sonuclar = await asyncio.gather(*[fetch_tek_kaynak(sess, k) for k in HABER_KAYNAKLARI])
    tum, seen = [], set()
    for liste in sonuclar:
        for h in liste:
            if h["url"] not in seen: seen.add(h["url"]); tum.append(h)
    tum.sort(key=lambda x: x["zaman"], reverse=True)
    return tum[:limit]

def _yeni(liste): return [h for h in liste if h["id"] not in posted_news and h["url"] not in posted_news]

async def openai_ozet(metin, baslik="", stil="standart"):
    if not OPENAI_KEY:
        return {"baslik": baslik, "ozet": metin[:300]+"...", "son_dk": False, "etiketler": []}
    stil_p = OZET_STILLERI.get(stil, OZET_STILLERI["standart"])["prompt"]
    prompt = (
        f"Sana Türkçe bir kripto para haberi veriyorum.\n\n"
        f"1. Başlık: Kısa ve çarpıcı Türkçe başlık yaz.\n"
        f"2. Özet: {stil_p}\n"
        f"3. Son Dakika: Acil/kritik mi? (büyük hack/iflas, ülke kararı, SEC kararı, BTC/ETH %5+ ani hareket) true/false\n"
        f"4. Etiketler: max 3 kripto etiketi (#BTC gibi)\n\n"
        f"YALNIZCA JSON: {{\"baslik\":\"...\",\"ozet\":\"...\",\"son_dk\":false,\"etiketler\":[\"#BTC\"]}}\n\n"
        f"BAŞLIK: {baslik}\nİÇERİK: {metin[:1800]}"
    )
    payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 600, "temperature": 0.4, "response_format": {"type": "json_object"}}
    try:
        headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as sess:
            async with sess.post("https://api.openai.com/v1/chat/completions",
                                 json=payload, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json()
                res = _json.loads(data["choices"][0]["message"]["content"])
                return {"baslik": res.get("baslik", baslik), "ozet": res.get("ozet", metin[:300]),
                        "son_dk": bool(res.get("son_dk", False)), "etiketler": res.get("etiketler", [])}
    except Exception as e:
        log.warning(f"OpenAI: {e}")
        return {"baslik": baslik, "ozet": metin[:300]+"...", "son_dk": False, "etiketler": []}

def haber_mesaj_formatla(h, ai, son_dk=False):
    et  = " ".join(ai.get("etiketler", []))
    hdr = "🚨 *SON DAKİKA* 🚨" if son_dk else "📰 *Kripto Haber*"
    zm  = h["zaman"].strftime("%d.%m.%Y %H:%M") if h.get("zaman") else ""
    t   = f"{hdr}\n━━━━━━━━━━━━━━━━━━━━\n📌 *{ai['baslik']}*\n\n📝 {ai['ozet']}\n\n"
    t  += f"🇹🇷 {h['kaynak']}  🕐 {zm}\n🔗 [Haberin tamamı]({h['url']})"
    if et: t += f"\n\n{et}"
    t += f"\n\n━━━━━━━━━━━━━━\n🤖 {haber_ayarlari['kanal_tag']}"
    return t

# ── Klavyeler ──
def ana_menu_kb(adm):
    rows = [
        [InlineKeyboardButton("🎁 Airdroplar",  callback_data="menu_airdrops"),
         InlineKeyboardButton("🏆 En İyiler",   callback_data="menu_topairdrops")],
        [InlineKeyboardButton("🔍 Filtrele",    callback_data="menu_filtre"),
         InlineKeyboardButton("📰 Haberler",    callback_data="menu_haberler")],
        [InlineKeyboardButton("📊 İstatistik",  callback_data="menu_istatistik"),
         InlineKeyboardButton("❓ Yardım",       callback_data="menu_yardim")],
    ]
    if adm: rows.append([InlineKeyboardButton("⚙️ Admin Paneli", callback_data="adm_ana")])
    return InlineKeyboardMarkup(rows)

def filtre_kb(geri="menu_ana"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Puan ≥ 8",         callback_data="filtre_8"),
         InlineKeyboardButton("🟡 Puan ≥ 5",         callback_data="filtre_5"),
         InlineKeyboardButton("📋 Tümü",             callback_data="filtre_0")],
        [InlineKeyboardButton("⏰ Bugün Bitiyor",    callback_data="filtre_bugun"),
         InlineKeyboardButton("📅 Bu Hafta Bitiyor", callback_data="filtre_hafta")],
        [InlineKeyboardButton("🔙 Geri",             callback_data=geri)],
    ])

def adm_ana_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Airdrop Yönetimi", callback_data="adm_airdrop"),
         InlineKeyboardButton("📰 Haber Yönetimi",   callback_data="adm_haber")],
        [InlineKeyboardButton("⚙️ Haber Ayarları",   callback_data="adm_haber_ayar"),
         InlineKeyboardButton("📊 İstatistikler",    callback_data="adm_istat")],
        [InlineKeyboardButton("📣 Duyuru Gönder",    callback_data="adm_duyuru_info"),
         InlineKeyboardButton("👥 Üye Raporu",       callback_data="adm_uye_rapor")],
        [InlineKeyboardButton("🔙 Ana Menü",         callback_data="menu_ana")],
    ])

def adm_airdrop_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Nasıl Eklerim?",     callback_data="adm_air_ekle_info"),
         InlineKeyboardButton("📋 Tüm Airdroplar",    callback_data="adm_air_tumu")],
        [InlineKeyboardButton("✅ Sadece Aktif",       callback_data="adm_air_aktif"),
         InlineKeyboardButton("❌ Biten Airdroplar",  callback_data="adm_air_bitti")],
        [InlineKeyboardButton("🏆 Puana Göre Sırala", callback_data="adm_air_puan"),
         InlineKeyboardButton("📅 Tarihe Göre Sırala",callback_data="adm_air_tarih")],
        [InlineKeyboardButton("🔙 Admin Paneli",       callback_data="adm_ana")],
    ])

def adm_haber_kb():
    oto = "✅ Oto Haber Açık" if haber_ayarlari["aktif"] else "❌ Oto Haber Kapalı"
    sdk = "✅ Son Dk Açık"    if haber_ayarlari["son_dk_aktif"] else "❌ Son Dk Kapalı"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Önizle & Paylaş",      callback_data="adm_haber_onizle")],
        [InlineKeyboardButton(oto,                        callback_data="adm_haber_toggle")],
        [InlineKeyboardButton(sdk,                        callback_data="adm_sondk_toggle")],
        [InlineKeyboardButton("📊 Durum & Kaynaklar",    callback_data="adm_haber_durum"),
         InlineKeyboardButton("🗑 Geçmişi Temizle",      callback_data="adm_haber_temizle")],
        [InlineKeyboardButton("🔙 Admin Paneli",          callback_data="adm_ana")],
    ])

def adm_haber_ayar_kb():
    st = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"), {}).get("isim","")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📝 Özet Stili: {st}", callback_data="adm_stil_menu")],
        [InlineKeyboardButton("⏱ 1sa",  callback_data="adm_sure_1"),
         InlineKeyboardButton("⏱ 3sa",  callback_data="adm_sure_3"),
         InlineKeyboardButton("⏱ 6sa",  callback_data="adm_sure_6"),
         InlineKeyboardButton("⏱ 12sa", callback_data="adm_sure_12"),
         InlineKeyboardButton("⏱ 24sa", callback_data="adm_sure_24")],
        [InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")],
    ])

def adm_stil_kb():
    mev = haber_ayarlari.get("ozet_stili","standart")
    rows = [[InlineKeyboardButton(("✅ " if k==mev else "")+v["isim"], callback_data=f"adm_stil_{k}")]
            for k,v in OZET_STILLERI.items()]
    rows.append([InlineKeyboardButton("🔙 Geri", callback_data="adm_haber_ayar")])
    return InlineKeyboardMarkup(rows)

def onizleme_kb(idx, toplam):
    rows = [[InlineKeyboardButton("✅ Gruba Gönder",   callback_data="hab_onayla"),
             InlineKeyboardButton("⏭ Sonrakine Geç",  callback_data="hab_sonraki")]]
    rows.append([InlineKeyboardButton("🔙 İptal",      callback_data="adm_haber")])
    return InlineKeyboardMarkup(rows)

def get_welcome_message():
    text = ("🎉 *KriptoDropTR 🎁 Kanalımıza Hoş Geldiniz!* 🎉\n\n"
            "🚀 Güncel *Airdrop* fırsatlarından haberdar olmak için\n"
            "📢 *KriptoDropTR DUYURU 🔊* kanalımıza katılmayı\n"
            "🔔 Kanal bildirimlerini açmayı unutmayın!\n\n💎 Bol kazançlar dileriz!")
    kb = [[InlineKeyboardButton("📢 KriptoDropTR DUYURU 🔊", url="https://t.me/kriptodropduyuru")],
          [InlineKeyboardButton("📜 Kurallar", url="https://t.me/kriptodropduyuru/46")],
          [InlineKeyboardButton("❓ SSS",      url="https://t.me/kriptodropduyuru/47")]]
    return text, InlineKeyboardMarkup(kb)

# ── Handlers ──
async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for m in update.message.new_chat_members:
        if m.is_bot: continue
        register_user(m)
        t, kb = get_welcome_message()
        await update.message.reply_text(t, reply_markup=kb, parse_mode="Markdown")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; adm = is_admin(user.id)
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            f"👋 Merhaba *{user.first_name}*!\n\n🤖 *KriptoDropTR Bot*'a hoş geldin:",
            parse_mode="Markdown", reply_markup=ana_menu_kb(adm))
    else:
        await update.message.reply_text(
            "👋 Merhaba! Airdroplar ve haberler için bana *DM* yaz 👉 @KriptoDropTR_bot",
            parse_mode="Markdown")

async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    adm = is_admin(update.effective_user.id)
    t = ("📖 *KriptoDropTR Bot — Komutlar*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
         "🌐 *Genel:*\n/start — Ana menü\n/airdrops — Aktif airdroplar\n"
         "/topairdrops — Puan ≥ 8 olanlar\n/airdrop `<id>` — Detay\n"
         "/haberler — Son Türkçe kripto haberleri\n/istatistik — İstatistikler\n")
    if adm:
        t += ("\n🔧 *Admin (DM):*\n"
              "/airdropekle `Başlık | Ödül | Baş | Bitiş | Puan | Link`\n"
              "/airdropduzenle `<id> | alan | değer`\n"
              "/airdropbitir `<id>` — Sonlandır\n/airdropsil `<id>` — Sil\n"
              "/haberler_paylas — Önizleme ile paylaş\n/haberayar — Haber ayarları\n"
              "/duyuru `<metin>` — Gruba duyuru\n")
    await update.message.reply_text(t, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_ana")]]))

async def cmd_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aktif = get_active_airdrops()
    if not aktif:
        await update.message.reply_text("🎁 Şu an aktif airdrop yok.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]])); return
    t = "🎁 *Aktif Airdrop Listesi*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    t += "\n\n".join(airdrop_card(a) for a in aktif)
    t += f"\n\n📌 {len(aktif)} aktif airdrop"
    await update.message.reply_text(t, parse_mode="Markdown",
        disable_web_page_preview=True, reply_markup=filtre_kb())

async def cmd_top_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mp = 8.0
    if context.args:
        try: mp = float(context.args[0])
        except: pass
    liste = sorted([a for a in get_active_airdrops() if a.get("puan",0) >= mp],
                   key=lambda x: x.get("puan",0), reverse=True)
    if not liste:
        await update.message.reply_text(f"😕 Puan ≥ {mp} olan aktif airdrop yok."); return
    t = f"🏆 *En İyi Airdroplar (Puan ≥ {mp})*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    t += "\n\n".join(airdrop_card(a) for a in liste)
    await update.message.reply_text(t, parse_mode="Markdown",
        disable_web_page_preview=True, reply_markup=filtre_kb())

async def cmd_airdrop_detay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: `/airdrop <id>`", parse_mode="Markdown"); return
    a = get_airdrop_by_id(int(context.args[0]))
    if not a: await update.message.reply_text("❌ Bulunamadı."); return
    await update.message.reply_text(airdrop_card(a), parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Katıl", url=a["link"]),
                                            InlineKeyboardButton("🔙 Liste", callback_data="menu_airdrops")]]))

async def cmd_haberler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📰 Türkçe haberler alınıyor... ⏳")
    haberler = await fetch_crypto_news()
    if not haberler:
        await msg.edit_text("❌ Haberler alınamadı. Kaynaklar geçici olarak erişilemiyor olabilir."); return
    await msg.delete()
    stil = haber_ayarlari.get("ozet_stili","standart")
    for h in haberler[:3]:
        ai = await openai_ozet(h["icerik"], h["baslik"], stil)
        await update.message.reply_text(haber_mesaj_formatla(h, ai, ai.get("son_dk",False)),
            parse_mode="Markdown", disable_web_page_preview=True)
        await asyncio.sleep(1)

async def cmd_istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_periods(); td = datetime.now().date(); gd = {}
    for e in join_log:
        if (td-e["date"]).days < 7:
            l = e["date"].strftime("%d.%m"); gd[l] = gd.get(l,0)+1
    ds = "".join(f"  {g}: {'█'*min(s,15)} {s}\n" for g,s in sorted(gd.items()))
    t = (f"📊 *KriptoDropTR — İstatistikler*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
         f"📅 Bugün: {len(daily_users)}  📆 Bu Hafta: {len(weekly_users)}\n"
         f"🗓 Bu Ay: {len(monthly_users)}  🏆 Toplam: {len(all_time_users)}\n\n"
         f"🎁 Aktif: {len(get_active_airdrops())}  |  Toplam: {len(airdrops)}\n"
         f"📰 Paylaşılan Haber: {len(posted_news)}\n\n"
         f"📈 *Son 7 Gün:*\n{ds if ds else '  Veri yok.'}")
    await update.message.reply_text(t, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

async def cmd_airdrop_ekle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    if update.effective_chat.type != "private": await update.message.reply_text("⛔ DM'den kullan."); return
    global airdrop_counter
    ornek = ("📝 *Kullanım:*\n`/airdropekle Başlık | Ödül | Başlangıç | Bitiş | Puan | Link`\n\n"
             "📌 Tarih: `GG.AA.YYYY`  |  Puan: `0–10`\n\n*Örnek:*\n"
             "`/airdropekle Layer3 | 50 USDT | 01.01.2025 | 31.03.2025 | 9 | https://layer3.xyz`")
    if not context.args: await update.message.reply_text(ornek, parse_mode="Markdown"); return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) < 6: await update.message.reply_text("❌ 6 alan gerekli.\n\n"+ornek, parse_mode="Markdown"); return
    try: puan = float(parts[4]); assert 0 <= puan <= 10
    except: await update.message.reply_text("❌ Puan 0–10 olmalı."); return
    airdrop_counter += 1
    yeni = {"id": airdrop_counter, "baslik": parts[0], "odül": parts[1],
            "baslangic": parts[2], "bitis": parts[3], "puan": puan,
            "link": parts[5], "durum": "aktif", "eklendi": datetime.now()}
    airdrops.append(yeni)
    await update.message.reply_text(f"✅ *Airdrop Eklendi!*\n\n{airdrop_card(yeni)}",
        parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_airdrop_duzenle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    if not context.args:
        await update.message.reply_text("Kullanım: `/airdropduzenle <id> | alan | değer`\nAlanlar: `baslik odül baslangic bitis puan link durum`", parse_mode="Markdown"); return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) < 3 or not parts[0].isdigit(): await update.message.reply_text("❌ Format hatası."); return
    a = get_airdrop_by_id(int(parts[0]))
    if not a: await update.message.reply_text("❌ Bulunamadı."); return
    alan, deger = parts[1].lower(), parts[2]
    if alan == "puan":
        try: deger = float(deger)
        except: await update.message.reply_text("❌ Puan sayı olmalı."); return
    if alan not in a: await update.message.reply_text(f"❌ Geçersiz alan: `{alan}`", parse_mode="Markdown"); return
    a[alan] = deger
    await update.message.reply_text(f"✅ Güncellendi!\n\n{airdrop_card(a)}", parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_airdrop_bitir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    if not context.args or not context.args[0].isdigit(): await update.message.reply_text("Kullanım: `/airdropbitir <id>`", parse_mode="Markdown"); return
    a = get_airdrop_by_id(int(context.args[0]))
    if not a: await update.message.reply_text("❌ Bulunamadı."); return
    a["durum"] = "bitti"
    await update.message.reply_text(f"❌ *#{a['id']} — {a['baslik']}* sonlandırıldı.", parse_mode="Markdown")

async def cmd_airdrop_sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    if not context.args or not context.args[0].isdigit(): await update.message.reply_text("Kullanım: `/airdropsil <id>`", parse_mode="Markdown"); return
    a = get_airdrop_by_id(int(context.args[0]))
    if not a: await update.message.reply_text("❌ Bulunamadı."); return
    airdrops.remove(a)
    await update.message.reply_text(f"🗑 *#{a['id']} — {a['baslik']}* silindi.", parse_mode="Markdown")

async def _onizle_gonder(uid, send_func, context, idx=0):
    haberler = await fetch_crypto_news()
    yeni = _yeni(haberler)
    if not yeni:
        n = len(haberler)
        await send_func(
            f"ℹ️ *Yeni haber yok.*\n\n{n} haber çekildi, tamamı daha önce paylaşılmış.\n\n"
            "💡 Admin panelinden 'Geçmişi Temizle' ile sıfırlayabilirsin.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_haber")]])); return
    if idx >= len(yeni): idx = 0
    h = yeni[idx]; stil = haber_ayarlari.get("ozet_stili","standart")
    ai = await openai_ozet(h["icerik"], h["baslik"], stil)
    text = haber_mesaj_formatla(h, ai, ai.get("son_dk",False))
    bekleyen_haberler[uid] = {"text": text, "haber_id": h["id"], "haber_url": h["url"],
                               "index": idx, "toplam": len(yeni)}
    oniz = (f"👁 *HABER ÖNİZLEME* ({idx+1}/{len(yeni)})\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Stil: {OZET_STILLERI.get(stil,{}).get('isim','')}  |  Kaynak: {h['kaynak']}\n\n" + text)
    await send_func(oniz, parse_mode="Markdown", disable_web_page_preview=True,
                    reply_markup=onizleme_kb(idx, len(yeni)))

async def cmd_haber_paylas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    if update.effective_chat.type != "private": await update.message.reply_text("⛔ DM'den kullan."); return
    if GROUP_ID == 0: await update.message.reply_text("❌ GROUP_ID ayarlanmamış."); return
    msg = await update.message.reply_text("📰 Haberler alınıyor... ⏳")
    await msg.delete()
    await _onizle_gonder(update.effective_user.id, update.message.reply_text, context, idx=0)

async def cmd_haber_ayar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    args = context.args
    if args and args[0].lower() == "temizle":
        n = len(posted_news); posted_news.clear()
        await update.message.reply_text(f"✅ Haber geçmişi temizlendi ({n} kayıt silindi)."); return
    oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
    sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
    stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
    t = (f"⚙️ *Haber Sistemi Ayarları*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
         f"📰 Otomatik Haber: {oto}\n⏱ Sıklık: Her {haber_ayarlari['interval_saat']} saatte bir\n"
         f"🚨 Son Dakika: {sdk}\n📝 Özet Stili: {stil}\n"
         f"📊 Paylaşılan: {len(posted_news)} haber\n\nAdmin panelinden de yönetebilirsin:")
    await update.message.reply_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text("⛔ Yetki yok."); return
    if update.effective_chat.type != "private": await update.message.reply_text("⛔ DM'den kullan."); return
    if not context.args: await update.message.reply_text("Kullanım: `/duyuru <metin>`", parse_mode="Markdown"); return
    if GROUP_ID == 0: await update.message.reply_text("❌ GROUP_ID ayarlanmamış."); return
    metin = " ".join(context.args)
    t = f"📣 *DUYURU*\n━━━━━━━━━━━━━━\n\n{metin}\n\n━━━━━━━━━━━━━━\n🤖 {haber_ayarlari['kanal_tag']}"
    await context.bot.send_message(GROUP_ID, t, parse_mode="Markdown")
    await update.message.reply_text("✅ Duyuru gruba gönderildi.")

# ── Callback Handler ──
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; uid = q.from_user.id; adm = is_admin(uid)
    await q.answer()

    # Kullanıcı Menüsü
    if data == "menu_ana":
        await q.message.edit_text("👋 *KriptoDropTR Bot*\n\nAşağıdan işlem seç:",
            parse_mode="Markdown", reply_markup=ana_menu_kb(adm))

    elif data == "menu_airdrops":
        aktif = get_active_airdrops()
        if not aktif:
            await q.message.edit_text("🎁 Şu an aktif airdrop yok.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]])); return
        t = "🎁 *Aktif Airdrop Listesi*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        t += "\n\n".join(airdrop_card(a) for a in aktif)
        t += f"\n\n📌 {len(aktif)} aktif airdrop"
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

    elif data == "menu_topairdrops":
        liste = sorted([a for a in get_active_airdrops() if a.get("puan",0) >= 8],
                       key=lambda x: x.get("puan",0), reverse=True)
        t = ("🏆 *En İyi Airdroplar (≥8)*\n━━━━━━━━━━━━━━━━━━━━━\n\n" +
             "\n\n".join(airdrop_card(a) for a in liste)) if liste else "😕 Puan ≥ 8 olan yok."
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

    elif data == "menu_filtre":
        await q.message.edit_text("🔍 *Filtrele:*", parse_mode="Markdown", reply_markup=filtre_kb())

    elif data.startswith("filtre_"):
        now = datetime.now().date(); aktif = get_active_airdrops()
        if data == "filtre_8":   liste = sorted([a for a in aktif if a.get("puan",0)>=8], key=lambda x:x.get("puan",0), reverse=True); bas = "🟢 Puan ≥ 8"
        elif data == "filtre_5": liste = sorted([a for a in aktif if a.get("puan",0)>=5], key=lambda x:x.get("puan",0), reverse=True); bas = "🟡 Puan ≥ 5"
        elif data == "filtre_0": liste = sorted(aktif, key=lambda x:x.get("puan",0), reverse=True); bas = "📋 Tüm Aktif"
        elif data == "filtre_bugun": liste = [a for a in aktif if _bitis_gun(a)==now]; bas = "⏰ Bugün Bitiyor"
        elif data == "filtre_hafta": liste = [a for a in aktif if _bitis_gun(a) and (_bitis_gun(a)-now).days<=7]; bas = "📅 Bu Hafta"
        else: liste = aktif; bas = "Airdroplar"
        t = (f"🔍 *{bas}*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n\n".join(airdrop_card(a) for a in liste) +
             f"\n\n📌 {len(liste)} airdrop") if liste else f"😕 *{bas}* — sonuç yok."
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

    elif data == "menu_istatistik":
        reset_periods()
        t = (f"📊 *İstatistikler*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📅 Bugün: {len(daily_users)}  📆 Bu Hafta: {len(weekly_users)}\n"
             f"🗓 Bu Ay: {len(monthly_users)}  🏆 Toplam: {len(all_time_users)}\n\n"
             f"🎁 Aktif Airdrop: {len(get_active_airdrops())}  |  Toplam: {len(airdrops)}\n"
             f"📰 Paylaşılan Haber: {len(posted_news)}")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    elif data == "menu_haberler":
        await q.message.edit_text("📰 Haberler alınıyor... ⏳")
        haberler = await fetch_crypto_news()
        if not haberler:
            await q.message.edit_text("❌ Haberler alınamadı.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]])); return
        h = haberler[0]; stil = haber_ayarlari.get("ozet_stili","standart")
        ai = await openai_ozet(h["icerik"], h["baslik"], stil)
        await q.message.edit_text(haber_mesaj_formatla(h, ai, ai.get("son_dk",False)),
            parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    elif data == "menu_yardim":
        t = ("📖 *Komutlar*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             "/airdrops — Aktif airdroplar\n/topairdrops — En iyiler\n"
             "/airdrop `<id>` — Detay\n/haberler — Son haberler\n/istatistik — İstatistik\n")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    # Admin Ana Paneli
    elif data == "adm_ana":
        if not adm: await q.answer("⛔ Yetki yok!", show_alert=True); return
        oto = "✅" if haber_ayarlari["aktif"] else "❌"
        sdk = "✅" if haber_ayarlari["son_dk_aktif"] else "❌"
        stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
        t = (f"⚙️ *Admin Paneli*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📰 Oto Haber: {oto}  🚨 Son Dk: {sdk}\n"
             f"⏱ Sıklık: {haber_ayarlari['interval_saat']} saat  📝 Stil: {stil}\n"
             f"📊 Paylaşılan: {len(posted_news)}  |  Aktif Airdrop: {len(get_active_airdrops())}/{len(airdrops)}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_ana_kb())

    # Admin Airdrop Yönetimi
    elif data == "adm_airdrop":
        if not adm: await q.answer("⛔ Yetki yok!", show_alert=True); return
        t = (f"🎁 *Airdrop Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"✅ Aktif: {len(get_active_airdrops())}\n"
             f"❌ Biten: {len([a for a in airdrops if a['durum']=='bitti'])}\n"
             f"📋 Toplam: {len(airdrops)}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_airdrop_kb())

    elif data == "adm_air_ekle_info":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text(
            "➕ *Airdrop Ekle*\n\n`/airdropekle Başlık | Ödül | Başlangıç | Bitiş | Puan | Link`\n\n"
            "*Örnek:*\n`/airdropekle Layer3 | 50 USDT | 01.01.2025 | 31.03.2025 | 9 | https://layer3.xyz`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data == "adm_air_tumu":
        if not adm: await q.answer("⛔", show_alert=True); return
        t = "📋 *Tüm Airdroplar*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + \
            ("\n\n".join(airdrop_card(a) for a in airdrops) if airdrops else "Henüz airdrop yok.")
        if airdrops: t += f"\n\n✅ {len(get_active_airdrops())} aktif  |  Toplam {len(airdrops)}"
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data == "adm_air_aktif":
        if not adm: await q.answer("⛔", show_alert=True); return
        aktif = get_active_airdrops()
        t = "✅ *Aktif Airdroplar*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + \
            ("\n\n".join(airdrop_card(a) for a in aktif) if aktif else "Aktif airdrop yok.")
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data == "adm_air_bitti":
        if not adm: await q.answer("⛔", show_alert=True); return
        bitti = [a for a in airdrops if a["durum"]=="bitti"]
        t = "❌ *Biten Airdroplar*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + \
            ("\n\n".join(airdrop_card(a) for a in bitti) if bitti else "Biten airdrop yok.")
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data == "adm_air_puan":
        if not adm: await q.answer("⛔", show_alert=True); return
        liste = sorted(airdrops, key=lambda x: x.get("puan",0), reverse=True)
        t = "🏆 *Puana Göre Sıralı*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + \
            ("\n\n".join(airdrop_card(a) for a in liste) if liste else "Airdrop yok.")
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data == "adm_air_tarih":
        if not adm: await q.answer("⛔", show_alert=True); return
        def tk(a):
            b = _bitis_gun(a); return b if b else datetime.max.date()
        liste = sorted(airdrops, key=tk)
        t = "📅 *Tarihe Göre Sıralı*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + \
            ("\n\n".join(airdrop_card(a) for a in liste) if liste else "Airdrop yok.")
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    # Admin Haber Yönetimi
    elif data == "adm_haber":
        if not adm: await q.answer("⛔", show_alert=True); return
        oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        t = (f"📰 *Haber Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Otomatik Haber: {oto}\nSon Dakika: {sdk}\nPaylaşılan: {len(posted_news)}\n\n"
             "Kaynaklar: " + ", ".join(k["isim"] for k in HABER_KAYNAKLARI))
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_haber_onizle":
        if not adm: await q.answer("⛔", show_alert=True); return
        if GROUP_ID == 0:
            await q.message.edit_text("❌ GROUP_ID ayarlanmamış.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_ana")]])); return
        await q.message.edit_text("📰 Haberler alınıyor... ⏳")
        await _onizle_gonder(uid, q.message.edit_text, context, idx=0)

    elif data == "hab_onayla":
        if not adm: await q.answer("⛔", show_alert=True); return
        bek = bekleyen_haberler.get(uid)
        if not bek: await q.answer("⚠️ Oturum sona erdi, tekrar dene.", show_alert=True); return
        try:
            await context.bot.send_message(GROUP_ID, bek["text"], parse_mode="Markdown", disable_web_page_preview=True)
            posted_news.add(bek["haber_id"]); posted_news.add(bek["haber_url"])
            bekleyen_haberler.pop(uid, None)
            await q.message.edit_text("✅ *Haber gruba gönderildi!*", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📰 Bir Daha Paylaş", callback_data="adm_haber_onizle")],
                    [InlineKeyboardButton("🔙 Admin Paneli",    callback_data="adm_ana")]]))
        except Exception as e:
            await q.message.edit_text(f"❌ Gönderilemedi: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_ana")]]))

    elif data == "hab_sonraki":
        if not adm: await q.answer("⛔", show_alert=True); return
        bek = bekleyen_haberler.get(uid)
        idx = (bek["index"] + 1) if bek else 0
        await q.message.edit_text("📰 Sonraki haber yükleniyor... ⏳")
        await _onizle_gonder(uid, q.message.edit_text, context, idx=idx)

    elif data == "adm_haber_toggle":
        if not adm: await q.answer("⛔", show_alert=True); return
        haber_ayarlari["aktif"] = not haber_ayarlari["aktif"]
        d = "✅ açıldı" if haber_ayarlari["aktif"] else "❌ kapatıldı"
        await q.answer(f"Otomatik haber {d}!")
        oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        t = (f"📰 *Haber Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Otomatik Haber: {oto}\nSon Dakika: {sdk}\nPaylaşılan: {len(posted_news)}\n\n"
             "Kaynaklar: " + ", ".join(k["isim"] for k in HABER_KAYNAKLARI))
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_sondk_toggle":
        if not adm: await q.answer("⛔", show_alert=True); return
        haber_ayarlari["son_dk_aktif"] = not haber_ayarlari["son_dk_aktif"]
        d = "✅ açıldı" if haber_ayarlari["son_dk_aktif"] else "❌ kapatıldı"
        await q.answer(f"Son dakika {d}!")
        oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        t = (f"📰 *Haber Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Otomatik Haber: {oto}\nSon Dakika: {sdk}\nPaylaşılan: {len(posted_news)}\n\n"
             "Kaynaklar: " + ", ".join(k["isim"] for k in HABER_KAYNAKLARI))
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_haber_durum":
        if not adm: await q.answer("⛔", show_alert=True); return
        oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
        t = (f"📊 *Haber Sistemi Durumu*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Otomatik: {oto}  |  Son Dk: {sdk}\n"
             f"Sıklık: {haber_ayarlari['interval_saat']} saat  |  Eşik: {haber_ayarlari['son_dk_esik']} dk\n"
             f"Özet Stili: {stil}  |  Kanal: {haber_ayarlari['kanal_tag']}\n"
             f"Paylaşılan: {len(posted_news)}\n\n"
             "📡 *Aktif Kaynaklar:*\n" + "\n".join(f"• {k['isim']}" for k in HABER_KAYNAKLARI))
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_haber_temizle":
        if not adm: await q.answer("⛔", show_alert=True); return
        n = len(posted_news); posted_news.clear()
        await q.answer(f"✅ {n} kayıt temizlendi!")
        await q.message.edit_text(f"✅ Haber geçmişi temizlendi ({n} kayıt silindi).",
            reply_markup=adm_haber_kb())

    # Haber Ayarları
    elif data == "adm_haber_ayar":
        if not adm: await q.answer("⛔", show_alert=True); return
        stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
        t = (f"⚙️ *Haber Ayarları*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Özet Stili: {stil}\nSıklık: Her {haber_ayarlari['interval_saat']} saatte bir\n\n"
             "Sıklık butonlarına basınca anında değişir:")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())

    elif data == "adm_stil_menu":
        if not adm: await q.answer("⛐", show_alert=True); return
        t = ("📝 *Özet Stili Seç*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             "📝 *Standart* — 3-4 cümle, bilgilendirici\n"
             "📄 *Detaylı* — 5-7 cümle, arka plan ve etki\n"
             "⚡ *Kısaca* — 2 cümle, sadece özet\n"
             "📌 *Madde Madde* — bullet point formatı\n\n"
             f"Şu an: {OZET_STILLERI.get(haber_ayarlari.get('ozet_stili','standart'),{}).get('isim','')}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_stil_kb())

    elif data.startswith("adm_stil_"):
        if not adm: await q.answer("⛔", show_alert=True); return
        k = data.replace("adm_stil_","")
        if k in OZET_STILLERI:
            haber_ayarlari["ozet_stili"] = k
            await q.answer(f"✅ Stil: {OZET_STILLERI[k]['isim']}")
            await q.message.edit_text(f"✅ Özet stili *{OZET_STILLERI[k]['isim']}* olarak ayarlandı.",
                parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())

    elif data.startswith("adm_sure_"):
        if not adm: await q.answer("⛔", show_alert=True); return
        try:
            s = int(data.replace("adm_sure_",""))
            haber_ayarlari["interval_saat"] = s
            await q.answer(f"✅ Sıklık: {s} saat")
            await q.message.edit_text(f"✅ Sıklık *{s} saat* olarak ayarlandı.\n⚠️ Bir sonraki döngüde geçerli olur.",
                parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())
        except: await q.answer("❌ Hata!")

    # Admin Diğer
    elif data == "adm_istat":
        if not adm: await q.answer("⛔", show_alert=True); return
        reset_periods()
        t = (f"📊 *Detaylı İstatistik*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📅 Bugün: {len(daily_users)}\n📆 Bu Hafta: {len(weekly_users)}\n"
             f"🗓 Bu Ay: {len(monthly_users)}\n🏆 Tüm Zamanlar: {len(all_time_users)}\n\n"
             f"🎁 Aktif Airdrop: {len(get_active_airdrops())}\n"
             f"❌ Biten: {len([a for a in airdrops if a['durum']=='bitti'])}\n"
             f"📋 Toplam Airdrop: {len(airdrops)}\n\n"
             f"📰 Paylaşılan Haber: {len(posted_news)}")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")]]))

    elif data == "adm_uye_rapor":
        if not adm: await q.answer("⛔", show_alert=True); return
        reset_periods(); td = datetime.now().date(); gd = {}
        for e in join_log:
            if (td-e["date"]).days < 7:
                l = e["date"].strftime("%d.%m"); gd[l] = gd.get(l,0)+1
        ds = "".join(f"  {g}: {'█'*min(s,15)} {s}\n" for g,s in sorted(gd.items()))
        t = (f"👥 *Üye Raporu*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📅 Bugün: {len(daily_users)}  📆 Bu Hafta: {len(weekly_users)}\n"
             f"🗓 Bu Ay: {len(monthly_users)}  🏆 Toplam: {len(all_time_users)}\n\n"
             f"📈 *Son 7 Gün:*\n{ds if ds else '  Veri yok.'}")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")]]))

    elif data == "adm_duyuru_info":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text(
            "📣 *Duyuru Gönder*\n\nDM'den:\n`/duyuru <metin>`\n\n*Örnek:*\n"
            "`/duyuru 🎉 Yeni airdrop fırsatı! Detay için /airdrops yazın.`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")]]))

# ── Otomatik Haber Job ──
async def auto_haber_job(context: ContextTypes.DEFAULT_TYPE):
    if GROUP_ID == 0 or not OPENAI_KEY: return
    if not haber_ayarlari.get("aktif", True): return
    haberler = await fetch_crypto_news()
    yeni = _yeni(haberler)
    if not yeni: log.info("auto_haber_job: yeni haber yok"); return
    h = yeni[0]; stil = haber_ayarlari.get("ozet_stili","standart")
    ai = await openai_ozet(h["icerik"], h["baslik"], stil)
    text = haber_mesaj_formatla(h, ai, son_dk=False)
    try:
        await context.bot.send_message(GROUP_ID, text, parse_mode="Markdown", disable_web_page_preview=True)
        posted_news.add(h["id"]); posted_news.add(h["url"])
        log.info(f"Oto haber: {h['baslik'][:60]}")
    except Exception as e:
        log.warning(f"Oto haber hatasi: {e}")

async def son_dk_haber_job(context: ContextTypes.DEFAULT_TYPE):
    if GROUP_ID == 0 or not OPENAI_KEY: return
    if not haber_ayarlari.get("son_dk_aktif", True): return
    esik = haber_ayarlari.get("son_dk_esik", 20)
    su_an = datetime.utcnow()
    haberler = await fetch_crypto_news()
    for h in haberler:
        if h["id"] in posted_news or h["url"] in posted_news: continue
        try: yas = (su_an - h["zaman"]).total_seconds() / 60
        except: continue
        if yas > esik: continue
        ai = await openai_ozet(h["icerik"], h["baslik"], "kisaca")
        if not ai.get("son_dk", False):
            posted_news.add(h["id"]); posted_news.add(h["url"]); continue
        text = haber_mesaj_formatla(h, ai, son_dk=True)
        try:
            await context.bot.send_message(GROUP_ID, text, parse_mode="Markdown", disable_web_page_preview=True)
            posted_news.add(h["id"]); posted_news.add(h["url"])
            log.info(f"SON DAKİKA: {h['baslik'][:60]}")
            await asyncio.sleep(2)
        except Exception as e:
            log.warning(f"Son dk hatasi: {e}")

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",       "Ana menü"),
        BotCommand("airdrops",    "Aktif airdroplar"),
        BotCommand("topairdrops", "En iyi airdroplar"),
        BotCommand("haberler",    "Son Türkçe kripto haberleri"),
        BotCommand("istatistik",  "İstatistikler"),
        BotCommand("yardim",      "Yardım"),
    ])
    log.info("KriptoDropTR Bot v4 hazır 🚀")

def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    hi = haber_ayarlari.get("interval_saat",6) * 3600
    app.job_queue.run_repeating(auto_haber_job,   interval=hi,  first=300)
    app.job_queue.run_repeating(son_dk_haber_job, interval=600, first=120)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(CommandHandler("start",           cmd_start))
    app.add_handler(CommandHandler("yardim",          cmd_yardim))
    app.add_handler(CommandHandler("airdrops",        cmd_airdrops))
    app.add_handler(CommandHandler("topairdrops",     cmd_top_airdrops))
    app.add_handler(CommandHandler("airdrop",         cmd_airdrop_detay))
    app.add_handler(CommandHandler("haberler",        cmd_haberler))
    app.add_handler(CommandHandler("istatistik",      cmd_istatistik))
    app.add_handler(CommandHandler("airdropekle",     cmd_airdrop_ekle))
    app.add_handler(CommandHandler("airdropduzenle",  cmd_airdrop_duzenle))
    app.add_handler(CommandHandler("airdropbitir",    cmd_airdrop_bitir))
    app.add_handler(CommandHandler("airdropsil",      cmd_airdrop_sil))
    app.add_handler(CommandHandler("haberler_paylas", cmd_haber_paylas))
    app.add_handler(CommandHandler("haberayar",       cmd_haber_ayar))
    app.add_handler(CommandHandler("duyuru",          cmd_duyuru))
    app.add_handler(CallbackQueryHandler(button_handler))
    log.info("BOT AKTIF")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
