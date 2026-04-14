import discord
from discord.ext import commands
from discord import app_commands
import os
import requests
from datetime import datetime
from duckduckgo_search import DDGS

# Инициализация бота
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# Переменные окружения
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
HF_API_URL = "https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium"

# Хранилище данных
channel_history = {}
user_requests = {}
channel_personality = {}

MAX_REQUESTS_PER_DAY = 15
MAX_HISTORY = 10
MAX_RESPONSE_LENGTH = 1900

def get_today_key():
    return datetime.utcnow().strftime("%Y-%m-%d")

def check_rate_limit(user_id, is_admin=False):
    if is_admin:
        return True, MAX_REQUESTS_PER_DAY
    today = get_today_key()
    key = f"{user_id}_{today}"
    if key not in user_requests:
        user_requests[key] = 0
    remaining = MAX_REQUESTS_PER_DAY - user_requests[key]
    return remaining > 0, remaining

def increment_request(user_id):
    today = get_today_key()
    key = f"{user_id}_{today}"
    user_requests[key] = user_requests.get(key, 0) + 1

def add_to_history(channel_id, role, content):
    if channel_id not in channel_history:
        channel_history[channel_id] = []
    channel_history[channel_id].append({"role": role, "content": content})
    if len(channel_history[channel_id]) > MAX_HISTORY:
        channel_history[channel_id] = channel_history[channel_id][-MAX_HISTORY:]

def get_channel_history(channel_id):
    return channel_history.get(channel_id, [])

def clear_channel_history(channel_id):
    if channel_id in channel_history:
        channel_history[channel_id] = []

def set_personality(channel_id, personality):
    channel_personality[channel_id] = personality

def get_personality(channel_id):
    return channel_personality.get(channel_id, "")

def reset_personality(channel_id):
    if channel_id in channel_personality:
        del channel_personality[channel_id]

def query_huggingface(question, channel_id):
    try:
        history = get_channel_history(channel_id)
        personality = get_personality(channel_id)
        
        context = ""
        if personality:
            context = f"[Инструкция: {personality}]\n\n"
        
        for msg in history[-5:]:
            context += f"{msg['role']}: {msg['content']}\n"
        
        context += f"user: {question}\nassistant:"
        
        headers = {
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "inputs": context,
            "parameters": {"max_length": 200, "temperature": 0.7}
        }
        
        response = requests.post(HF_API_URL, json=payload, headers=headers, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if isinstance(result, list) and len(result) > 0:
                answer = result[0].get("generated_text", "").strip()
                if "assistant:" in answer:
                    answer = answer.split("assistant:")[-1].strip()
                return answer[:MAX_RESPONSE_LENGTH] if answer else "Не смог сформировать ответ."
            return "Ошибка при обработке ответа API."
        else:
            return f"❌ Ошибка API: {response.status_code}. Попробуй позже."
    
    except requests.exceptions.Timeout:
        return "❌ Timeout: API не ответил за 30 секунд. Попробуй позже."
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def search_duckduckgo(query):
    try:
        ddgs = DDGS()
        results = list(ddgs.text(query, max_results=3))
        ddgs.close()
        
        if not results:
            return "Ничего не найдено."
        
        response = "🔍 **Результаты поиска:**\n\n"
        for i, result in enumerate(results, 1):
            title = result.get("title", "Без названия")
            link = result.get("href", "#")
            body = result.get("body", "Нет описания")[:150]
            response += f"{i}. **{title}**\n{body}...\n🔗 {link}\n\n"
        
        return response[:MAX_RESPONSE_LENGTH]
    
    except Exception as e:
        return f"❌ Ошибка поиска: {str(e)}"

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Бот {bot.user} запущен!")
        print(f"✅ Синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"❌ Ошибка синхронизации команд: {e}")

@bot.tree.command(name="ask", description="Задать вопрос AI")
@app_commands.describe(question="Твой вопрос")
async def ask_command(interaction: discord.Interaction, question: str):
    await interaction.response.defer()
    
    user_id = interaction.user.id
    channel_id = interaction.channel_id
    is_admin = interaction.user.guild_permissions.administrator
    
    allowed, remaining = check_rate_limit(user_id, is_admin)
    if not allowed:
        await interaction.followup.send("❌ Ты превысил лимит 15 запросов в день. Попробуй завтра!")
        return
    
    add_to_history(channel_id, "user", question)
    answer = query_huggingface(question, channel_id)
    add_to_history(channel_id, "assistant", answer)
    increment_request(user_id)
    
    await interaction.followup.send(f"🤖 **Ответ:**\n{answer}")

@bot.tree.command(name="search", description="Поиск в интернете")
@app_commands.describe(query="Что искать?")
async def search_command(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    
    user_id = interaction.user.id
    is_admin = interaction.user.guild_permissions.administrator
    
    allowed, remaining = check_rate_limit(user_id, is_admin)
    if not allowed:
        await interaction.followup.send("❌ Ты превысил лимит 15 запросов в день. Попробуй завтра!")
        return
    
    results = search_duckduckgo(query)
    increment_request(user_id)
    
    await interaction.followup.send(results)

@bot.tree.command(name="reset", description="Очистить историю диалога")
async def reset_command(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    clear_channel_history(channel_id)
    await interaction.response.send_message("✅ История диалога очищена!")

@bot.tree.command(name="stats", description="Показать статистику запросов")
async def stats_command(interaction: discord.Interaction):
    user_id = interaction.user.id
    is_admin = interaction.user.guild_permissions.administrator
    
    if is_admin:
        await interaction.response.send_message("👑 Ты администратор — **безлимитные запросы**!")
        return
    
    today = get_today_key()
    key = f"{user_id}_{today}"
    used = user_requests.get(key, 0)
    remaining = MAX_REQUESTS_PER_DAY - used
    
    await interaction.response.send_message(
        f"📊 **Твоя статистика на сегодня:**\n"
        f"Использовано: {used}/{MAX_REQUESTS_PER_DAY}\n"
        f"Осталось: {remaining}"
    )

@bot.tree.command(name="history", description="Установить персонаж бота")
@app_commands.describe(text="Описание персонажа")
async def history_command(interaction: discord.Interaction, text: str):
    channel_id = interaction.channel_id
    set_personality(channel_id, text)
    await interaction.response.send_message(f"✅ Персонаж установлен: *{text}*")

@bot.tree.command(name="resethistory", description="Сбросить персонаж бота")
async def resethistory_command(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    reset_personality(channel_id)
    await interaction.response.send_message("✅ Персонаж сброшен на стандартный!")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
