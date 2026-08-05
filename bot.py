#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Родительский навигатор – генерация постов по схеме: Хук → Лид → Тело → Вывод → CTA → Темы → Хештеги.
Картинки генерируются через нейросети (Agnes → Pixazo → Pollinations) или берутся из Pexels.
Кэш использованных картинок предотвращает дубли.
Автопостинг каждые 6 часов. Поддержка topics.txt и RSS.
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
import schedule
import feedparser
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import vk_api
from vk_api.upload import VkUpload

load_dotenv()

# ---------- НАСТРОЙКИ ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
VK_TOKEN_AI = os.getenv("VK_TOKEN_AI")
GROUP_ID_AI = int(os.getenv("GROUP_ID_AI", "197687739"))
VK_TOKEN_USER = os.getenv("VK_TOKEN_USER")
VK_USER_ID = int(os.getenv("VK_USER_ID", "317272476"))

AGNES_API_KEY = os.getenv("AGNES_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXAZO_API_KEY = os.getenv("PIXAZO_API_KEY")
POLLINATIONS_BASE_URL = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai")
IMAGE_NEGATIVE_PROMPT = os.getenv("IMAGE_NEGATIVE_PROMPT", "ugly, deformed, blurry, low quality, same face, boring, plain, cartoon, doll, mannequin, 3d render, smooth skin, unrealistic, extra limbs, bad anatomy, distorted, people, human, woman, girl, beach, sea, sand, swimsuit, nude, naked, portrait, selfie, smile, face, eyes, hair, meadow, field, hay, grass, farm, cow, horse, rural, village, landscape, trees, forest, nature, road, mountains, countryside, plants, outdoor")

RSS_DEFAULT_GROUP = os.getenv("RSS_DEFAULT_GROUP", "Родительский навигатор")
RSS_SOURCES_JSON = os.getenv("RSS_SOURCES", '[]')
RSS_ENABLED = os.getenv("RSS_ENABLED", "false").lower() == "true"
DATA_DIR = os.getenv("DATA_DIR", "./data")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не задан!")

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "post_state.json")
USED_IMAGES_FILE = os.path.join(DATA_DIR, "used_images.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ---------- КЭШ КАРТИНОК ----------
def load_used_images():
    if os.path.exists(USED_IMAGES_FILE):
        with open(USED_IMAGES_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_used_images(used_set):
    with open(USED_IMAGES_FILE, "w") as f:
        json.dump(list(used_set), f)

def compute_hash(image_bytes):
    return hashlib.md5(image_bytes).hexdigest()

def is_image_used(image_bytes):
    h = compute_hash(image_bytes)
    used = load_used_images()
    return h in used

def mark_image_as_used(image_bytes):
    h = compute_hash(image_bytes)
    used = load_used_images()
    used.add(h)
    save_used_images(used)

# ---------- ЗАГРУЗКА ПОСТОВ ИЗ ФАЙЛА ----------
POSTS_FILE = "posts.txt"

def load_posts_from_file():
    posts = []
    if not os.path.exists(POSTS_FILE):
        return None
    try:
        with open(POSTS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        blocks = re.split(r'\n===', content)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            if block.startswith('==='):
                block = block[3:].strip()
            lines = block.split('\n')
            if not lines:
                continue
            title = lines[0].strip()
            text = '\n'.join(lines[1:]).strip()
            if title and text:
                posts.append({'title': title, 'text': text})
        if posts:
            logger.info(f"✅ Загружено {len(posts)} готовых постов из {POSTS_FILE}")
            return posts
    except Exception as e:
        logger.error(f"Ошибка чтения {POSTS_FILE}: {e}")
    return None

# ---------- RSS ПАРСИНГ ----------
def get_rss_entries(sources_json):
    sources = json.loads(sources_json) if sources_json else []
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
            logger.info(f"RSS {url}: получено {len(entries)} заголовков")
        except Exception as e:
            logger.error(f"Ошибка парсинга RSS {url}: {e}")
    return entries

# ---------- ЗАПАСНЫЕ ТЕМЫ ----------
def load_topics():
    try:
        with open("topics.txt", "r", encoding="utf-8") as f:
            topics = [line.strip() for line in f if line.strip()]
        if topics:
            return topics
    except FileNotFoundError:
        pass
    return [
        "Как помочь ребёнку адаптироваться в школе",
        "Развитие эмоционального интеллекта у детей",
        "Советы по воспитанию подростков",
        "Как говорить с ребёнком о трудных вопросах",
        "Развитие самостоятельности у детей",
        "Выбор кружков и секций для ребёнка",
        "Как мотивировать ребёнка к учёбе",
        "Психологические кризисы у детей: как помочь",
        "Безопасность детей в интернете",
        "Как развить у ребёнка уверенность в себе"
    ]

# ---------- TELEGRAM ----------
def send_message(chat_id, text):
    requests.post(f"{BASE_URL}/sendMessage", json={"chat_id": chat_id, "text": text})

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(f"{BASE_URL}/getUpdates", params=params, timeout=(15, 120))
        return resp.json().get("result", [])
    except Exception as e:
        logger.error(f"Ошибка получения обновлений: {e}")
        return []

# ---------- ГЕНЕРАЦИЯ ТЕКСТА ----------
def generate_text(topic: str) -> str:
    if AGNES_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
            prompt = f"""
Напиши развернутый пост (около 250–300 слов) на тему: {topic} (воспитание, дети, родительство).
Пост должен быть полезным, содержать факты, примеры из жизни, практические советы и вопросы к читателям.
Структура:
1. Хук – цепляющий заголовок (5–7 слов) с эмодзи.
2. Вступление – 3–4 предложения, которые вводят в тему.
3. Основной блок – 3–5 пунктов с подзаголовками (краткими) и пояснениями (2–3 предложения каждый).
4. Вывод – 2–3 предложения с итогом и вдохновением.
5. CTA – вопрос к аудитории, вовлекающий в обсуждение.
6. Темы для комментариев – 3 вопроса.
7. Хештеги – 7–10 штук.

Пост должен быть живым, человечным, без канцелярита. Используй эмодзи, абзацы.
"""
            data = {
                "model": "agnes-v1",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "temperature": 0.85
            }
            resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if text and len(text) > 100:
                    return text.strip()
        except Exception as e:
            logger.warning(f"Agnes не сработал: {e}")

    return generate_template_text(topic)

def generate_template_text(topic: str) -> str:
    hooks = [
        f"👨‍👩‍👧 {topic.upper()} – ЭТО НЕ ШКОЛА, ЭТО ЖИЗНЬ",
        f"😤 {topic.upper()} – РОДИТЕЛИ, ЭТО ПРО ВАС",
        f"💔 {topic.upper()} – БОЛЬ, КОТОРУЮ МЫ ИГНОРИРУЕМ",
        f"🤯 {topic.upper()} – ТЫ ТОЖЕ ЭТО ПРОХОДИЛ?",
        f"💥 {topic.upper()} – КАК НЕ СЛОМАТЬ РЕБЁНКА"
    ]
    hook = random.choice(hooks)

    leads = [
        f"Ребёнок жалуется на живот, хотя температуры нет? Стал раздражительным? Спойлер: он не симулянт. Он в стрессе. {topic} – это не просто слова, это реальная проблема, которую мы часто игнорируем.",
        f"Ты когда-нибудь замечал, что твой ребёнок перестал делиться новостями из школы? Или стал огрызаться на любые вопросы? Возможно, ты столкнулся с {topic}. Давай разберёмся, что делать.",
        f"Воспитывать детей – это как вести корабль по бурному морю. Иногда кажется, что ты всё делаешь неправильно. {topic} – одна из тех тем, о которых страшно говорить, но нужно."
    ]
    lead = random.choice(leads)

    body_pool = [
        ("👂", "Слушай, а не критикуй", "Дети хотят быть услышанными. Начни диалог с вопроса: «Что случилось?» – это снижает защиту."),
        ("🧠", "Эмоциональный интеллект", "Учи ребёнка распознавать свои чувства. Это поможет ему справляться с тревогой."),
        ("💪", "Поддержка, а не контроль", "Вместо «сделай так» скажи «я верю в тебя». Это даёт уверенность."),
        ("❤️", "Любовь без условий", "Даже когда ребёнок ошибается, он должен знать, что его любят. Это основа психологического здоровья."),
        ("🤝", "Совместное решение", "Обсуждайте проблемы вместе – дети чувствуют себя важными и ответственными."),
        ("🌟", "Похвала за усилия", "Хвалите не за результат, а за старания – это формирует самооценку."),
        ("📌", "Личный пример", "Твоё поведение – главный урок. Если ты честен и открыт, ребёнок будет брать с тебя пример."),
        ("💡", "Игровой подход", "Учите через игру – дети лучше усваивают информацию в формате увлекательных заданий."),
        ("🛡️", "Безопасная среда", "Ребёнок должен знать, что дом – это место, где его защитят от любых проблем."),
        ("🎯", "Цели и мечты", "Помогайте ставить цели, но не навязывайте свои. Пусть ребёнок выбирает свой путь.")
    ]
    random.shuffle(body_pool)
    selected = body_pool[:random.randint(3, 5)]
    body = "\n".join([f"{emoji} **{title}**\n{desc}" for emoji, title, desc in selected])

    conclusions = [
        f"Помните: {topic} – это не поражение, это возможность стать ближе к своему ребёнку.",
        "Каждый ребёнок уникален. Не бойтесь ошибаться, бойтесь не пробовать.",
        "Лучшее, что вы можете дать ребёнку – это ваше время и внимание."
    ]
    conclusion = random.choice(conclusions)

    cta_questions = [
        f"👇 А вы сталкивались с ситуациями, когда {topic} был для вас вызовом? Как выходили из положения?",
        f"👇 Что вам помогает сохранять спокойствие в сложных моментах общения с детьми?",
        f"👇 Согласны, что иногда самое сложное – это просто być рядом?"
    ]
    cta = random.choice(cta_questions)

    comments_themes = [
        "1. «Как вы справляетесь с капризами ребёнка?» – поделитесь лайфхаками.",
        "2. «Какую книгу или методику по воспитанию вы считаете самой полезной?» – порекомендуйте.",
        "3. «Что бы вы хотели изменить в своём подходе к воспитанию?» – поделитесь мыслями."
    ]
    themes = "\n".join(comments_themes)

    base_hashtags = ["#воспитание", "#дети", "#родители", "#психология", "#школа", "#семья", "#развитие", "#родительство"]
    extra = [f"#{topic.replace(' ', '').lower()}" for _ in range(3)]
    hashtags = list(set(base_hashtags + extra))[:10]
    hashtag_str = " ".join(hashtags)

    post = f"{hook}\n\n{lead}\n\n{body}\n\n{conclusion}\n\n{cta}\n\nТемы для обсуждения:\n{themes}\n\n{hashtag_str}"
    return post

# ---------- ГЕНЕРАТОРЫ КАРТИНОК ----------
def generate_agnes_image(prompt):
    if not AGNES_API_KEY:
        return None
    try:
        seed = random.randint(1, 1000000)
        adjectives = ["bright", "colorful", "vibrant", "modern", "elegant", "minimalistic", "professional", "high quality"]
        adj = random.choice(adjectives)
        full_prompt = f"{adj} illustration about {prompt}, parenting, family, children, creative, modern, no people, no nature"
        headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
        data = {
            "prompt": full_prompt,
            "negative_prompt": "ugly, deformed, blurry, low quality, people, human, woman, girl, nature, trees, landscape",
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
        logger.warning(f"Agnes генерация не сработала: {e}")
    return None

def generate_pixazo_image(prompt):
    if not PIXAZO_API_KEY:
        return None
    try:
        seed = random.randint(1, 1000000)
        adjectives = ["bright", "colorful", "vibrant", "modern", "elegant", "minimalistic", "professional", "high quality"]
        adj = random.choice(adjectives)
        full_prompt = f"{adj} illustration about {prompt}, parenting, family, children, creative, modern, no people, no nature"
        url = "https://api.pixazo.com/v1/generate"
        headers = {
            "Authorization": f"Bearer {PIXAZO_API_KEY}",
            "Content-Type": "application/json"
        }
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

def generate_pollinations_image(prompt):
    try:
        seed = random.randint(1, 1000000)
        adjectives = ["bright", "colorful", "vibrant", "modern", "elegant", "minimalistic", "professional", "high quality"]
        adj = random.choice(adjectives)
        full_prompt = f"{adj} {prompt}, parenting, family, children, creative, modern, no people, no nature"
        url = f"{POLLINATIONS_BASE_URL}/prompt/{requests.utils.quote(full_prompt)}?width=1024&height=1024&nologo=true&seed={seed}&model=flux"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.warning(f"Pollinations не сработал: {e}")
    return None

def search_pexels_relevant_photo(topic):
    if not PEXELS_API_KEY:
        return None
    queries = [
        f"parenting {topic}",
        f"family {topic}",
        f"children {topic}"
    ]
    random.shuffle(queries)
    for query in queries[:2]:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        params = {"query": query, "per_page": 3, "orientation": "landscape"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos", [])
                if photos:
                    return random.choice(photos)["src"]["large2x"]
        except:
            pass
    return None

def download_photo(url):
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.content
    except:
        pass
    return None

def generate_image(topic):
    generators = [
        ("Agnes", generate_agnes_image),
        ("Pixazo", generate_pixazo_image),
        ("Pollinations", generate_pollinations_image)
    ]
    random.shuffle(generators)

    for name, func in generators:
        img = func(topic)
        if img:
            if not is_image_used(img):
                mark_image_as_used(img)
                logger.info(f"✅ Картинка сгенерирована через {name} (уникальная)")
                return img, name
            else:
                logger.info(f"⚠️ Картинка от {name} уже использовалась, пробуем следующий генератор")

    photo_url = search_pexels_relevant_photo(topic)
    if photo_url:
        img = download_photo(photo_url)
        if img and not is_image_used(img):
            mark_image_as_used(img)
            logger.info("✅ Картинка от Pexels (уникальная)")
            return img, "Pexels"
        elif img:
            logger.info("⚠️ Картинка от Pexels уже использовалась")

    img = create_banner(topic[:20])
    if not is_image_used(img):
        mark_image_as_used(img)
        logger.info("✅ Использован баннер (уникальный)")
        return img, "баннер"
    else:
        img = create_banner(topic[:15] + str(random.randint(1, 100)))
        mark_image_as_used(img)
        logger.info("✅ Использован баннер с рандомным суффиксом")
        return img, "баннер"

def create_banner(text, width=1024, height=1024):
    img = Image.new('RGB', (width, height), color='#2a4a7f')
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

# ---------- ЗАГРУЗКА ФОТО (ПРЯМОЙ HTTP) ----------
def upload_photo_to_vk_via_http(image_bytes, owner_id, token):
    try:
        vk = vk_api.VkApi(token=token)
        if owner_id < 0:
            group_id = abs(owner_id)
            upload_url = vk.method('photos.getWallUploadServer', {'group_id': group_id})['upload_url']
            logger.info(f"Загрузка в группу {group_id}")
        else:
            upload_url = vk.method('photos.getWallUploadServer', {})['upload_url']
            logger.info(f"Загрузка на публичную страницу/личную стену (owner_id={owner_id})")

        files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
        resp = requests.post(upload_url, files=files)
        resp.raise_for_status()
        upload_data = resp.json()
        if 'photo' not in upload_data or 'server' not in upload_data or 'hash' not in upload_data:
            logger.error(f"Неполный ответ: {upload_data}")
            return None
        save_params = {
            'photo': upload_data['photo'],
            'server': upload_data['server'],
            'hash': upload_data['hash']
        }
        if owner_id < 0:
            save_params['group_id'] = abs(owner_id)
        saved = vk.method('photos.saveWallPhoto', save_params)
        photo = saved[0]
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
        logger.info(f"Получен attachment: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}")
        return None

# ---------- VK ПУБЛИКАЦИЯ ----------
def publish_to_group(text, image_bytes):
    if not VK_TOKEN_AI or not GROUP_ID_AI:
        return "❌ Нет VK токена или ID"
    try:
        attachments = []
        if image_bytes:
            attachment = upload_photo_to_vk_via_http(image_bytes, GROUP_ID_AI, VK_TOKEN_AI)
            if attachment:
                attachments.append(attachment)
                logger.info("Фото успешно загружено в группу")
            else:
                logger.warning("Не удалось загрузить фото в группу, попробуем без фото")
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
        resp = requests.get("https://api.vk.com/method/wall.post", params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK: {resp['error']['error_msg']}"
        return f"✅ Пост в группе опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации в группу: {e}")
        return f"❌ Ошибка: {e}"

def publish_announce_to_user(announce_text, image_bytes, group_link):
    if not VK_TOKEN_USER or not VK_USER_ID:
        return "❌ Нет токена для личной стены"
    try:
        attachments = []
        if image_bytes:
            attachment = upload_photo_to_vk_via_http(image_bytes, VK_USER_ID, VK_TOKEN_USER)
            if attachment:
                attachments.append(attachment)
                logger.info("Фото загружено на личную стену")
            else:
                logger.warning("Не удалось загрузить фото на личную стену")
        full_text = announce_text + f"\n\n👉 {group_link}" if group_link else announce_text
        params = {
            "access_token": VK_TOKEN_USER,
            "owner_id": VK_USER_ID,
            "message": full_text,
            "v": "5.131"
        }
        if attachments:
            params["attachments"] = ",".join(attachments)
        resp = requests.get("https://api.vk.com/method/wall.post", params=params).json()
        if "error" in resp:
            return f"❌ Ошибка VK (личная): {resp['error']['error_msg']}"
        return f"✅ Анонс опубликован (id: {resp['response']['post_id']})"
    except Exception as e:
        logger.error(f"Ошибка публикации на личную стену: {e}")
        return f"❌ Ошибка: {e}"

# ---------- СОЗДАНИЕ ПОСТА ----------
def create_post_content(title, text=None):
    if text and len(text) > 50:
        post_text = None
        if AGNES_API_KEY:
            try:
                headers = {"Authorization": f"Bearer {AGNES_API_KEY}", "Content-Type": "application/json"}
                prompt = f"Перепиши следующий текст, чтобы он стал более живым, добавь эмодзи, абзацы, сделай его как пост популярного блогера о воспитании. Текст:\n\n{text}"
                data = {
                    "model": "agnes-v1",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "temperature": 0.8
                }
                resp = requests.post("https://apihub.agnes-ai.cn/v1/chat/completions", json=data, headers=headers, timeout=30)
                if resp.status_code == 200:
                    rewritten = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if rewritten and len(rewritten) > 100:
                        post_text = rewritten.strip()
                        logger.info("✅ Рерайт через Agnes выполнен")
            except Exception as e:
                logger.warning(f"Рерайт не удался: {e}")
        if not post_text:
            post_text = text
    else:
        post_text = generate_text(title)

    teaser = post_text[:150]
    if len(post_text) > 150:
        teaser += "..."
    lines = post_text.split('\n')
    if lines and len(lines[0]) < 150:
        teaser = lines[0] + "..."

    announce_text = f"🔥 Новый пост в группе {RSS_DEFAULT_GROUP}\n\n{teaser}\n\n➡️ Читать полностью и обсудить в группе:"
    group_link = f"https://vk.com/public{GROUP_ID_AI}" if GROUP_ID_AI > 0 else f"https://vk.com/club{abs(GROUP_ID_AI)}"
    image_bytes, source = generate_image(title)
    return post_text, announce_text, group_link, image_bytes, source

def publish_post_item(title, text=None):
    post_text, announce_text, group_link, image_bytes, source = create_post_content(title, text)
    group_res = publish_to_group(post_text, image_bytes)
    user_res = publish_announce_to_user(announce_text, image_bytes, group_link)
    return {"group": group_res, "user": user_res, "source": source}

# ---------- ПОЛУЧЕНИЕ СЛЕДУЮЩЕГО ПОСТА ----------
POSTS_POOL = None

def build_posts_pool():
    global POSTS_POOL
    posts = load_posts_from_file()
    if posts:
        POSTS_POOL = posts
        logger.info("📚 Используем посты из файла posts.txt")
        return

    if RSS_ENABLED and RSS_SOURCES_JSON and RSS_SOURCES_JSON != '[]':
        entries = get_rss_entries(RSS_SOURCES_JSON)
        if entries:
            POSTS_POOL = [{'title': t, 'text': None} for t in entries]
            logger.info(f"📚 Используем RSS-заголовки: {len(POSTS_POOL)} тем")
            return

    topics = load_topics()
    POSTS_POOL = [{'title': t, 'text': None} for t in topics]
    logger.info(f"📚 Используем запасной список тем: {len(POSTS_POOL)}")

def get_posts_pool():
    global POSTS_POOL
    if POSTS_POOL is None:
        build_posts_pool()
    return POSTS_POOL

def get_next_post():
    pool = get_posts_pool()
    if not pool:
        return None, None
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    used_indices = state.get(today, [])
    available = [i for i in range(len(pool)) if i not in used_indices]
    if not available:
        available = list(range(len(pool)))
        used_indices = []
    idx = random.choice(available)
    used_indices.append(idx)
    state[today] = used_indices
    save_state(state)
    post = pool[idx]
    return post['title'], post['text']

# ---------- СОСТОЯНИЕ ----------
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ---------- ПЛАНИРОВЩИК ----------
def scheduled_post():
    logger.info("⏰ Автопостинг (каждые 6 часов)")
    try:
        title, text = get_next_post()
        if not title:
            logger.warning("Нет доступных постов для публикации")
            return
        result = publish_post_item(title, text)
        logger.info(f"Результат: {result}")
    except Exception as e:
        logger.error(f"Ошибка автопостинга: {e}")

def scheduler_worker():
    logger.info("📡 Планировщик запущен (4 поста в сутки)")
    scheduled_post()
    schedule.every(6).hours.do(scheduled_post)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ---------- КОМАНДЫ ----------
def handle_command(chat_id, text):
    if text in ("/start", "/help"):
        send_message(chat_id,
            "👨‍👩‍👧 Бот «Родительский навигатор» – посты по схеме!\n"
            "📌 Команды:\n"
            "/post <заголовок> — сгенерировать пост\n"
            "/post <текст (длиннее 50 символов)> — опубликовать с рерайтом\n"
            "/ping — проверка\n"
            "/status — статистика"
        )
        return
    if text == "/ping":
        send_message(chat_id, "🏓 Pong!")
        return
    if text == "/status":
        state = load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        used = state.get(today, [])
        total = len(get_posts_pool())
        send_message(chat_id, f"📊 Сегодня опубликовано {len(used)} постов из {total}.")
        return
    if text.startswith("/post"):
        content = text.replace("/post", "").strip()
        if not content:
            send_message(chat_id, "❌ Укажите тему или готовый текст.")
            return
        if len(content) > 50:
            title = content[:50] + "..."
            result = publish_post_item(title, content)
        else:
            result = publish_post_item(content)
        send_message(chat_id, f"📌 Группа: {result['group']}\n👤 Анонс: {result['user']}")
        return

# ---------- ЗАПУСК ----------
def main():
    logger.info("🚀 Бот запущен")
    threading.Thread(target=scheduler_worker, daemon=True).start()
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