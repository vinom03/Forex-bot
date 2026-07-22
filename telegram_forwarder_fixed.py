r"""
============================================================
بوت نسخ منشورات تيليجرام - ForexGold Pro
============================================================

الوظيفة:
    يراقب قناة تيليجرام مصدر (SOURCE_CHANNEL) وينسخ أي منشور جديد
    فيها (نص و/أو صور) إلى قناتك (DEST_CHANNEL)، بعد:
      1. تنظيف تنسيق النص (حذف وسوم HTML، تحويل الأسطر...)
      2. حذف توقيع/رابط القناة المصدر من نهاية المنشور
      3. إضافة توقيعك الخاص (OWN_SIGNATURE) بدلاً منه
      4. رفع أي صور موجودة بالمنشور مباشرة (مو بالرابط، تلافياً لأخطاء
         تيليجرام برفض الروابط الخارجية)

طريقة التشغيل (مصمم لـ GitHub Actions):
    هذا الملف "تشغيلة وحدة" (run once) — يشتغل، يتحقق من المنشورات
    الجديدة، يرسلها، ويطفي. ملف الجدولة (bot.yml) هو اللي يكرر تشغيله
    تلقائياً كل 5 دقائق. ما فيه حلقة لا نهائية هنا لأن GitHub Actions
    ما يدعم تشغيل مستمر بالخلفية.

المتطلبات قبل التشغيل:
    - متغير بيئة اسمه BOT_TOKEN فيه توكن البوت (يُضاف كـ GitHub Secret،
      مايُكتب بالكود مباشرة لأسباب أمنية).
    - مكتبتين بايثون: requests و beautifulsoup4
      (تثبيت: pip install requests beautifulsoup4)

الملفات اللي ينشئها/يحدّثها البوت تلقائياً بمجلد المشروع:
    - last_seen_id.txt  -> رقم آخر منشور تمت معالجته (عشان ما يعيد
                            إرسال نفس المنشورات كل تشغيلة).
    - bot_log.txt        -> سجل نصي بكل عملية صارت بكل تشغيلة (آخر 300
                            سطر محفوظين، الأقدم يُحذف تلقائياً).

للتعديل مستقبلاً:
    - DEST_CHANNEL / SOURCE_CHANNEL / OWN_SIGNATURE بالأسفل.
    - signature_line بدالة clean_text() لو تغيّرت صياغة توقيع المصدر.

============================================================
🔧 لو تبي تغيّر القناة المصدر أو قناتك (الوجهة):
============================================================
فيه 4 أماكن بس بالكود، كلها معلّمة بـ 🔧 عشان تلقاها بسرعة
(دور عليها بالبحث Ctrl+F / بحث بصفحة GitHub):

1. SOURCE_CHANNEL  -> اسم القناة المصدر الجديدة، بدون @ وبدون رابط،
                       بس اسم المستخدم زي ما يبين بعد t.me/
                       مثال: رابط القناة https://t.me/SomeNews
                             تكتب: SOURCE_CHANNEL = "SomeNews"

2. DEST_CHANNEL    -> قناتك اللي يوصلها المنشور (مع @ بأولها)
                       مثال: DEST_CHANNEL = "@MyNewChannel"

3. OWN_SIGNATURE   -> التوقيع اللي ينضاف بدل توقيع المصدر - غيّر
                       الاسم والرابط ليطابق قناتك الجديدة

4. signature_line  -> جوا دالة clean_text() - نمط (regex) يتعرف على
                       توقيع القناة المصدر القديمة عشان يحذفه من كل
                       منشور. *لازم* تغيّره ليطابق توقيع المصدر الجديد،
                       وإلا توقيعه القديم بيبين مع توقيعك جنب بعض!
                       افتح أي منشور من القناة المصدر الجديدة، شوف
                       آخر سطر فيه (عادة اسم القناة)، وخذ الكلمات
                       الثابتة منه بس (تجاهل الإيموجي والتشكيل).
                       مثال: لو التوقيع الجديد "قناة الأخبار السريعة"
                       اكتب: signature_line = re.compile(r'قناة\s*الأخبار\s*السريعة')

⚠️ لو نسيت تعدّل رقم 4: البوت يستمر يشتغل ويرسل عادي، بس توقيع
   القناة المصدر القديم ما بينحذف (بيبين مكرر مع توقيعك بكل منشور).
============================================================
"""

import os
import re
import json
import time
import datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

# التوكن يُقرأ من متغير بيئة (GitHub Secret) بدل ما يكون مكتوب هنا مباشرة
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "خطأ: لازم تحط توكن البوت بمتغير بيئة اسمه BOT_TOKEN (عن طريق GitHub Secrets)."
    )

DEST_CHANNEL = "@ForexGold_Pro"      # 🔧 قناتك (وين يترسل المنشور)
SOURCE_CHANNEL = "ForexBreakingNews"  # 🔧 القناة المصدر (منين يُجلب المنشور)

TELEGRAM_CAPTION_LIMIT = 1024  # حد تيليجرام لطول الكابشن مع الصور

# آخر منشور تمت معالجته يُحفظ بملف داخل المستودع نفسه، عشان يفضل محفوظ
# بين كل تشغيلة وتشغيلة (GitHub Actions ما يحتفظ بالذاكرة بين التشغيلات)
LAST_SEEN_FILE = "last_seen_id.txt"

# ============================================================
# سجل موحد (log) يُحفظ بملف بالمستودع نفسه، عشان تقدر تفتح ملف وحد
# وتشوف بالتفصيل كل خطوة صارت بكل تشغيلة (بدء المراقبة، نسخ، تعديل،
# إرسال بصورة أو بدونها، أو أي خطأ) بدون ما تدخل تبويب Actions.
# ============================================================
LOG_FILE = "bot_log.txt"
MAX_LOG_LINES = 300  # نحتفظ بآخر 300 سطر بس عشان الملف ما يكبر بلا حدود

_log_buffer = []


def log(message):
    print(message)
    _log_buffer.append(str(message))


def flush_log():
    if not _log_buffer:
        return
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    existing_lines = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            existing_lines = f.read().splitlines()
    new_lines = [f"--- {timestamp} ---"] + _log_buffer
    all_lines = (existing_lines + new_lines)[-MAX_LOG_LINES:]
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines) + "\n")


def load_last_seen_id():
    if os.path.exists(LAST_SEEN_FILE):
        with open(LAST_SEEN_FILE, "r") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    return None


def save_last_seen_id(post_id):
    with open(LAST_SEEN_FILE, "w") as f:
        f.write(str(post_id))


# ============================================================
# جلب المنشورات من صفحة معاينة القناة المصدر
# ============================================================
def get_channel_posts():
    """
    يرجع لستة منشورات، كل منشور dict فيه:
      id         -> رقم المنشور
      text_html  -> النص كـ HTML خام (قبل التنظيف)
      photos     -> لستة روابط الصور (تدعم منشور بصورة وحدة أو ألبوم كامل)

    نستخدم BeautifulSoup (مو ريجكس) عشان:
    - كل منشور يُقرأ من "صندوقه" الخاص (data-post) بدون تداخل مع منشورات
      أو عناصر أخرى بالصفحة.
    - نستثني إشعارات النظام (زي "فلان ثبّت صورة") لأنها مش منشورات
      حقيقية رغم إن تيليجرام يعرضها بنفس شكل المنشور.
    - نمر على كل منشور حتى لو ما فيه نص (صورة بدون كابشن)، فما نفقده.
    """
    url = f"https://t.me/s/{SOURCE_CHANNEL}"

    # إعادة محاولة تلقائية: أحياناً بروكسي الاستضافة يرجع خطأ مؤقت مثل
    # 503 Service Unavailable. بدل ما نستسلم فوراً، نجرب كذا مرة أول.
    max_retries = 3
    retry_delay = 8  # ثواني بين كل محاولة

    response = None
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()  # يتأكد إن الرد فعلاً ناجح (200) مو صفحة خطأ
            break
        except Exception as e:
            last_error = e
            log(f"⚠️ محاولة {attempt}/{max_retries} فشلت (جلب المنشورات): {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)

    if response is None:
        raise last_error

    soup = BeautifulSoup(response.text, "html.parser")

    posts = []
    for msg_div in soup.find_all("div", class_="tgme_widget_message", attrs={"data-post": True}):
        try:
            post_id = int(msg_div["data-post"].split("/")[-1])
        except (KeyError, ValueError):
            continue

        # نستثني إشعارات النظام (زي "فلان ثبّت صورة") - هذي مش منشورات
        # حقيقية، بس تيليجرام يعرضها بنفس شكل المنشور بصفحة المعاينة
        if msg_div.find(class_=lambda c: c and "service" in c):
            continue

        # النص (قد لا يوجد إذا كان المنشور صورة بدون كابشن)
        text_div = msg_div.find("div", class_="tgme_widget_message_text")
        text_html = str(text_div) if text_div else ""

        # الصور: منشور بصورة وحدة أو ألبوم (أكثر من صورة)
        photo_urls = []
        for a_tag in msg_div.find_all("a", class_="tgme_widget_message_photo_wrap"):
            style = a_tag.get("style", "")
            m = re.search(r"background-image:url\('(.+?)'\)", style)
            if m:
                photo_urls.append(m.group(1))

        posts.append({"id": post_id, "text_html": text_html, "photos": photo_urls})

    posts.sort(key=lambda p: p["id"])  # ترتيب تصاعدي مضمون حسب رقم المنشور
    return posts


# ============================================================
# تنظيف النص وحذف توقيع القناة المصدر + إضافة توقيعك
# ============================================================
OWN_SIGNATURE = "📢 قناة ForexGold Pro || اشترك الآن:\nhttps://t.me/ForexGold_Pro"  # 🔧

# 🔧 خلي القيمة False لو تبي توقف التنسيق العريض وترجع للنص العادي
BOLD_TEXT = True


def make_bold_html(text):
    """
    يجهّز النص عشان يترسل عريض (Bold) عن طريق تنسيق HTML الخاص
    بتيليجرام. نهرب أحرف &, <, > الخاصة أولاً (شرط تيليجرام لتنسيق
    HTML)، وبعدين نغلّف النص كامل بوسم <b>.
    """
    escaped = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
    return f"<b>{escaped}</b>"


def clean_text(text_html):
    text = text_html
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)

    # حذف أحرف مخفية (zero-width) قد تحطها تيليجرام بين أحرف الروابط
    # وتكسر مطابقة الريجكس رغم إن النص يبين متطابق للعين
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', text)

    # نشيل أسطر التوقيع/الرابط من نهاية المنشور فقط، سطر سطر، ونتوقف
    # فور ما نوصل لأول سطر محتوى حقيقي. هذا يضمن عدم المساس بأي جزء
    # من وسط أو بداية المنشور مهما كان شكله.
    signature_line = re.compile(r'قناة\s*أ?خبار\s*الفوركس\s*العاجلة')  # 🔧 توقيع القناة المصدر
    link_line = re.compile(r'(t\.me|telegram\.me)/', re.IGNORECASE)
    # علامات التشكيل العربية (فتحة، ضمة، كسرة...) - نتجاهلها وقت الفحص بس
    # لأن كلمات زي "أَخبار" (بتشكيل) ما كانت تتطابق مع "أخبار" (بدون تشكيل)
    arabic_diacritics = re.compile(r'[\u064B-\u065F\u0670\u06D6-\u06ED]')

    lines = text.split('\n')
    while lines:
        last = lines[-1].strip()
        last_normalized = arabic_diacritics.sub('', last)
        if last == '' or signature_line.search(last_normalized) or link_line.search(last_normalized):
            lines.pop()
            continue
        break  # وصلنا لسطر محتوى حقيقي -> نوقف الحذف فوراً

    text = '\n'.join(lines).strip()

    # إضافة توقيع قناتك في نهاية كل منشور
    text = f"{text}\n\n{OWN_SIGNATURE}" if text else OWN_SIGNATURE

    return text


# ============================================================
# الإرسال إلى تيليجرام
# ============================================================
# ترويسة تحاكي متصفح حقيقي يفتح صفحة تيليجرام - بعض سيرفرات تخزين
# الصور عند تيليجرام (cdn.telesco.pe) ترفض الطلب بدونها بخطأ 500
IMAGE_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://t.me/",
}


def download_image(url):
    max_retries = 2
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=IMAGE_REQUEST_HEADERS, timeout=20)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last_error = e
            log(f"⚠️ محاولة {attempt}/{max_retries} فشلت (تحميل صورة): {e}")
            if attempt < max_retries:
                time.sleep(3)
    raise last_error


def post_with_retry(url, **kwargs):
    """
    يرسل طلب POST لتيليجرام، ولو رجع 429 (Too Many Requests - طلبات
    كثيرة بوقت قصير) ينتظر بالضبط المدة اللي يطلبها تيليجرام نفسه
    (retry_after) بدل ما يعتبرها فشل عادي، ويعيد المحاولة تلقائياً.
    """
    max_retries = 3
    r = None
    for attempt in range(1, max_retries + 1):
        r = requests.post(url, **kwargs)
        if r.status_code != 429:
            return r
        try:
            retry_after = r.json().get("parameters", {}).get("retry_after", 5)
        except Exception:
            retry_after = 5
        log(f"⏳ تيليجرام طلب الانتظار {retry_after} ثانية قبل إعادة المحاولة "
            f"(429 - طلبات كثيرة) - محاولة {attempt}/{max_retries}")
        time.sleep(retry_after + 1)
    return r


def send_text(text):
    if not text:
        return True
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": DEST_CHANNEL, "text": text}
    if BOLD_TEXT:
        payload["text"] = make_bold_html(text)
        payload["parse_mode"] = "HTML"
    r = post_with_retry(url, data=payload, timeout=15)
    log(f"   ↳ رد تيليجرام (نص): {r.status_code} {r.text[:200]}")
    return r.ok and r.json().get("ok", False)


def send_single_photo(photo_url, caption=""):
    # نحمّل الصورة بنفسنا ونرفعها مباشرة (upload) بدل ما نعطي تيليجرام
    # الرابط ويحاول يجيبه بنفسه -> هذا كان سبب خطأ 400 "failed to get HTTP URL content"
    try:
        image_bytes = download_image(photo_url)
    except Exception as e:
        log(f"   ↳ فشل تحميل الصورة: {e}")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": DEST_CHANNEL}
    if caption:
        cap = caption[:TELEGRAM_CAPTION_LIMIT]
        if BOLD_TEXT:
            payload["caption"] = make_bold_html(cap)
            payload["parse_mode"] = "HTML"
        else:
            payload["caption"] = cap
    files = {"photo": ("image.jpg", image_bytes)}
    r = post_with_retry(url, data=payload, files=files, timeout=30)
    log(f"   ↳ رد تيليجرام (صورة): {r.status_code} {r.text[:200]}")
    return r.ok and r.json().get("ok", False)


def send_media_group(photo_urls, caption=""):
    files = {}
    media = []
    for i, photo_url in enumerate(photo_urls):
        try:
            image_bytes = download_image(photo_url)
        except Exception as e:
            log(f"   ↳ فشل تحميل صورة رقم {i + 1}: {e}")
            continue
        field_name = f"photo{i}"
        files[field_name] = (f"image{i}.jpg", image_bytes)
        item = {"type": "photo", "media": f"attach://{field_name}"}
        if i == 0 and caption:
            cap = caption[:TELEGRAM_CAPTION_LIMIT]
            if BOLD_TEXT:
                item["caption"] = make_bold_html(cap)
                item["parse_mode"] = "HTML"
            else:
                item["caption"] = cap
        media.append(item)

    if not media:
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
    r = post_with_retry(api_url, data={"chat_id": DEST_CHANNEL, "media": json.dumps(media)}, files=files, timeout=40)
    log(f"   ↳ رد تيليجرام (ألبوم): {r.status_code} {r.text[:200]}")
    return r.ok and r.json().get("ok", False)


def send_post(text, photo_urls, post_id):
    """
    يقرر طريقة الإرسال حسب محتوى المنشور، ويطبع بالسجل ملخص واضح
    لكل حالة (بصورة / بدون صورة / فشلت الصورة).
    """
    if not photo_urls:
        send_text(text)
        log(f"📤 تم إرسال المنشور {post_id} كنص فقط (بدون صورة)")
        return

    # نترك هامش أمان (20 حرف) بحد الكابشن عشان وسوم <b></b> اللي بتنضاف
    # وقت التنسيق العريض ما تسبب تجاوز حد تيليجرام (1024 حرف)
    caption_fits = len(text) <= (TELEGRAM_CAPTION_LIMIT - 20)
    caption = text if caption_fits else ""

    if len(photo_urls) == 1:
        photo_sent = send_single_photo(photo_urls[0], caption=caption)
        photo_desc = "مع صورة واحدة"
    else:
        photo_sent = send_media_group(photo_urls, caption=caption)
        photo_desc = f"مع ألبوم من {len(photo_urls)} صور"

    if photo_sent:
        extra = "" if caption_fits else " (والنص الكامل أُرسل برسالة منفصلة لطوله)"
        log(f"📤 تم إرسال المنشور {post_id} {photo_desc}{extra}")
    else:
        log(f"⚠️ فشل إرسال صورة المنشور {post_id}، تم إرسال النص فقط بدلاً منها")

    # نرسل النص كرسالة منفصلة إذا: الصورة فشلت بالكامل، أو نجحت لكن بدون كابشن (طويل)
    if text and (not photo_sent or not caption_fits):
        send_text(text)


# ============================================================
# تشغيلة واحدة (بدل الحلقة اللانهائية) — مناسبة لـ GitHub Actions
# اللي يتكفل بتكرار التشغيل كل 5 دقائق عن طريق جدولة (cron) خارجية
# ============================================================
def run_once():
    try:
        log("=" * 50)
        log("🤖 البوت اشتغل - يتحقق من منشورات جديدة...")

        last_seen_id = load_last_seen_id()
        if last_seen_id is not None:
            log(f"📍 يراقب من المنشور رقم: {last_seen_id}")
        else:
            log("📍 هذي أول تشغيلة - ما فيه نقطة بداية محفوظة بعد")

        try:
            posts = get_channel_posts()
        except Exception as e:
            log(f"❌ صار خطأ بجلب المنشورات: {e}")
            return

        if not posts:
            log("ℹ️ ما فيه منشورات بالصفحة حالياً.")
            return

        latest_id = posts[-1]["id"]

        if last_seen_id is None:
            # أول تشغيلة على الإطلاق -> نحفظ نقطة البداية بدون إرسال شي
            save_last_seen_id(latest_id)
            log(f"✅ بدأنا المراقبة من المنشور رقم: {latest_id} (بدون إرسال منشورات قديمة)")
            return

        new_posts = [p for p in posts if p["id"] > last_seen_id]

        if not new_posts:
            log("ℹ️ ما فيه منشورات جديدة هالمرة.")
            return

        log(f"📊 عدد المنشورات الجديدة: {len(new_posts)}")

        for post in new_posts:
            try:
                log(f"🔄 معالجة المنشور رقم {post['id']}...")
                clean = clean_text(post["text_html"])
                log(f"✏️ تم تنظيف نص المنشور {post['id']} (حذف التوقيع القديم + إضافة توقيعك)")
                send_post(clean, post["photos"], post["id"])
                log(f"✅ تم نسخ المنشور {post['id']} بنجاح")
            except Exception as post_err:
                # خطأ بمنشور واحد بس -> نطبعه ونكمل، وما نوقف الدفعة كلها
                log(f"❌ فشل إرسال المنشور {post['id']}: {post_err}")
            finally:
                # نحدّث آخر منشور تمت معالجته بعد كل منشور (نجح أو فشل) عشان
                # ما نرجع نرسل نفس المنشور مرة ثانية بالتشغيلة الجاية
                save_last_seen_id(post["id"])
    finally:
        # نحفظ السجل دائماً حتى لو صار خطأ غير متوقع بأي مكان فوق
        flush_log()


if __name__ == "__main__":
    run_once()
