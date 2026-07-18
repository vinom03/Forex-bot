import os
import re
import json
import time
from html import unescape

import requests
from bs4 import BeautifulSoup

# التوكن الآن يُقرأ من متغير بيئة (GitHub Secret) بدل ما يكون مكتوب هنا مباشرة 
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "خطأ: لازم تحط توكن البوت بمتغير بيئة اسمه BOT_TOKEN (عن طريق GitHub Secrets)."
    )

DEST_CHANNEL = "@ForexGold_Pro"
SOURCE_CHANNEL = "ForexBreakingNews"

TELEGRAM_CAPTION_LIMIT = 1024  # حد تيليجرام لطول الكابشن مع الصور

# آخر منشور تمت معالجته يُحفظ بملف داخل المستودع نفسه، عشان يفضل محفوظ
# بين كل تشغيلة وتشغيلة (GitHub Actions ما يحتفظ بالذاكرة بين التشغيلات)
LAST_SEEN_FILE = "last_seen_id.txt"


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
# جلب المنشورات (يحل مشكلة 2 و 3: التنسيق + النقص)
# ============================================================
def get_channel_posts():
    """
    يرجع لستة منشورات، كل منشور dict فيه:
      id         -> رقم المنشور
      text_html  -> النص كـ HTML خام (قبل التنظيف)
      photos     -> لستة روابط الصور (تدعم منشور بصورة وحدة أو ألبوم كامل)

    نستخدم BeautifulSoup بدل الريجكس القديم لأن:
    - كل منشور يُقرأ من "صندوقه" الخاص (data-post) بدون تداخل مع منشورات
      أو عناصر أخرى في الصفحة (هذا كان سبب ظهور كلمات غريبة مثل "pinned").
    - نلتقط محتوى الـ div كامل بدل التوقف عند أول </div> داخلي، فما ينقطع
      النص ولا تنكسر هيكلته (أرقام، أسطر، فقرات).
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
            break  # نجحت المحاولة -> نطلع من اللوب
        except Exception as e:
            last_error = e
            print(f"محاولة {attempt}/{max_retries} فشلت (جلب المنشورات):", e)
            if attempt < max_retries:
                time.sleep(retry_delay)

    if response is None:
        # كل المحاولات فشلت -> نرفع الخطأ عشان الطبقة اللي فوق تمسكه
        raise last_error

    soup = BeautifulSoup(response.text, "html.parser")

    posts = []
    for msg_div in soup.find_all("div", class_="tgme_widget_message", attrs={"data-post": True}):
        try:
            post_id = int(msg_div["data-post"].split("/")[-1])
        except (KeyError, ValueError):
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
OWN_SIGNATURE = "📢 قناة ForexGold Pro || اشترك الآن:\nhttps://t.me/ForexGold_Pro"


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
    signature_line = re.compile(r'قناة\s*أ?خبار\s*الفوركس\s*العاجلة')
    link_line = re.compile(r'(t\.me|telegram\.me)/', re.IGNORECASE)

    lines = text.split('\n')
    while lines:
        last = lines[-1].strip()
        if last == '' or signature_line.search(last) or link_line.search(last):
            lines.pop()
            continue
        break  # وصلنا لسطر محتوى حقيقي -> نوقف الحذف فوراً

    text = '\n'.join(lines).strip()

    # إضافة توقيع قناتك في نهاية كل منشور
    text = f"{text}\n\n{OWN_SIGNATURE}" if text else OWN_SIGNATURE

    return text


# ============================================================
# الإرسال إلى تيليجرام (يحل مشكلة 1: الصور)
# ============================================================
def download_image(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content


def send_text(text):
    if not text:
        return True
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": DEST_CHANNEL, "text": text}, timeout=15)
    print("إرسال نص:", r.status_code, r.text[:200])
    return r.ok and r.json().get("ok", False)


def send_single_photo(photo_url, caption=""):
    # نحمّل الصورة بنفسنا ونرفعها مباشرة (upload) بدل ما نعطي تيليجرام
    # الرابط ويحاول يجيبه بنفسه -> هذا كان سبب خطأ 400 "failed to get HTTP URL content"
    try:
        image_bytes = download_image(photo_url)
    except Exception as e:
        print("فشل تحميل الصورة:", e)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    payload = {"chat_id": DEST_CHANNEL}
    if caption:
        payload["caption"] = caption[:TELEGRAM_CAPTION_LIMIT]
    files = {"photo": ("image.jpg", image_bytes)}
    r = requests.post(url, data=payload, files=files, timeout=30)
    print("إرسال صورة:", r.status_code, r.text[:200])
    return r.ok and r.json().get("ok", False)


def send_media_group(photo_urls, caption=""):
    files = {}
    media = []
    for i, photo_url in enumerate(photo_urls):
        try:
            image_bytes = download_image(photo_url)
        except Exception as e:
            print(f"فشل تحميل صورة رقم {i + 1}:", e)
            continue
        field_name = f"photo{i}"
        files[field_name] = (f"image{i}.jpg", image_bytes)
        item = {"type": "photo", "media": f"attach://{field_name}"}
        if i == 0 and caption:
            item["caption"] = caption[:TELEGRAM_CAPTION_LIMIT]
        media.append(item)

    if not media:
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup"
    r = requests.post(api_url, data={"chat_id": DEST_CHANNEL, "media": json.dumps(media)}, files=files, timeout=40)
    print("إرسال ألبوم:", r.status_code, r.text[:200])
    return r.ok and r.json().get("ok", False)


def send_post(text, photo_urls):
    """
    يقرر طريقة الإرسال حسب محتوى المنشور:
      - نص فقط              -> sendMessage
      - صورة وحدة            -> رفع الصورة مباشرة (مع الكابشن إذا كان قصير بما فيه الكفاية)
      - أكثر من صورة (ألبوم) -> رفع كل الصور مباشرة كألبوم
      - إذا فشل رفع الصورة، أو كان النص أطول من حد الكابشن (1024 حرف):
        يُرسل النص كاملاً كرسالة منفصلة عشان المحتوى ما يضيع بأي حال.
    """
    if not photo_urls:
        send_text(text)
        return

    caption_fits = len(text) <= TELEGRAM_CAPTION_LIMIT
    caption = text if caption_fits else ""

    if len(photo_urls) == 1:
        photo_sent = send_single_photo(photo_urls[0], caption=caption)
    else:
        photo_sent = send_media_group(photo_urls, caption=caption)

    # نرسل النص كرسالة منفصلة إذا: الصورة فشلت بالكامل، أو نجحت لكن بدون كابشن (طويل)
    if text and (not photo_sent or not caption_fits):
        send_text(text)


# ============================================================
# تشغيلة واحدة (بدل الحلقة اللانهائية) — مناسبة لـ GitHub Actions
# اللي يتكفل بتكرار التشغيل كل 5 دقائق عن طريق جدولة (cron) خارجية
# ============================================================
def run_once():
    print("البوت اشتغل - يتحقق من منشورات جديدة...")

    last_seen_id = load_last_seen_id()

    try:
        posts = get_channel_posts()
    except Exception as e:
        print("صار خطأ بجلب المنشورات:", e)
        return

    if not posts:
        print("ما فيه منشورات بالصفحة حالياً.")
        return

    latest_id = posts[-1]["id"]

    if last_seen_id is None:
        # أول تشغيلة على الإطلاق -> نحفظ نقطة البداية بدون إرسال شي
        save_last_seen_id(latest_id)
        print("بدأنا المراقبة من المنشور رقم:", latest_id)
        return

    for post in posts:
        if post["id"] > last_seen_id:
            try:
                clean = clean_text(post["text_html"])
                send_post(clean, post["photos"])
                print("تم نسخ منشور جديد بنجاح ✅", post["id"])
            except Exception as post_err:
                # خطأ بمنشور واحد بس -> نطبعه ونكمل، وما نوقف الدفعة كلها
                print(f"فشل إرسال المنشور {post['id']}:", post_err)
            finally:
                # نحدّث آخر منشور تمت معالجته بعد كل منشور (نجح أو فشل) عشان
                # ما نرجع نرسل نفس المنشور مرة ثانية بالتشغيلة الجاية
                save_last_seen_id(post["id"])


if __name__ == "__main__":
    run_once()
