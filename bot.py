#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот с генерацией текста, картинок, RSS-планировщиком
Использует альтернативный эндпоинт Agnes (apihub.agnes-ai.cn)
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

load_dotenv()

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VK_TOKEN_AI = os.getenv("VK_TOKEN_AI")
GROUP_ID_AI = int(os.getenv("GROUP_ID_AI", "0"))
VK_TOKEN_USER = os.getenv("VK_TOKEN_USER")
VK_USER_ID = int(os.getenv("VK_USER_ID", "0"))
AGNES_API_KEY = os.getenv("AGNES_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai")
RSS_SOURCES_JSON = os.getenv("RSS_SOURCES", '[]')
POST_TIMES_JSON = os.getenv("POST_TIMES", '["07:00","11:00","13:00","18:00"]')
RSS_DEFAULT_GROUP = os.getenv("RSS_DEFAULT_GROUP", "AI Навигатор")
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

# ========== ГЕНЕРАЦИЯ ТЕКСТА ==========
def generate_text(topic: str) -> str:
    """Генерирует текст поста (≈200 слов) через Agnes (альтернативный эндпоинт) или шаблон."""
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            data = {
                "model": "agnes-v1",
                "messages": [{"role": "user", "content": f"Напиши развернутый пост (около 200 слов) на тему: {topic}. Используй факты, примеры, выводы. Пиши в деловом, но доступном стиле."}],
                "max_tokens": 400,
                "temperature": 0.7
            }
            # 🔁 ЗАМЕНА ЭНДПОИНТА: api.agnes.ai → apihub.agnes-ai.cn
            resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if text and len(text) > 50:
                    return text.strip()
        except Exception as e:
            logger.warning(f"Agnes (текст) не сработал: {e}")

    # Шаблонный генератор
    return generate_template_text(topic)

def generate_template_text(topic: str) -> str:
    intro_phrases = [
        f"Нейросети и искусственный интеллект всё глубже проникают в нашу жизнь. Сегодня мы поговорим о том, как {topic} меняет привычный уклад.",
        f"Искусственный интеллект — это не просто модное слово, а реальный инструмент трансформации. Разберёмся, как {topic} влияет на бизнес и повседневность.",
        f"Технологии ИИ развиваются стремительно. Одна из ключевых тем сегодня — {topic}. Давайте рассмотрим её подробнее."
    ]
    body_phrases = [
        "Согласно последним исследованиям, компании, внедряющие AI, увеличивают производительность на 30–40%.",
        "Нейросети уже сегодня помогают в анализе данных, прогнозировании, автоматизации рутинных задач.",
        "Специалисты по данным и AI-инженеры становятся самыми востребованными на рынке труда.",
        "Важно понимать, что ИИ не заменяет человека, а дополняет его компетенции, освобождая время для творчества.",
        "Однако есть и вызовы: этические вопросы, необходимость переобучения кадров, кибербезопасность.",
        "Перспективы огромны: от персонализированной медицины до управления умными городами."
    ]
    conclusion_phrases = [
        "Подводя итог, можно сказать, что {topic} — это не будущее, а уже настоящее. Важно быть в курсе и использовать эти инструменты с умом.",
        "Искусственный интеллект открывает новые горизонты. Будьте готовы меняться вместе с технологиями!",
        "Следите за обновлениями в нашем сообществе, чтобы не пропустить самое интересное о мире ИИ."
    ]
    intro = random.choice(intro_phrases)
    body = random.sample(body_phrases, k=3)
    conclusion = random.choice(conclusion_phrases).format(topic=topic)
    return f"{intro}\n\n{' '.join(body)}\n\n{conclusion}"

# ========== ГЕНЕРАТОРЫ КАРТИНОК (С РАНДОМИЗАЦИЕЙ) ==========

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
        # 🔁 ЗАМЕНА ЭНДПОИНТА: api.agnes.ai → apihub.agnes-ai.cn
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
    # Больше вариантов запросов
    query_templates = [
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
    random.shuffle(query_templates)
    for query in query_templates[:3]:
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
    """Генерирует картинку с рандомизацией и случайным пропуском Pexels"""
    generators = []
    if AGNES_API_KEY:
        generators.append(("Agnes", generate_agnes))
    if PEXELS_API_KEY:
        generators.append(("Pexels", search_pexels_relevant_photo))
    if PIXAZO_API_KEY:
        generators.append(("Pixazo", generate_pixazo))
    generators.append(("Pollinations", generate_pollinations))

    # Случайно пропускаем Pexels с вероятностью 50% (если есть другие генераторы)
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

# ========== VK ПУБЛИКАЦИЯ ==========
def upload_photo_to_vk(image_bytes, owner_id, token):
    if not token:
        raise ValueError("Нет VK токена")
    owner_id_abs = abs(owner_id)
    upload_url_api = "https://api.vk.com/method/photos.getWallUploadServer"
    params = {"access_token": token, "group_id": owner_id_abs, "v": "5.131"}
    resp = requests.get(upload_url_api, params=params).json()
    if "error" in resp:
        raise Exception(f"Ошибка получения upload_url: {resp['error']['error_msg']}")
    upload_url = resp["response"]["upload_url"]
    temp_path = f"/tmp/temp_{random.randint(1, 1000000)}.jpg"
    with open(temp_path, "wb") as f:
        f.write(image_bytes)
    files = {"photo": open(temp_path, "rb")}
    resp_upload = requests.post(upload_url, files=files).json()
    os.remove(temp_path)
    if not all(k in resp_upload for k in ("photo", "server", "hash")):
        raise Exception(f"Неполный ответ от сервера загрузки: {resp_upload}")
    save_api = "https://api.vk.com/method/photos.saveWallPhoto"
    params = {
        "access_token": token,
        "group_id": owner_id_abs,
        "photo": resp_upload["photo"],
        "server": resp_upload["server"],
        "hash": resp_upload["hash"],
        "v": "5.131"
    }
    save_resp = requests.post(save_api, data=params).json()
    if "error" in save_resp:
        raise Exception(f"Ошибка сохранения фото: {save_resp['error']['error_msg']}")
    photo_data = save_resp["response"][0]
    return f"photo{photo_data['owner_id']}_{photo_data['id']}"

def publish_to_group(text, image_bytes):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"
    try:
        attachments = []
        if image_bytes:
            try:
                attachment = upload_photo_to_vk(image_bytes, GROUP_ID_AI, VK_TOKEN_AI)
                attachments.append(attachment)
                logger.info("Фото загружено в группу")
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
        resp = requests.get(wall_api, params=params).json()
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
                attachment = upload_photo_to_vk(image_bytes, VK_USER_ID, VK_TOKEN_USER)
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
    announce_text = f"🔥 Новый пост в группе AI Навигатор: {topic}"
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
            "👋 Бот с генерацией текста и картинок\n\n"
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