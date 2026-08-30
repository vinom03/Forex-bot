r"""
============================================================
بوت نسخ منشورات تيليجرام - ForexGold Pro
============================================================

الوظيفة:
    يراقب قناة تيليجرام مصدر (SOURCE_CHANNEL) وينسخ أي منشور جديد
    فيها (نص و/أو صور) إلى قناتك (DEST_CHANNEL)، بعد:
      1. تنظيف تنسيق النص (حذف وسوم HTML، تحويل الأسطر...)
      2. حذف توقيع/رابط القناة المصدر من نهاية المنشور (بس لو المصدر
         سوى توقيعه هو بنفس المنشور - غير كذا يترسل نظيف بلا توقيع)
      3. ترجمة النص من الإنجليزي إلى العربي (المصدر بيكون كله إنجليزي)
      4. إضافة توقيعك الخاص (OWN_SIGNATURE) بدلاً من توقيع المصدر
      5. رفع أي صور موجودة بالمنشور مباشرة (مو بالرابط، تلافياً لأخطاء
         تيليجرام برفض الروابط الخارجية)
      6. لو المنشور "رد" على منشور ثاني بالمصدر، وكانت "نسخته" موجودة
         بقناتك (بدفتر المطابقة)، يترسل كـ"رد حقيقي" على نفس المنشور
         بقناتك. لو ما لقى مطابقة (منشور قديم قبل ما نبدأ نتابعه)،
         يترسل عادي بدون رد.

طريقة التشغيل (مصمم لـ GitHub Actions):
    هذا الملف "تشغيلة وحدة" (run once) — يشتغل، يتحقق من المنشورات
    الجديدة، يرسلها، ويطفي. ملف الجدولة (main.yml) هو اللي يكرر تشغيله
    تلقائياً عن طريق جدولة خارجية (cron-job.org أو GitHub). ما فيه
    حلقة لا نهائية هنا لأن GitHub Actions ما يدعم تشغيل مستمر بالخلفية.

المتطلبات قبل التشغيل:
    - متغير بيئة اسمه BOT_TOKEN فيه توكن البوت (يُضاف كـ GitHub Secret،
      مايُكتب بالكود مباشرة لأسباب أمنية).
    - ثلاث مكتبات بايثون: requests و beautifulsoup4 و deep-translator
      (تثبيت: pip install requests beautifulsoup4 deep-translator)

الملفات اللي ينشئها/يحدّثها البوت تلقائياً بمجلد المشروع:
    - last_seen_id.txt  -> رقم آخر منشور تمت معالجته (عشان ما يعيد
                            إرسال نفس المنشورات كل تشغيلة).
    - bot_log.txt        -> سجل نصي بكل عملية صارت بكل تشغيلة (آخر 300
                            سطر محفوظين، الأقدم يُحذف تلقائياً).
    - id_map.json         -> دفتر مطابقة: يربط رقم منشور المصدر برقم
                            نفس المنشور بقناتك، لازم لميزة الردود
                            (آخر 500 مطابقة بس محفوظة، الأقدم تُحذف).

للتعديل مستقبلاً:
    - DEST_CHANNEL / SOURCE_CHANNEL / OWN_SIGNATURE بالأسفل.
    - signature_line بدالة clean_text() لو تغيّرت صياغة توقيع المصدر.
    - TRANSLATE_ENABLED بالأسفل لو تبي توقف/تشغّل الترجمة بسرعة.

============================================================
🔧 لو تبي تغيّر القناة المصدر أو قناتك (الوجهة):
============================================================
فيه 4 أماكن بس بالكود، كلها معلّمة بـ 🔧 عشان تلقاها بسرعة
(دور عليها بالبحث Ctrl+F / بحث بصفحة GitHub):

1. SOURCE_CHANNEL  -> اسم القناة المصدر الجديدة، بدون @ وبدون رابط،
                       بس اسم المستخدم زي ما يبين بعد t.me/
2. DEST_CHANNEL    -> قناتك اللي يوصلها المنشور (مع @ بأولها)
3. OWN_SIGNATURE   -> التوقيع اللي ينضاف بدل توقيع المصدر
4. signature_line  -> جوا دالة clean_text() - نمط (regex) يتعرف على
                       توقيع القناة المصدر القديمة عشان يحذفه من كل
                       منشور.
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
from deep_translator import GoogleTranslator

# التوكن يُقرأ من متغير بيئة (GitHub Secret) بدل ما يكون مكتوب هنا مباشرة
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "خطأ: لازم تحط توكن البوت بمتغير بيئة اسمه BOT_TOKEN (عن طريق GitHub Secrets)."
    )

DEST_CHANNEL = "@ForexGold_Pro"      # 🔧 قناتك (وين يترسل المنشور)
SOURCE_CHANNEL = "bu3oof_fx"  # 🔧 القناة المصدر (منين يُجلب المنشور)

TELEGRAM_CAPTION_LIMIT = 1024  # حد تيليجرام لطول الكابشن مع الصور

LAST_SEEN_FILE = "last_seen_id.txt"

LOG_FILE = "bot_log.txt"
MAX_LOG_LINES = 300

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


ID_MAP_FILE = "id_map.json"
MAX_ID_MAP_ENTRIES = 500


def load_id_map():
    if os.path.exists(ID_MAP_FILE):
        try:
            with open(ID_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_id_map(id_map):
    if len(id_map) > MAX_ID_MAP_ENTRIES:
        newest_keys = sorted(
            id_map.keys(),
            key=lambda k: int(k)
        )[-MAX_ID_MAP_ENTRIES:]
        id_map = {
            k: id_map[k]
            for k in newest_keys
        }

    with open(ID_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(id_map, f)

    return id_map


def get_channel_posts():
    url = f"https://t.me/s/{SOURCE_CHANNEL}"

    max_retries = 3
    retry_delay = 8

    response = None
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                timeout=15
            )
            response.raise_for_status()
            break

        except Exception as e:
            last_error = e

            log(
                f"⚠️ محاولة {attempt}/{max_retries} فشلت "
                f"(جلب المنشورات): {e}"
            )

            if attempt < max_retries:
                time.sleep(retry_delay)

    if response is None:
        raise last_error

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    posts = []

    for msg_div in soup.find_all(
        "div",
        class_="tgme_widget_message",
        attrs={"data-post": True}
    ):
        try:
            post_id = int(
                msg_div["data-post"].split("/")[-1]
            )

        except (KeyError, ValueError):
            continue

        if msg_div.find(
            class_=lambda c: c and "service" in c
        ):
            continue

        reply_to_source_id = None

        reply_tag = msg_div.find(
            "a",
            class_="tgme_widget_message_reply"
        )

        if reply_tag:
            href = reply_tag.get(
                "href",
                ""
            )

            m = re.search(
                r'/(\d+)(?:\?.*)?$',
                href
            )

            if m:
                reply_to_source_id = int(
                    m.group(1)
                )

            reply_tag.decompose()

        text_div = msg_div.find(
            "div",
            class_="tgme_widget_message_text"
        )

        text_html = (
            str(text_div)
            if text_div
            else ""
        )

        photo_urls = []

        for a_tag in msg_div.find_all(
            "a",
            class_="tgme_widget_message_photo_wrap"
        ):
            style = a_tag.get(
                "style",
                ""
            )

            m = re.search(
                r"background-image:url\('(.+?)'\)",
                style
            )

            if m:
                photo_urls.append(
                    m.group(1)
                )

        posts.append({
            "id": post_id,
            "text_html": text_html,
            "photos": photo_urls,
            "reply_to": reply_to_source_id,
        })

    posts.sort(
        key=lambda p: p["id"]
    )

    return posts


OWN_SIGNATURE = "📢 للانضمام :\nhttps://t.me/YOSEEF_ADMIN"  # 🔧

BOLD_TEXT = True


def make_bold_html(text):
    escaped = (
        text.replace(
            "&",
            "&amp;"
        )
        .replace(
            "<",
            "&lt;"
        )
        .replace(
            ">",
            "&gt;"
        )
    )

    return f"<b>{escaped}</b>"


def clean_text(text_html):
    text = text_html

    text = re.sub(
        r'<br\s*/?>',
        '\n',
        text
    )

    text = re.sub(
        r'<img[^>]*alt="([^"]*)"[^>]*>',
        r'\1',
        text
    )

    text = re.sub(
        r'<[^>]+>',
        '',
        text
    )

    text = unescape(text)

    text = re.sub(
        r'[\u200b\u200c\u200d\u200e\u200f\ufeff]',
        '',
        text
    )

    signature_line = re.compile(
        r'@Abdulrahamn2022',
        re.IGNORECASE
    )  # 🔧 توقيع القناة المصدر

    link_line = re.compile(
        r'(t\.me|telegram\.me)/',
        re.IGNORECASE
    )

    arabic_diacritics = re.compile(
        r'[\u064B-\u065F\u0670\u06D6-\u06ED]'
    )

    lines = text.split('\n')

    found_source_signature = False

    while lines:
        last = lines[-1].strip()

        if last == '':
            lines.pop()
            continue

        last_normalized = arabic_diacritics.sub(
            '',
            last
        )

        if (
            signature_line.search(last_normalized)
            or link_line.search(last_normalized)
        ):
            found_source_signature = True
            lines.pop()
            continue

        break

    text = '\n'.join(lines).strip()

    # ملاحظة مهمة: ما نضيف توقيعك هنا. نرجع بس علامة (found_source_signature)
    # عشان نضيف توقيعك بعدين في run_once() بعد الترجمة مباشرة - لو نضيفه هنا
    # قبل الترجمة، بيروح يترجم مع باقي النص وينكسر تنسيقه/رابطه.
    if found_source_signature:
        log(
            "🔏 المصدر سوى توقيعه بهالمنشور - "
            "راح يُحذف ويُضاف توقيعك بدله بعد الترجمة"
        )
    else:
        log(
            "ℹ️ المصدر ما سوى أي توقيع بهالمنشور - "
            "راح يترسل نظيف بدون توقيع"
        )

    return text, found_source_signature


def add_own_signature(
    text,
    should_add_signature
):
    if not should_add_signature:
        return text

    return (
        f"{text}\n\n{OWN_SIGNATURE}"
        if text
        else OWN_SIGNATURE
    )


# ============================================================
# 🌐 ترجمة النص من الإنجليزي إلى العربي
# ============================================================
# القناة المصدر تنشر بالإنجليزي بالكامل، وقناتك عربية، فهذي الدالة
# تترجم النص قبل ما يترسل. تشتغل بعد clean_text() (يعني بعد ما ينحذف
# توقيع المصدر) وقبل إضافة توقيعك الخاص.
#
# لو فشلت الترجمة لأي سبب (مشكلة شبكة، خدمة الترجمة متعطلة، إلخ)،
# ترجع النص الأصلي الإنجليزي بدون ما توقف البوت، وتسجل الفشل باللوق.

TRANSLATE_ENABLED = True   # 🔧 خليها False لو تبي توقف الترجمة مؤقتاً

TRANSLATE_MAX_RETRIES = 2

TRANSLATE_RETRY_DELAY = 3  # ثواني


def translate_to_arabic(
    text,
    post_id=None
):
    if (
        not TRANSLATE_ENABLED
        or not text
        or not text.strip()
    ):
        return text

    last_error = None

    for attempt in range(
        1,
        TRANSLATE_MAX_RETRIES + 1
    ):
        try:
            translated = GoogleTranslator(
                source="en",
                target="ar"
            ).translate(text)

            if translated:
                log(
                    f"🌐 تم ترجمة المنشور {post_id} "
                    f"من الإنجليزي للعربي"
                )

                return translated

            last_error = Exception(
                "رجعت الترجمة فارغة"
            )

        except Exception as e:
            last_error = e

            log(
                f"⚠️ محاولة {attempt}/{TRANSLATE_MAX_RETRIES} "
                f"فشلت (ترجمة المنشور {post_id}): {e}"
            )

            if attempt < TRANSLATE_MAX_RETRIES:
                time.sleep(
                    TRANSLATE_RETRY_DELAY
                )

    log(
        f"⚠️ فشلت ترجمة المنشور {post_id} "
        f"- راح يترسل بالإنجليزي كما هو ({last_error})"
    )

    return text


IMAGE_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://t.me/",
}


def download_image(url):
    max_retries = 2
    last_error = None

    for attempt in range(
        1,
        max_retries + 1
    ):
        try:
            r = requests.get(
                url,
                headers=IMAGE_REQUEST_HEADERS,
                timeout=20
            )

            r.raise_for_status()

            return r.content

        except Exception as e:
            last_error = e

            log(
                f"⚠️ محاولة {attempt}/{max_retries} "
                f"فشلت (تحميل صورة): {e}"
            )

            if attempt < max_retries:
                time.sleep(3)

    raise last_error


def post_with_retry(
    url,
    **kwargs
):
    max_retries = 3

    r = None

    for attempt in range(
        1,
        max_retries + 1
    ):
        r = requests.post(
            url,
            **kwargs
        )

        if r.status_code != 429:
            return r

        try:
            retry_after = (
                r.json()
                .get(
                    "parameters",
                    {}
                )
                .get(
                    "retry_after",
                    5
                )
            )

        except Exception:
            retry_after = 5

        log(
            f"⏳ تيليجرام طلب الانتظار "
            f"{retry_after} ثانية قبل إعادة المحاولة "
            f"(429 - طلبات كثيرة) - "
            f"محاولة {attempt}/{max_retries}"
        )

        time.sleep(
            retry_after + 1
        )

    return r


def _extract_message_id(
    response,
    first_of_list=False
):
    if not response.ok:
        return None

    try:
        data = response.json()

        if not data.get("ok"):
            return None

        result = data["result"]

        if first_of_list:
            return result[0]["message_id"]

        return result["message_id"]

    except Exception:
        return None


def send_text(
    text,
    reply_to_message_id=None
):
    if not text:
        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": DEST_CHANNEL,
        "text": text
    }

    if BOLD_TEXT:
        payload["text"] = make_bold_html(text)
        payload["parse_mode"] = "HTML"

    if reply_to_message_id:
        payload["reply_to_message_id"] = (
            reply_to_message_id
        )

    r = post_with_retry(
        url,
        data=payload,
        timeout=15
    )

    log(
        f"   ↳ رد تيليجرام (نص): "
        f"{r.status_code} {r.text[:200]}"
    )

    return _extract_message_id(r)


def send_single_photo(
    photo_url,
    caption="",
    reply_to_message_id=None
):
    try:
        image_bytes = download_image(
            photo_url
        )

    except Exception as e:
        log(
            f"   ↳ فشل تحميل الصورة: {e}"
        )

        return None

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendPhoto"
    )

    payload = {
        "chat_id": DEST_CHANNEL
    }

    if caption:
        cap = caption[
            :TELEGRAM_CAPTION_LIMIT
        ]

        if BOLD_TEXT:
            payload["caption"] = (
                make_bold_html(cap)
            )
            payload["parse_mode"] = "HTML"

        else:
            payload["caption"] = cap

    if reply_to_message_id:
        payload["reply_to_message_id"] = (
            reply_to_message_id
        )

    files = {
        "photo": (
            "image.jpg",
            image_bytes
        )
    }

    r = post_with_retry(
        url,
        data=payload,
        files=files,
        timeout=30
    )

    log(
        f"   ↳ رد تيليجرام (صورة): "
        f"{r.status_code} {r.text[:200]}"
    )

    return _extract_message_id(r)


def send_media_group(
    photo_urls,
    caption="",
    reply_to_message_id=None
):
    files = {}
    media = []

    for i, photo_url in enumerate(
        photo_urls
    ):
        try:
            image_bytes = download_image(
                photo_url
            )

        except Exception as e:
            log(
                f"   ↳ فشل تحميل صورة رقم "
                f"{i + 1}: {e}"
            )

            continue

        field_name = f"photo{i}"

        files[field_name] = (
            f"image{i}.jpg",
            image_bytes
        )

        item = {
            "type": "photo",
            "media": f"attach://{field_name}"
        }

        if i == 0 and caption:
            cap = caption[
                :TELEGRAM_CAPTION_LIMIT
            ]

            if BOLD_TEXT:
                item["caption"] = (
                    make_bold_html(cap)
                )

                item["parse_mode"] = "HTML"

            else:
                item["caption"] = cap

        media.append(item)

    if not media:
        return None

    data_payload = {
        "chat_id": DEST_CHANNEL,
        "media": json.dumps(media)
    }

    if reply_to_message_id:
        data_payload[
            "reply_to_message_id"
        ] = reply_to_message_id

    api_url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMediaGroup"
    )

    r = post_with_retry(
        api_url,
        data=data_payload,
        files=files,
        timeout=40
    )

    log(
        f"   ↳ رد تيليجرام (ألبوم): "
        f"{r.status_code} {r.text[:200]}"
    )

    return _extract_message_id(
        r,
        first_of_list=True
    )


def send_post(
    text,
    photo_urls,
    post_id,
    reply_to_message_id=None
):
    if not photo_urls:
        msg_id = send_text(
            text,
            reply_to_message_id=reply_to_message_id
        )

        if msg_id:
            log(
                f"📤 تم إرسال المنشور {post_id} "
                f"كنص فقط (بدون صورة)"
            )

        return msg_id

    caption_fits = (
        len(text)
        <= (
            TELEGRAM_CAPTION_LIMIT - 20
        )
    )

    caption = (
        text
        if caption_fits
        else ""
    )

    if len(photo_urls) == 1:
        msg_id = send_single_photo(
            photo_urls[0],
            caption=caption,
            reply_to_message_id=reply_to_message_id
        )

        photo_desc = "مع صورة واحدة"

    else:
        msg_id = send_media_group(
            photo_urls,
            caption=caption,
            reply_to_message_id=reply_to_message_id
        )

        photo_desc = (
            f"مع ألبوم من {len(photo_urls)} صور"
        )

    if msg_id:
        extra = (
            ""
            if caption_fits
            else " (والنص الكامل أُرسل برسالة منفصلة لطوله)"
        )

        log(
            f"📤 تم إرسال المنشور {post_id} "
            f"{photo_desc}{extra}"
        )

        if text and not caption_fits:
            send_text(text)

    else:
        log(
            f"⚠️ فشل إرسال صورة المنشور {post_id}، "
            f"تم إرسال النص فقط بدلاً منها"
        )

        msg_id = send_text(
            text,
            reply_to_message_id=reply_to_message_id
        )

    return msg_id


def run_once():
    try:
        log("=" * 50)

        log(
            "🤖 البوت اشتغل - يتحقق من منشورات جديدة..."
        )

        last_seen_id = load_last_seen_id()

        if last_seen_id is not None:
            log(
                f"📍 يراقب من المنشور رقم: "
                f"{last_seen_id}"
            )

        else:
            log(
                "📍 هذي أول تشغيلة - "
                "ما فيه نقطة بداية محفوظة بعد"
            )

        try:
            posts = get_channel_posts()

        except Exception as e:
            log(
                f"❌ صار خطأ بجلب المنشورات: {e}"
            )

            return

        if not posts:
            log(
                "ℹ️ ما فيه منشورات بالصفحة حالياً."
            )

            return

        latest_id = posts[-1]["id"]

        if last_seen_id is None:
            save_last_seen_id(
                latest_id
            )

            log(
                f"✅ بدأنا المراقبة من المنشور رقم: "
                f"{latest_id} "
                f"(بدون إرسال منشورات قديمة)"
            )

            return

        new_posts = [
            p
            for p in posts
            if p["id"] > last_seen_id
        ]

        if not new_posts:
            log(
                "ℹ️ ما فيه منشورات جديدة هالمرة."
            )

            return

        log(
            f"📊 عدد المنشورات الجديدة: "
            f"{len(new_posts)}"
        )

        id_map = load_id_map()

        for post in new_posts:
            try:
                log(
                    f"🔄 معالجة المنشور رقم "
                    f"{post['id']}..."
                )

                clean, should_add_signature = (
                    clean_text(
                        post["text_html"]
                    )
                )

                log(
                    f"✏️ تم تنظيف نص المنشور "
                    f"{post['id']} "
                    f"(حذف توقيع المصدر إن وجد)"
                )

                # ترجمة النص من الإنجليزي للعربي
                # (القناة المصدر إنجليزية بالكامل)
                #
                # مهم: هذا يصير قبل إضافة توقيعك، عشان توقيعك
                # (العربي أصلاً) ما يرسل لخدمة الترجمة ويتكسر
                # رابطه أو تنسيقه

                clean = translate_to_arabic(
                    clean,
                    post_id=post["id"]
                )

                # إضافة توقيعك الخاص بعد الترجمة مباشرة
                # (لو المصدر سوى توقيعه)

                clean = add_own_signature(
                    clean,
                    should_add_signature
                )

                # لو المنشور رد على منشور ثاني،
                # نشوف هل عندنا "نسخته" بقناتك
                # بدفتر المطابقة.
                #
                # لو عندنا، نرد عليه بنفس الشكل.

                reply_to_dest_id = None

                if post.get("reply_to"):
                    reply_to_dest_id = id_map.get(
                        str(post["reply_to"])
                    )

                    if reply_to_dest_id:
                        log(
                            f"↩️ المنشور {post['id']} "
                            f"رد على المنشور "
                            f"{post['reply_to']} "
                            f"(بقناتك: "
                            f"{reply_to_dest_id})"
                        )

                    else:
                        log(
                            f"↩️ المنشور {post['id']} "
                            f"رد على منشور "
                            f"{post['reply_to']} "
                            f"مو موجود بدفتر المطابقة - "
                            f"بيترسل عادي بدون رد"
                        )

                # مهم: هذا السطر لازم يشتغل لكل منشور
                # (رد أو عادي)، مو بس جوا حالة
                # "رد بدون مطابقة" - هذا كان سبب توقف
                # الإرسال بالمنشورات العادية بالنسخة السابقة

                dest_msg_id = send_post(
                    clean,
                    post["photos"],
                    post["id"],
                    reply_to_message_id=reply_to_dest_id
                )

                if dest_msg_id:
                    id_map[
                        str(post["id"])
                    ] = dest_msg_id

                    if reply_to_dest_id:
                        log(
                            f"↩️ تأكيد: تم إرسال المنشور "
                            f"{post['id']} كرد حقيقي على "
                            f"رسالتك رقم "
                            f"{reply_to_dest_id} "
                            f"بقناتك ✅"
                        )

                log(
                    f"✅ تم نسخ المنشور "
                    f"{post['id']} بنجاح"
                )

            except Exception as post_err:
                log(
                    f"❌ فشل إرسال المنشور "
                    f"{post['id']}: "
                    f"{post_err}"
                )

            finally:
                save_last_seen_id(
                    post["id"]
                )

                id_map = save_id_map(
                    id_map
                )

    finally:
        flush_log()


if __name__ == "__main__":
    run_once()
