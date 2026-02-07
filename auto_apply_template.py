#!/usr/bin/env python3
"""
HH.ru Автооткликатор
Автоматический поиск вакансий и отклик с AI-генерированными письмами

ИНСТРУКЦИЯ:
1. Замени YOUR_OPENAI_API_KEY на свой ключ от OpenAI
2. Заполни MY_PROFILE своими данными (Claude поможет)
3. Настрой SEARCH_QUERIES под свои вакансии
4. Запусти: python3 auto_apply.py
"""

import json
import os
import sys
import time
from openai import OpenAI
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

# ============== НАСТРОЙКИ ==============

# OpenAI API ключ (получить на https://platform.openai.com/api-keys)
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"

# Поисковые запросы (замени на свои)
SEARCH_QUERIES = [
    "YOUR_SEARCH_QUERY_1",
    "YOUR_SEARCH_QUERY_2",
]

# Сколько страниц парсить для каждого запроса (1 страница = ~20 вакансий)
MAX_PAGES = 1

# Пауза между откликами (секунды) — не ставь меньше 5, иначе забанят
DELAY_BETWEEN_APPLIES = 7

# Путь к файлу сессии
N8N_FILES_DIR = os.getenv("N8N_FILES_DIR", os.path.expanduser("~/.n8n-files"))
SESSION_FILE = os.path.join(N8N_FILES_DIR, "hh_session.json")

# ============== ТВОЙ ПРОФИЛЬ ==============
# Claude поможет тебе заполнить этот раздел

MY_PROFILE = """
Имя: YOUR_NAME
Возраст: YOUR_AGE лет
Город: YOUR_CITY
Формат работы: YOUR_WORK_FORMAT

Опыт: YOUR_EXPERIENCE_YEARS лет в YOUR_FIELD, реализовал около YOUR_PROJECTS_COUNT проектов

Ключевые компетенции:
- YOUR_SKILL_1
- YOUR_SKILL_2
- YOUR_SKILL_3

Крутые кейсы:
- YOUR_CASE_1
- YOUR_CASE_2

Работал с: YOUR_CLIENTS

Сайт/портфолио: YOUR_WEBSITE

Что важно в работе: YOUR_VALUES
"""

# ============== КОД ==============

client = OpenAI(api_key=OPENAI_API_KEY)


def generate_cover_letter(title: str, employer: str, description: str) -> str:
    """Генерирует сопроводительное письмо через ChatGPT"""
    prompt = f"""Напиши сопроводительное письмо для отклика на вакансию. Пиши как живой человек, не как робот.

СТРУКТУРА ПИСЬМА:
1. Приветствие (Добрый день! или Здравствуйте!)
2. Представься кратко (имя, чем занимаюсь)
3. Почему заинтересовала именно эта вакансия/компания (найди что-то конкретное в описании)
4. Кратко релевантный опыт (1-2 примера кейсов которые подходят под вакансию)
5. Призыв к действию + ссылка на сайт с кейсами (если есть)

ПРАВИЛА:
- Длина: 4-6 предложений, не больше
- Тон: дружелюбный, профессиональный, но не официозный
- Без штампов: "с большим интересом", "буду рад", "внести вклад", "рассмотрите мою кандидатуру"
- Без длинных тире (—), без восклицательных знаков в конце каждого предложения
- Пиши так, будто реальный человек пишет реальному человеку
- Русский язык

ОБО МНЕ:
{MY_PROFILE}

ВАКАНСИЯ:
Название: {title}
Компания: {employer}
Описание: {description[:2500]}

Напиши только текст письма, без комментариев."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            temperature=0.8
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  ⚠️  Ошибка генерации письма: {e}")
        return ""


def search_vacancies(page, query: str, page_num: int = 0) -> list:
    """Ищет вакансии на HH.ru"""
    url = f"https://hh.ru/search/vacancy?text={query}&area=113&items_on_page=20&page={page_num}"
    
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        # Проверка на капчу
        if "captcha" in page.title().lower():
            print("  ⚠️  Сработала капча! Подожди немного и попробуй снова.")
            return []
        
        # Ждём загрузки вакансий
        page.wait_for_selector("[data-qa='vacancy-serp__vacancy']", timeout=10000)
        
        vacancies = []
        cards = page.locator("[data-qa='vacancy-serp__vacancy']").all()
        
        for card in cards:
            try:
                title_el = card.locator("[data-qa='serp-item__title']")
                title_el.wait_for(state="visible", timeout=5000)
                
                href = title_el.get_attribute("href")
                title = title_el.inner_text()
                
                employer_el = card.locator("[data-qa='vacancy-serp__vacancy-employer']").first
                employer = employer_el.inner_text() if employer_el.count() > 0 else "Компания"
                
                vacancies.append({
                    "title": title,
                    "url": href,
                    "employer": employer
                })
            except:
                continue
        
        return vacancies
    except Exception as e:
        print(f"  ⚠️  Ошибка поиска: {e}")
        return []


def get_vacancy_description(page, url: str) -> str:
    """Получает описание вакансии"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("[data-qa='vacancy-description']", timeout=10000)
        
        desc_el = page.locator("[data-qa='vacancy-description']")
        return desc_el.inner_text() if desc_el.count() > 0 else ""
    except:
        return ""


def apply_to_vacancy(page, url: str, message: str) -> dict:
    """Откликается на вакансию с сопроводительным письмом"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        
        # Уже откликались?
        if page.locator("text=Вы откликнулись").count() > 0:
            return {"status": "skipped", "reason": "Уже откликались"}
        
        # СПОСОБ 1: Ищем ссылку "Написать сопроводительное" 
        cover_link = page.locator("a:has-text('Написать сопроводительное')")
        if cover_link.count() > 0 and message:
            print("      🔍 Нашёл ссылку 'Написать сопроводительное'")
            cover_link.first.click()
            page.wait_for_timeout(2000)
            
            letter_area = page.locator("textarea").first
            if letter_area.count() > 0:
                print("      ✍️  Заполняю письмо...")
                letter_area.fill(message)
                page.wait_for_timeout(500)
                
                submit_btn = page.locator("button:has-text('Откликнуться'), button:has-text('Отправить'), button[data-qa='vacancy-response-submit-popup']").first
                if submit_btn.count() > 0:
                    submit_btn.click()
                    page.wait_for_timeout(3000)
                    return {"status": "success", "reason": "С письмом"}
        
        # СПОСОБ 2: Кнопка с выпадающим меню
        apply_btn = page.locator("[data-qa='vacancy-response-link-top']")
        if apply_btn.count() == 0:
            apply_btn = page.locator("[data-qa='vacancy-response-link-bottom']")
        
        if apply_btn.count() > 0 and message:
            dropdown = page.locator("[data-qa='vacancy-response-link-top'] ~ button, button[data-qa='vacancy-response-actions-dropdown']").first
            if dropdown.count() > 0:
                print("      🔍 Нашёл выпадающее меню")
                dropdown.click()
                page.wait_for_timeout(1000)
                
                with_letter = page.locator("text=С сопроводительным, text=сопроводительным письмом").first
                if with_letter.count() > 0:
                    print("      🔍 Нашёл опцию 'С сопроводительным'")
                    with_letter.click()
                    page.wait_for_timeout(2000)
                    
                    letter_area = page.locator("textarea").first
                    if letter_area.count() > 0:
                        print("      ✍️  Заполняю письмо...")
                        letter_area.fill(message)
                        page.wait_for_timeout(500)
                        
                        submit_btn = page.locator("button:has-text('Откликнуться'), button:has-text('Отправить')").first
                        if submit_btn.count() > 0:
                            submit_btn.click()
                            page.wait_for_timeout(3000)
                            return {"status": "success", "reason": "С письмом (меню)"}
        
        # СПОСОБ 3: Просто нажать "Откликнуться" и потом добавить письмо
        if apply_btn.count() > 0:
            print("      🔍 Жму основную кнопку 'Откликнуться'")
            apply_btn.first.click()
            page.wait_for_timeout(3000)
            
            letter_area = page.locator("textarea").first
            if letter_area.count() > 0 and message:
                print("      ✍️  Появилось поле для письма, заполняю...")
                letter_area.fill(message)
                page.wait_for_timeout(1000)
                
                submit_btn = page.locator("button:has-text('Отправить'), button:has-text('Откликнуться'), button:has-text('Отправить письмо'), button[type='submit']").first
                if submit_btn.count() > 0:
                    print("      📨 Нажимаю кнопку отправки...")
                    submit_btn.click()
                    page.wait_for_timeout(3000)
                    return {"status": "success", "reason": "С письмом (после отклика)"}
                else:
                    all_buttons = page.locator("button").all()
                    for btn in all_buttons:
                        btn_text = btn.inner_text().lower()
                        if "отправ" in btn_text or "откликн" in btn_text:
                            print(f"      📨 Нашёл кнопку: {btn_text}")
                            btn.click()
                            page.wait_for_timeout(3000)
                            return {"status": "success", "reason": "С письмом"}
            
            if page.locator("text=Вы откликнулись").count() > 0 or page.locator("text=Резюме доставлено").count() > 0:
                return {"status": "success", "reason": "Без письма"}
            
            return {"status": "success", "reason": "Статус неясен"}
        
        return {"status": "error", "reason": "Кнопка не найдена"}
        
    except Exception as e:
        return {"status": "error", "reason": str(e)}


def main():
    print("\n" + "="*50)
    print("🚀 HH.ru Автооткликатор")
    print("="*50)
    
    # Проверка настроек
    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        print("\n❌ Ошибка: Укажи свой OpenAI API ключ в файле!")
        print("   Открой auto_apply.py и замени YOUR_OPENAI_API_KEY")
        return
    
    if "YOUR_SEARCH_QUERY" in SEARCH_QUERIES[0]:
        print("\n❌ Ошибка: Укажи поисковые запросы!")
        print("   Открой auto_apply.py и заполни SEARCH_QUERIES")
        return
    
    if "YOUR_NAME" in MY_PROFILE:
        print("\n❌ Ошибка: Заполни свой профиль!")
        print("   Открой auto_apply.py и заполни MY_PROFILE")
        return
    
    if not os.path.exists(SESSION_FILE):
        print("\n❌ Сессия не найдена!")
        print("   Сначала запусти: python3 hh_login.py")
        return
    
    print(f"\n📋 Поисковые запросы: {', '.join(SEARCH_QUERIES)}")
    print(f"📄 Страниц на запрос: {MAX_PAGES}")
    print(f"⏱️  Пауза между откликами: {DELAY_BETWEEN_APPLIES} сек")
    
    stats = {"success": 0, "skipped": 0, "error": 0}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        
        for query in SEARCH_QUERIES:
            print(f"\n🔍 Поиск: {query}")
            
            for page_num in range(MAX_PAGES):
                print(f"  📄 Страница {page_num + 1}")
                
                vacancies = search_vacancies(page, query, page_num)
                print(f"  📊 Найдено вакансий: {len(vacancies)}")
                
                for i, vacancy in enumerate(vacancies, 1):
                    print(f"\n  [{i}/{len(vacancies)}] {vacancy['title'][:50]}...")
                    print(f"      Компания: {vacancy['employer']}")
                    
                    description = get_vacancy_description(page, vacancy['url'])
                    
                    print("      💬 Генерирую письмо...")
                    letter = generate_cover_letter(
                        vacancy['title'], 
                        vacancy['employer'], 
                        description
                    )
                    
                    if letter:
                        print(f"      📝 Письмо: {letter[:80]}...")
                    
                    print("      📤 Отправляю отклик...")
                    result = apply_to_vacancy(page, vacancy['url'], letter)
                    
                    if result['status'] == 'success':
                        print(f"      ✅ Успех! ({result['reason']})")
                        stats['success'] += 1
                    elif result['status'] == 'skipped':
                        print(f"      ⏭️  Пропущено: {result['reason']}")
                        stats['skipped'] += 1
                    else:
                        print(f"      ❌ Ошибка: {result['reason']}")
                        stats['error'] += 1
                    
                    print(f"      ⏳ Пауза {DELAY_BETWEEN_APPLIES} сек...")
                    time.sleep(DELAY_BETWEEN_APPLIES)
        
        browser.close()
    
    print("\n" + "="*50)
    print("📊 ИТОГИ:")
    print(f"   ✅ Успешно: {stats['success']}")
    print(f"   ⏭️  Пропущено: {stats['skipped']}")
    print(f"   ❌ Ошибок: {stats['error']}")
    print("="*50 + "\n")


if __name__ == "__main__":
    main()
