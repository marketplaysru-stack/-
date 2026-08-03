#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import logging
import random
import re
import requests
import schedule
import time
import threading
from datetime import datetime
from urllib.parse import quote
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from vk_api import vk_api
from PIL import Image, ImageDraw, ImageFont
import io

# ======================== НАСТРОЙКА ЛОГИРОВАНИЯ ========================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .ENV ========================
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
VK_TOKEN_AI = os.getenv('VK_TOKEN_AI')           # <-- сюда вставьте новый токен
GROUP_ID_AI = os.getenv('GROUP_ID_AI')           # должно быть 197687739
VK_TOKEN_USER = os.getenv('VK_TOKEN_USER')
VK_USER_ID = os.getenv('VK_USER_ID')

AGNES_API_KEY = os.getenv('AGNES_API_KEY')
PEXELS_API_KEY = os.getenv('PEXELS_API_KEY')
PIXAZO_API_KEY = os.getenv('PIXAZO_API_KEY')
POLLINATIONS_BASE_URL = os.getenv('POLLINATIONS_BASE_URL', 'https://image.pollinations.ai')
IMAGE_NEGATIVE_PROMPT = os.getenv('IMAGE_NEGATIVE_PROMPT', 'ugly, deformed, blurry, low quality')

RSS_SOURCES = json.loads(os.getenv('RSS_SOURCES', '[]'))
POST_TIMES = json.loads(os.getenv('POST_TIMES', '["07:00","11:00","13:00","18:00"]'))
RSS_DEFAULT_GROUP = os.getenv('RSS_DEFAULT_GROUP', 'Родительский')
DATA_DIR = os.getenv('DATA_DIR', './data')

os.makedirs(DATA_DIR, exist_ok=True)

# ======================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ========================

def extract_keywords(text, max_words=5):
    stop_words = {'как', 'что', 'для', 'с', 'на', 'по', 'из', 'от', 'в', 'к', 'у', 'за', 'о', 'об', 'при', 'без', 'до',
                  'про', 'через', 'после', 'перед', 'между', 'среди', 'вокруг', 'около', 'возле', 'или', 'и', 'а', 'но',
                  'зато', 'так', 'же', 'ведь', 'вот', 'это', 'тот', 'этот', 'свой', 'наш', 'ваш', 'мой', 'твой', 'его',
                  'её', 'их', 'кто', 'что', 'какой', 'такой', 'весь', 'всякий', 'каждый', 'любой', 'другой', 'самый'}
    words = re.findall(r'\b[а-яА-ЯёЁa-zA-Z]+\b', text.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 2]
    if not keywords:
        keywords = text.lower().split()[:max_words]
    return ' '.join(keywords[:max_words])

# ======================== ГЕНЕРАТОРЫ ТЕКСТА ========================

def generate_text_agnes(topic):
    logger.debug(f"Генерация текста через Agnes для темы: {topic}")
    try:
        url = "https://apihub.agnes-ai.cn/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {AGNES_API_KEY}",
            "Content-Type": "application/json"
        }
        prompt = f"Напиши пост для ВКонтакте на тему: {topic}. Пост должен быть полезным, информативным, примерно 200-300 слов, без лишней воды, с чёткими советами. Используй эмодзи для структуры."
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        text = result['choices'][0]['message']['content']
        logger.debug(f"Текст сгенерирован, длина: {len(text)} символов")
        return text
    except Exception as e:
        logger.error(f"Ошибка генерации текста через Agnes: {e}")
        return f"🔥 {topic}\n\nПолезные советы и рекомендации по этой теме. Подробности читайте в нашем посте!"

# ======================== ГЕНЕРАТОРЫ КАРТИНОК ========================

def get_agnes_image(prompt):
    logger.debug("Agnes не поддерживает генерацию картинок, пропускаем")
    return None

def get_pexels_image(query):
    logger.debug(f"Pexels поиск по запросу: {query}")
    if not query or len(query) < 2:
        logger.debug("Слишком короткий запрос для Pexels")
        return None
    try:
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": PEXELS_API_KEY}
        params = {"query": query, "per_page": 3, "orientation": "landscape"}
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        photos = data.get('photos', [])
        if photos:
            photo = random.choice(photos)
            img_url = photo['src']['large']
            logger.debug(f"Найдено фото в Pexels: {img_url}")
            return img_url
        else:
            logger.debug("Pexels не вернул ни одного фото")
            return None
    except Exception as e:
        logger.error(f"Ошибка при запросе к Pexels: {e}")
        return None

def get_pixazo_image(prompt):
    logger.debug(f"Попытка генерации через Pixazo с промптом: {prompt[:50]}...")
    try:
        url = "https://api.pixazo.com/v1/generate"
        headers = {"Authorization": f"Bearer {PIXAZO_API_KEY}"}
        payload = {
            "prompt": prompt,
            "negative_prompt": IMAGE_NEGATIVE_PROMPT,
            "width": 1024,
            "height": 1024,
            "steps": 30
        }
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        img_url = data.get('image_url')
        if img_url:
            logger.debug("Pixazo сгенерировал картинку")
            return img_url
        else:
            logger.debug("Pixazo не вернул URL")
            return None
    except Exception as e:
        logger.error(f"Ошибка Pixazo: {e}")
        return None

def get_pollinations_image(prompt):
    logger.debug(f"Попытка генерации через Pollinations с промптом: {prompt[:50]}...")
    try:
        full_prompt = f"{prompt}. High quality, realistic, professional photo, 4k, detailed."
        encoded_prompt = quote(full_prompt)
        url = f"{POLLINATIONS_BASE_URL}/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&seed={random.randint(1, 100000)}"
        head = requests.head(url, timeout=10)
        if head.status_code == 200:
            logger.debug("Pollinations вернул картинку")
            return url
        else:
            logger.debug(f"Pollinations вернул статус {head.status_code}")
            return None
    except Exception as e:
        logger.error(f"Ошибка Pollinations: {e}")
        return None

def generate_banner(text):
    logger.debug("Генерация баннера с текстом")
    try:
        img = Image.new('RGB', (1024, 768), color=(73, 109, 137))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except:
            font = ImageFont.load_default()
        lines = []
        words = text.split()
        line = ""
        for w in words:
            if len(line + w) < 30:
                line += w + " "
            else:
                lines.append(line)
                line = w + " "
        if line:
            lines.append(line)
        y = 100
        for line in lines:
            draw.text((50, y), line, fill=(255, 255, 255), font=font)
            y += 50
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        imgbb_key = os.getenv('IMGBB_API_KEY')
        if imgbb_key:
            files = {'image': ('banner.png', buf, 'image/png')}
            resp = requests.post(f"https://api.imgbb.com/1/upload?key={imgbb_key}", files=files)
            if resp.status_code == 200:
                data = resp.json()
                return data['data']['url']
        logger.warning("Не удалось загрузить баннер, возвращаем None")
        return None
    except Exception as e:
        logger.error(f"Ошибка генерации баннера: {e}")
        return None

def generate_image_for_post(topic):
    logger.info(f"Начинаем генерацию картинки для темы: {topic}")

    img = get_agnes_image(topic)
    if img:
        logger.info("✅ Картинка от Agnes")
        return img, 'Agnes'

    keywords = extract_keywords(topic)
    img = get_pexels_image(keywords)
    if img:
        logger.info("✅ Картинка от Pexels")
        return img, 'Pexels'

    img = get_pixazo_image(topic)
    if img:
        logger.info("✅ Картинка от Pixazo")
        return img, 'Pixazo'

    img = get_pollinations_image(topic)
    if img:
        logger.info("✅ Картинка от Pollinations")
        return img, 'Pollinations'

    img = generate_banner(topic)
    if img:
        logger.info("✅ Картинка от Баннера")
        return img, 'Banner'

    logger.error("❌ Не удалось получить картинку ни от одного генератора")
    return None, None

# ======================== РАБОТА С VK (ИСПРАВЛЕННАЯ ЗАГРУЗКА ФОТО) ========================

def vk_upload_photo(vk_session, image_url, owner_id):
    """
    Загружает фото на стену (группы или пользователя) через прямой upload.
    Возвращает attachment в формате 'photo{owner_id}_{id}'.
    """
    logger.debug(f"Загрузка фото из {image_url} для owner_id={owner_id}")
    try:
        # 1. Скачиваем изображение
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
        image_data = resp.content
        logger.debug(f"Изображение скачано, размер: {len(image_data)} байт")

        vk = vk_session

        # 2. Получаем URL для загрузки
        if owner_id < 0:
            group_id = abs(owner_id)
            upload_url = vk.method('photos.getWallUploadServer', {'group_id': group_id})['upload_url']
            logger.debug(f"Upload URL для группы: {upload_url}")
        else:
            upload_url = vk.method('photos.getWallUploadServer', {})['upload_url']
            logger.debug(f"Upload URL для личной стены: {upload_url}")

        # 3. Загружаем фото на сервер
        files = {'photo': ('image.jpg', image_data, 'image/jpeg')}
        upload_response = requests.post(upload_url, files=files)
        upload_response.raise_for_status()
        upload_data = upload_response.json()
        logger.debug(f"Ответ сервера загрузки: {upload_data}")

        # 4. Сохраняем фото на стену
        save_params = {
            'photo': upload_data['photo'],
            'server': upload_data['server'],
            'hash': upload_data['hash']
        }
        if owner_id < 0:
            save_params['group_id'] = abs(owner_id)
        saved = vk.method('photos.saveWallPhoto', save_params)
        logger.debug(f"Результат сохранения: {saved}")

        # 5. Формируем attachment
        photo = saved[0]  # список с одним фото
        attachment = f"photo{photo['owner_id']}_{photo['id']}"
        logger.debug(f"Получен attachment: {attachment}")
        return attachment
    except Exception as e:
        logger.error(f"Ошибка загрузки фото: {e}", exc_info=True)
        return None

def post_to_vk_group(vk_session, group_id, text, image_url):
    logger.info(f"Публикация в группу {group_id}")
    try:
        group_id_int = int(group_id)
        owner_id = -abs(group_id_int)
        attachment = vk_upload_photo(vk_session, image_url, owner_id) if image_url else None
        if not attachment:
            logger.warning("Не удалось загрузить фото, публикуем без фото")
            attachments = []
        else:
            attachments = [attachment]

        params = {
            'owner_id': owner_id,
            'from_group': 1,
            'message': text,
            'attachments': ','.join(attachments)
        }
        logger.debug(f"Параметры wall.post: {params}")
        response = vk_session.method('wall.post', params)
        logger.info(f"Пост опубликован, ID = {response['post_id']}")
        return response
    except Exception as e:
        logger.error(f"Ошибка публикации в группу: {e}", exc_info=True)
        return None

def post_to_user_wall(vk_session, user_id, text, image_url):
    logger.info(f"Публикация на личную стену пользователя {user_id}")
    try:
        owner_id = int(user_id)
        attachment = vk_upload_photo(vk_session, image_url, owner_id) if image_url else None
        if not attachment:
            attachments = []
        else:
            attachments = [attachment]

        params = {
            'owner_id': owner_id,
            'message': text,
            'attachments': ','.join(attachments)
        }
        response = vk_session.method('wall.post', params)
        logger.info(f"Пост на личной стене опубликован, ID = {response['post_id']}")
        return response
    except Exception as e:
        logger.error(f"Ошибка публикации на личную стену: {e}", exc_info=True)
        return None

# ======================== ОБРАБОТЧИКИ КОМАНД TELEGRAM ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Я бот для публикации постов в ВК.\nИспользуй /post <тема> для создания и публикации поста.")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong 🏓")

async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = ' '.join(context.args)
    if not topic:
        await update.message.reply_text("Укажи тему после команды, например: /post Как помочь ребёнку адаптироваться")
        return

    await update.message.reply_text("🔄 Генерирую пост...")
    try:
        text = generate_text_agnes(topic)
        image_url, source = generate_image_for_post(topic)
        if not image_url:
            await update.message.reply_text("⚠️ Не удалось создать картинку, публикую только текст.")
            # Всё равно публикуем текст
            image_url = None

        vk_session = vk_api.VkApi(token=VK_TOKEN_AI)
        group_id = GROUP_ID_AI
        result = post_to_vk_group(vk_session, group_id, text, image_url)
        if result:
            msg = f"✅ Пост опубликован в группе!\nТема: {topic}"
            if image_url:
                msg += f"\nКартинка: {source}"
            else:
                msg += "\n⚠️ Без картинки"
            if VK_TOKEN_USER and VK_USER_ID:
                vk_user_session = vk_api.VkApi(token=VK_TOKEN_USER)
                post_to_user_wall(vk_user_session, VK_USER_ID, text, image_url)
                msg += "\n✅ Также опубликован на личной стене"
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("❌ Ошибка публикации в группу.")
    except Exception as e:
        logger.error(f"Ошибка в /post: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")

# ======================== RSS-ПЛАНИРОВЩИК (ЗАГЛУШКА) ========================

def rss_worker():
    schedule.every().day.at("07:00").do(rss_post_job)
    schedule.every().day.at("11:00").do(rss_post_job)
    schedule.every().day.at("13:00").do(rss_post_job)
    schedule.every().day.at("18:00").do(rss_post_job)
    while True:
        schedule.run_pending()
        time.sleep(60)

def rss_post_job():
    logger.info("Запуск RSS-постинга по расписанию (заглушка)")

# ======================== ЗАПУСК БОТА ========================

def main():
    rss_thread = threading.Thread(target=rss_worker, daemon=True)
    rss_thread.start()
    logger.info("📡 RSS-планировщик запущен")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("post", post_command))

    logger.info("🚀 Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()