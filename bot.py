#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Родительский навигатор – бот для публикации постов в родительской группе
Основан на рабочем коде AI Навигатора.
Адаптирован для публичной страницы (ID 197687739).
Использует улучшенную загрузку фото через прямой HTTP (работает с любым owner_id).
"""

import os
import io
import json
import time
import logging
import random
import re
import requests
import threading
import feedparser
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import vk_api
from vk_api.upload import VkUpload

load_dotenv()

# ========== НАСТРОЙКИ (РОДИТЕЛЬСКИЕ) ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")               # Токен родительского бота
VK_TOKEN_AI = os.getenv("VK_TOKEN_AI")                     # Новый токен для группы (права wall, photos)
GROUP_ID_AI = int(os.getenv("GROUP_ID_AI", "197687739"))   # Публичная страница (положительный ID)
VK_TOKEN_USER = os.getenv("VK_TOKEN_USER")                 # Токен для личной стены (можно тот же)
VK_USER_ID = int(os.getenv("VK_USER_ID", "317272476"))     # Ваш ID из нового токена

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai")
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT", "ugly, deformed, blurry, low quality")

# RSS-источники для родительского бота (замените при необходимости)
RSS_SOURCES_JSON = os.getenv("RSS_SOURCES", '[]')
POST_TIMES_JSON = os.getenv("POST_TIMES", '["07:00","11:00","13:00","18:00"]')
RSS_DEFAULT_GROUP = os.getenv("RSS_DEFAULT_GROUP", "Родительский навигатор")
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "rss_state.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ========== TELEGRAM ==========
def send_message(chat_id, text):
    url = f"{BASE_URL}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def send_photo(chat_id, photo_bytes, caption=""):
    url = f"{BASE_URL}/sendPhoto"
    files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}
    requests.post(url, data={"chat_id": chat_id, "caption": caption}, files=files)

def get_updates(offset=None):
    url = f"{BASE_URL}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    resp = requests.get(url, params=params)
    return resp.json().get("result", [])

# ========== ГЕНЕРАЦИЯ ТЕКСТА (АДАПТИРОВАНА ПОД РОДИТЕЛЬСКУЮ ТЕМАТИКУ) ==========
def generate_text(topic: str) -> str:
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            data = {
                "model": "agnes-v1",
                "messages": [{"role": "user", "content": f"Напиши полезный пост для родителей на тему: {topic}. Пост должен быть практичным, с советами и примерами. Объём около 200 слов. Пиши в дружелюбном, поддерживающем тоне."}],
                "max_tokens": 400,
                "temperature": 0.7
            }
            resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if text and len(text) > 50:
                    return text.strip()
        except Exception as e:
            logger.warning(f"Agnes (текст) не сработал: {e}")

    return generate_template_text(topic)

def generate_template_text(topic: str) -> str:
    intro_phrases = [
        f"Родительство – это постоянное развитие. Сегодня поговорим о том, как {topic} может помочь вашему ребёнку.",
        f"Каждый родитель хочет дать своему ребёнку лучшее. Давайте разберёмся, как {topic} влияет на воспитание и развитие.",
        f"В современном мире вопросы воспитания становятся всё сложнее. Тема {topic} – одна из самых актуальных."
    ]
    body_phrases = [
        "Психологи рекомендуют начинать с малого – ежедневные беседы, совместное чтение, обсуждение чувств.",
        "Важно создать безопасную среду, где ребёнок может выражать свои эмоции и задавать вопросы.",
        "Используйте игровые формы обучения – через игру дети лучше усваивают информацию.",
        "Не забывайте хвалить ребёнка за старания, а не только за результат – это формирует здоровую самооценку.",
        "Пример родителей – лучший урок. Показывайте своим поведением, как важно быть честным, ответственным и добрым.",
        "Общайтесь с учителями, участвуйте в школьной жизни – это помогает быть в курсе успехов и проблем."
    ]
    conclusion_phrases = [
        "Помните, что каждый ребёнок уникален. Ищите подход, который работает именно для вашей семьи.",
        "Главное в воспитании – любовь и терпение. С вашей поддержкой ребёнок справится с любыми трудностями.",
        "Следите за обновлениями в нашем сообществе, чтобы узнавать ещё больше полезных советов!"
    ]
    intro = random.choice(intro_phrases)
    body = random.sample(body_phrases, k=3)
    conclusion = random.choice(conclusion_phrases)
    return f"{intro}\n\n{' '.join(body)}\n\n{conclusion}"

# ========== ГЕНЕРАТОРЫ КАРТИНОК (БЕЗ ИЗМЕНЕНИЙ) ==========
def random_seed():
    return random.randint(1, 1000000)

def generate_agnes(prompt):
    if not AGNES_API_KEY:
        return None
    try:
        seed = random_seed()
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        extra_words = ["innovative", "modern", "futuristic", "creative", "dynamic", "bright", "vibrant"]
        extra = random.choice(extra_words)
        full_prompt = f"Professional business and technology illustration about {prompt}. Include AI, neural networks, charts, modern office. {extra} style. No people, no nature."
        data = {
            "prompt": full_prompt,
            "negative_prompt": "ugly, deformed, blurry, nature, trees, forest, landscape, people",
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 30,
            "guidance_scale": 7.0,
            "seed": seed
        }
        resp = requests.post("https://apihub.agnes-ai.cn/v1/images/generations", json=data, headers=headers, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            image_url = result.get("data", [{}])[0].get("url")
            if image_url:
                img_resp = requests.get(image_url, timeout=30)
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as e:
        logger.warning(f"Agnes не сработал: {e}")
    return None

def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    queries = [
        f"artificial intelligence business {topic}",
        f"AI technology {topic}",
        f"neural network business {topic}",
        f"machine learning {topic}",
        f"AI startup modern office {topic}",
        f"digital transformation technology {topic}",
        f"business technology innovation {topic}",
        f"future technology {topic}",
        f"AI concept {topic}",
        f"technology innovation {topic}",
        f"modern business technology {topic}",
        f"AI visualization {topic}"
    ]
    random.shuffle(queries)
    for query in queries[:3]:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        page = random.randint(1, 3)
        params = {"query": query, "per_page": 5, "page": page}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    photo = random.choice(photos)
                    photo_url = photo["src"]["large2x"]
                    logger.info(f"Pexels: запрос '{query}', страница {page}")
                    return photo_url
        except Exception as e:
            logger.warning(f"Pexels ошибка: {e}")
    return None

def download_photo(url):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except:
        pass
    return None

def generate_pixazo(prompt):
    if not PIXAZO_API_KEY:
        return None
    try:
        seed = random_seed()
        url = "https://api.pixazo.com/v1/generate"
        headers = {
            "Authorization": f"Bearer {PIXAZO_API_KEY}",
            "Content-Type": "application/json"
        }
        extra_words = ["innovative", "modern", "futuristic", "creative", "dynamic", "bright", "vibrant"]
        extra = random.choice(extra_words)
        full_prompt = f"Professional business and technology illustration about {prompt}. Include AI, neural networks, charts, modern office. {extra} style. No people, no nature."
        data = {
            "prompt": full_prompt,
            "model": "flux",
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 30,
            "guidance_scale": 7.0,
            "seed": seed
        }
        resp = requests.post(url, json=data, headers=headers, timeout=60)
        if resp.status_code == 200:
            result = resp.json()
            image_url = result.get("image_url")
            if image_url:
                img_resp = requests.get(image_url, timeout=30)
                if img_resp.status_code == 200:
                    return img_resp.content
    except Exception as e:
        logger.warning(f"Pixazo не сработал: {e}")
    return None

def generate_pollinations(prompt):
    try:
        seed = random_seed()
        extra_words = ["innovative", "modern", "futuristic", "creative", "dynamic", "bright", "vibrant"]
        extra = random.choice(extra_words)
        full_prompt = f"{prompt} {extra} technology business illustration"
        url = f"{POLLINATIONS_BASE_URL}/prompt/{requests.utils.quote(full_prompt)}?width=1024&height=1024&nologo=true&seed={seed}"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.warning(f"Pollinations не сработал: {e}")
    return None

def create_banner(text, width=1024, height=1024):
    img = Image.new('RGB', (width, height), color='#0a0a2e')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
    except:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2
    draw.text((x, y), text, fill='#FFD700', font=font)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def generate_image(topic):
    generators = []
    if AGNES_API_KEY:
        generators.append(("Agnes", generate_agnes))
    if PEXELS_API_KEY:
        generators.append(("Pexels", search_pexels_relevant_photo))
    if PIXAZO_API_KEY:
        generators.append(("Pixazo", generate_pixazo))
    generators.append(("Pollinations", generate_pollinations))

    skip_pexels = False
    if len(generators) > 1 and PEXELS_API_KEY:
        skip_pexels = random.random() < 0.5

    random.shuffle(generators)

    image_bytes = None
    sources = []

    for name, func in generators:
        if name == "Pexels" and skip_pexels:
            logger.info("Pexels пропущен случайным образом")
            continue

        try:
            if name == "Pexels":
                photo_url = func(topic)
                if photo_url:
                    img = download_photo(photo_url)
                    if img:
                        image_bytes = img
                        sources.append(name)
                        logger.info(f"✅ Картинка от {name}")
                        break
            else:
                img = func(topic)
                if img:
                    image_bytes = img
                    sources.append(name)
                    logger.info(f"✅ Картинка от {name}")
                    break
        except Exception as e:
            logger.warning(f"{name} не сработал: {e}")

    if not image_bytes:
        image_bytes = create_banner(topic[:20])
        sources.append("баннер")
        logger.info("✅ Использован баннер")

    logger.info(f"Источник картинки: {', '.join(sources)}")
    return image_bytes

# ========== VK ПУБЛИКАЦИЯ (УЛУЧШЕННАЯ ЗАГРУЗКА ЧЕРЕЗ ПРЯМОЙ HTTP) ==========
def upload_photo_to_vk_via_http(image_bytes, owner_id, token):
    """
    Загружает фото на стену (группы или публичной страницы) через прямой HTTP.
    Работает для любого owner_id (отрицательного для группы, положительного для публичной страницы).
    Возвращает attachment в формате 'photo{owner_id}_{id}'.
    """
    try:
        vk = vk_api.VkApi(token=token)
        # 1. Получаем URL для загрузки
        if owner_id < 0:
            group_id = abs(owner_id)
            upload_url = vk.method('photos.getWallUploadServer', {'group_id': group_id})['upload_url']
            logger.info(f"Загрузка в группу (group_id={group_id})")
        else:
            upload_url = vk.method('photos.getWallUploadServer', {})['upload_url']
            logger.info(f"Загрузка на публичную страницу (owner_id={owner_id})")

        # 2. Загружаем фото на сервер
        files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
        resp = requests.post(upload_url, files=files)
        resp.raise_for_status()
        upload_data = resp.json()

        # 3. Сохраняем фото на стену
        save_params = {
            'photo': upload_data['photo'],
            'server': upload_data['server'],
            'hash': upload_data['hash']
        }
        if owner_id < 0:
            save_params['group_id'] = abs(owner_id)
        saved = vk.method('photos.saveWallPhoto', save_params)

        # 4. Формируем attachment
        photo = saved[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
        logger.info(f"Получен attachment: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        raise

def publish_to_group(text, image_bytes):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk_via_http(image_bytes, GROUP_ID_AI, VK_TOKEN_AI)
                attachments.append(attachment)
                logger.info("Фото загружено, attachment получен")
            except Exception as e:
                logger.error(f"Ошибка загрузки фото в группу: {e}")
                return f"❌ Ошибка загрузки фото: {e}"

        wall_api = "https://api.vk.com/method/wall.post"
        params = {
            "access_token": VK_TOKEN_AI,
            "owner_id": GROUP_ID_AI,
            "message": text,
            "v": "5.131"
        }
        if GROUP_ID_AI < 0:
            params["from_group"] = 1
        if attachments:
            params["attachments"] = ",".join(attachments)

        logger.info(f"Параметры wall.post: owner_id={GROUP_ID_AI}, attachments={attachments}")
        resp = requests.get(wall_api, params=params).json()
        logger.info(f"Ответ wall.post: {json.dumps(resp, indent=2)}")

        if "error" in resp:
            return f"❌ Ошибка VK (группа): {resp['error']['error_msg']}"
        return f"✅ Пост в группе опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации в группу: {e}")
        return f"❌ Ошибка: {e}"

def publish_announce_to_user(announce_text, image_bytes, group_link):
    if not VK_TOKEN_USER or not VK_USER_ID:
        return "❌ Нет токена или ID для личной страницы"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk_via_http(image_bytes, VK_USER_ID, VK_TOKEN_USER)
                attachments.append(attachment)
                logger.info("Фото загружено на личную стену")
            except Exception as e:
                logger.error(f"Ошибка загрузки фото на личную стену: {e}")
                return f"❌ Ошибка загрузки фото: {e}"
        full_text = announce_text + f"\n\n👉 {group_link}" if group_link else announce_text
        wall_api = "https://api.vk.com/method/wall.post"
        params = {
            "access_token": VK_TOKEN_USER,
            "owner_id": VK_USER_ID,
            "message": full_text,
            "v": "5.131"
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        resp = requests.get(wall_api, params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK (личная): {resp['error']['error_msg']}"
        return f"✅ Анонс на личной стене опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации на личную стену: {e}")
        return f"❌ Ошибка: {e}"

def create_post(topic, custom_text=None):
    if custom_text and len(custom_text) > 50:
        post_text = custom_text
    else:
        post_text = generate_text(topic)
    announce_text = f"🔥 Новый пост в группе {RSS_DEFAULT_GROUP}: {topic}"
    group_link = f"https://vk.com/club{abs(GROUP_ID_AI)}" if GROUP_ID_AI < 0 else f"https://vk.com/public{GROUP_ID_AI}"
    image_bytes = generate_image(topic)
    return post_text, announce_text, group_link, image_bytes

def publish_post(topic, custom_text=None):
    post_text, announce_text, group_link, image_bytes = create_post(topic, custom_text)
    group_result = publish_to_group(post_text, image_bytes)
    user_result = publish_announce_to_user(announce_text, image_bytes, group_link)
    return {"group": group_result, "user": user_result}

# ========== RSS ПЛАНИРОВЩИК ==========
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def get_rss_entries(sources_json):
    sources = json.loads(sources_json)
    entries = []
    for src in sources:
        url = src.get("url")
        if not url:
            continue
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                if title:
                    entries.append(title)
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS {url}: {e}")
    return entries

def rss_scheduler():
    logger.info("📡 RSS-планировщик запущен")
    post_times = json.loads(POST_TIMES_JSON)
    times = [datetime.strptime(t, "%H:%M").time() for t in post_times]
    state = load_state()
    last_date = state.get("last_date", "")
    published_titles = set(state.get("published_titles", []))

    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            current_time = now.time()

            for t in times:
                diff = (now.replace(hour=t.hour, minute=t.minute, second=0) - now).total_seconds()
                if 0 <= diff < 300:
                    key = f"{today}_{t.hour:02d}:{t.minute:02d}"
                    if key not in state.get("published_keys", []):
                        titles = get_rss_entries(RSS_SOURCES_JSON)
                        if not titles:
                            logger.warning("Нет заголовков из RSS")
                            continue
                        available = [title for title in titles if title not in published_titles]
                        if not available:
                            logger.warning("Нет новых заголовков для публикации")
                            published_titles.clear()
                            available = titles
                        topic = random.choice(available)
                        logger.info(f"⏰ Автоматическая публикация в {t.hour:02d}:{t.minute:02d}: {topic}")
                        result = publish_post(topic)
                        logger.info(f"Результат: {result}")
                        published_titles.add(topic)
                        state["published_titles"] = list(published_titles)
                        if "published_keys" not in state:
                            state["published_keys"] = []
                        state["published_keys"].append(key)
                        state["last_date"] = today
                        save_state(state)

            if state.get("last_date") != today:
                state["published_keys"] = []
                state["last_date"] = today
                save_state(state)

            time.sleep(60)
        except Exception as e:
            logger.error(f"Ошибка в RSS-планировщике: {e}")
            time.sleep(60)

# ========== ОБРАБОТЧИК КОМАНД ==========
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "👋 Привет! Я бот «Родительский навигатор».\n"
            "Помогаю публиковать полезные посты для родителей.\n\n"
            "📌 Команды:\n"
            "/post <тема> — сгенерировать и опубликовать пост\n"
            "/post <текст поста (от 50 символов)> — опубликовать готовый текст с картинкой\n"
            "/ping — проверить работу бота"
        )
        return

    if text == "/ping":
        send_message(chat_id, "🏓 Pong! Бот работает")
        return

    if text.startswith("/post"):
        content = text.replace("/post", "").strip()
        if not content:
            send_message(chat_id, "❌ Укажите тему или текст поста.")
            return

        if len(content) > 50:
            custom_text = content
            topic = content[:50] + "..."
            send_message(chat_id, f"⏳ Публикую готовый пост...")
        else:
            custom_text = None
            topic = content
            send_message(chat_id, f"⏳ Генерирую пост на тему: {topic}...")

        result = publish_post(topic, custom_text)
        send_message(chat_id, f"📌 Группа:\n{result['group']}")
        send_message(chat_id, f"👤 Анонс:\n{result['user']}")
        return

    send_message(chat_id, "❓ Неизвестная команда. Напишите /help")

# ========== ЗАПУСК ==========
def main():
    logger.info("🚀 Бот запущен")

    scheduler_thread = threading.Thread(target=rss_scheduler, daemon=True)
    scheduler_thread.start()

    last_update_id = 0
    while True:
        try:
            updates = get_updates(offset=last_update_id + 1)
            if updates:
                for update in updates:
                    last_update_id = update["update_id"]
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        if "text" in msg:
                            handle_command(chat_id, msg["text"].strip())
            time.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()