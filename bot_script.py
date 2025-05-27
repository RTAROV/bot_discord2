import discord
from discord.ext import commands
from discord.ui import View, Select
from datetime import datetime, timedelta, timezone
import json
import os
import random
import asyncio
from difflib import get_close_matches
import requests
import logging
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from collections import defaultdict
import time

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Configuration ---
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DATA_FILE = DATA_DIR / "user_data.json"
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Check for Discord token
if not DISCORD_TOKEN:
    logger.error("DISCORD_TOKEN not found in environment variables!")
    logger.error("Please create a .env file with: DISCORD_TOKEN=your_bot_token_here")
    exit(1)

# --- Rate limiting ---
user_last_command = defaultdict(float)
user_command_count = defaultdict(int)

def is_rate_limited(user_id, cooldown=3):
    """Simple rate limiting to prevent spam"""
    now = time.time()
    if now - user_last_command[user_id] < cooldown:
        return True
    user_last_command[user_id] = now
    return False

def validate_user_input(text, max_length=500):
    """Validate user input"""
    if not text or len(text) > max_length:
        return False
    return True

# --- Intents ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- User data storage ---
user_data = {}

# --- Enhanced data management ---
def load_data():
    """Load user data with error handling"""
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(f"Loaded data for {len(data)} users")
                return data
        logger.info("No existing data file found, starting fresh")
        return {}
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Error loading data: {e}")
        # Backup corrupted file
        if DATA_FILE.exists():
            backup_file = DATA_DIR / f"user_data_backup_{int(time.time())}.json"
            try:
                DATA_FILE.rename(backup_file)
                logger.info(f"Corrupted file backed up to {backup_file}")
            except Exception as backup_error:
                logger.error(f"Failed to backup corrupted file: {backup_error}")
        return {}

def save_data():
    """Save user data with error handling and backup"""
    try:
        # Create backup before saving
        if DATA_FILE.exists():
            backup_file = DATA_DIR / "user_data_backup.json"
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                with open(backup_file, "w", encoding="utf-8") as backup:
                    backup.write(f.read())
        
        # Save new data
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
        logger.debug("Data saved successfully")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def ensure_user_data(user_id):
    """Initialize user data if not exists"""
    uid = str(user_id)
    if uid not in user_data:
        user_data[uid] = {
            "money": 0,
            "level": 1,
            "exp": 0,
            "inventory": [],
            "last_daily": None,
            "item": "",
            "total_online": 0,
            "last_online": None,
            "join_date": datetime.now(timezone.utc).isoformat(),
            "command_usage": 0
        }
        logger.info(f"Created new user data for {uid}")
    return user_data[uid]

# --- Bot events ---
@bot.event
async def on_ready():
    global user_data
    user_data = load_data()
    logger.info(f"Bot logged in as {bot.user.name} - Serving {len(bot.guilds)} servers")
    
    # Set bot status
    try:
        activity = discord.Activity(type=discord.ActivityType.watching, name="เซิร์ฟเวอร์ | !help")
        await bot.change_presence(activity=activity)
        logger.info("Bot status set successfully")
    except Exception as e:
        logger.error(f"Failed to set bot status: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully"""
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ รอสักครู่นะคะ อีก {error.retry_after:.1f} วินาที")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ คำสั่งไม่ครบค่ะ ลองดู !help")
    elif isinstance(error, commands.CommandNotFound):
        # Silent ignore for unknown commands
        pass
    else:
        logger.error(f"Command error in {ctx.command}: {error}")
        await ctx.send("❌ เกิดข้อผิดพลาด ลองใหม่อีกครั้งนะคะ")

# --- Status selection menu ---
class ItemSelect(Select):
    def __init__(self, user_id):
        self.user_id = str(user_id)
        options = [
            discord.SelectOption(label="มีแฟน", emoji="❤️", description="สถานะ: มีแฟนแล้ว"),
            discord.SelectOption(label="มีคนคุย", emoji="😊", description="สถานะ: กำลังมีคนคุย"),
            discord.SelectOption(label="โสดเว้ย", emoji="🧪", description="สถานะ: โสดหาคู่")
        ]
        super().__init__(placeholder="เลือกสถานะของคุณ...", options=options)

    async def callback(self, interaction: discord.Interaction):
        try:
            uid = self.user_id
            user = ensure_user_data(uid)
            user["item"] = self.values[0]
            user["command_usage"] += 1
            save_data()
            
            embed = discord.Embed(
                title="✅ อัพเดทสถานะสำเร็จ!",
                description=f"สถานะของคุณ: **{self.values[0]}**",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error in ItemSelect callback: {e}")
            await interaction.response.send_message("❌ เกิดข้อผิดพลาด กรุณาลองใหม่", ephemeral=True)

class ItemView(View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.add_item(ItemSelect(user_id))
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

# --- Commands ---
@bot.command(name="โชว์โปรไฟล์")
async def setup_profile(ctx):
    """Setup user profile status"""
    if is_rate_limited(ctx.author.id):
        await ctx.send("⏳ รอสักครู่นะคะ")
        return
    
    view = ItemView(ctx.author.id)
    embed = discord.Embed(
        title="🔧 ตั้งค่าโปรไฟล์",
        description="กรุณาเลือกสถานะของคุณ:",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=view)

@bot.command(name="เช็คโปรไฟล์")
async def check_profile(ctx, member: discord.Member = None):
    """Check user profile"""
    if is_rate_limited(ctx.author.id):
        await ctx.send("⏳ รอสักครู่นะคะ")
        return
    
    target = member or ctx.author
    uid = str(target.id)
    user = ensure_user_data(uid)
    user["command_usage"] += 1

    # Calculate online time
    total_seconds = user.get("total_online", 0)
    if user.get("last_online"):
        try:
            last_time = datetime.fromisoformat(user["last_online"])
            total_seconds += int((datetime.now(timezone.utc) - last_time).total_seconds())
        except ValueError:
            logger.warning(f"Invalid last_online format for user {uid}")

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    time_str = f"{hours} ชั่วโมง {minutes} นาที"

    embed = discord.Embed(
        title=f"📊 โปรไฟล์ของ {target.display_name}",
        color=discord.Color.green()
    )
    embed.add_field(name="💝 สถานะ", value=user.get("item", "ยังไม่ได้เลือก"), inline=True)
    embed.add_field(name="💰 เงิน", value=f'{user.get("money", 0):,} เหรียญ', inline=True)
    embed.add_field(name="⭐ เลเวล", value=user.get("level", 1), inline=True)
    embed.add_field(name="🕒 เวลาออนไลน์รวม", value=time_str, inline=False)
    embed.add_field(name="🎮 ใช้คำสั่ง", value=f'{user.get("command_usage", 0)} ครั้ง', inline=True)
    
    if user.get("inventory"):
        items = ", ".join(user["inventory"][:5])  # Show first 5 items
        if len(user["inventory"]) > 5:
            items += f" และอีก {len(user['inventory']) - 5} รายการ"
        embed.add_field(name="🎒 ไอเทม", value=items, inline=False)
    
    embed.set_thumbnail(url=target.display_avatar.url)
    if target.joined_at:
        embed.set_footer(text=f"เข้าร่วมเซิร์ฟเวอร์: {target.joined_at.strftime('%d/%m/%Y')}")

    save_data()
    await ctx.send(embed=embed)

@bot.command(name="รับรางวัลประจำวัน")
@commands.cooldown(1, 86400, commands.BucketType.user)  # Once per day
async def daily_reward(ctx):
    """Daily reward system"""
    user_id = str(ctx.author.id)
    user = ensure_user_data(user_id)

    # Check if already claimed today
    if user["last_daily"]:
        try:
            last_daily = datetime.fromisoformat(user["last_daily"])
            if (datetime.now(timezone.utc) - last_daily) < timedelta(days=1):
                remaining = timedelta(days=1) - (datetime.now(timezone.utc) - last_daily)
                hours = remaining.seconds // 3600
                minutes = (remaining.seconds % 3600) // 60
                await ctx.send(f"⏳ คุณรับรางวัลประจำวันไปแล้ว! กลับมาอีก {hours} ชั่วโมง {minutes} นาที")
                return
        except ValueError:
            logger.warning(f"Invalid last_daily format for user {user_id}")

    # Calculate reward based on level
    base_reward = 100
    level_bonus = user.get("level", 1) * 10
    total_reward = base_reward + level_bonus

    user["money"] += total_reward
    user["exp"] += 25
    user["last_daily"] = datetime.now(timezone.utc).isoformat()
    user["command_usage"] += 1

    # Level up check
    level_up_exp = user["level"] * 100
    if user["exp"] >= level_up_exp:
        user["level"] += 1
        user["exp"] = 0
        level_up_bonus = user["level"] * 50
        user["money"] += level_up_bonus
        
        embed = discord.Embed(
            title="🎉 รับรางวัลประจำวันสำเร็จ!",
            description=f"💰 ได้รับ {total_reward:,} เหรียญ\n⭐ **เลเวลอัพ!** ตอนนี้เลเวล {user['level']}\n💎 โบนัสเลเวล: {level_up_bonus:,} เหรียญ",
            color=discord.Color.gold()
        )
    else:
        embed = discord.Embed(
            title="🎉 รับรางวัลประจำวันสำเร็จ!",
            description=f"💰 ได้รับ {total_reward:,} เหรียญ\n✨ ได้รับ 25 EXP",
            color=discord.Color.green()
        )
    
    embed.add_field(name="💰 เงินรวม", value=f"{user['money']:,} เหรียญ", inline=True)
    embed.add_field(name="⭐ เลเวล", value=user['level'], inline=True)
    embed.set_footer(text="กลับมารับรางวัลใหม่พรุ่งนี้นะคะ!")

    save_data()
    await ctx.send(embed=embed)

@bot.command(name="สุ่มของ")
@commands.cooldown(3, 3600, commands.BucketType.user)  # 3 times per hour
async def gacha(ctx):
    """Gacha system for random items"""
    if is_rate_limited(ctx.author.id):
        await ctx.send("⏳ รอสักครู่นะคะ")
        return

    user_id = str(ctx.author.id)
    user = ensure_user_data(user_id)
    
    cost = 50
    if user["money"] < cost:
        await ctx.send(f"💸 เงินไม่พอค่ะ ต้องใช้ {cost} เหรียญ (คุณมี {user['money']} เหรียญ)")
        return

    user["money"] -= cost
    user["command_usage"] += 1

    # Weighted item list
    items = [
        ("🍞 ขนมปัง", 30),
        ("🧪 พลังฟื้นฟู", 25),
        ("💎 เพชรเล็ก", 20),
        ("🎁 กล่องลึกลับ", 15),
        ("⚡ กระสุนไม่จำกัด", 8),
        ("💳 บัตรเงิน", 2)
    ]
    
    # Weighted random selection
    total_weight = sum(weight for _, weight in items)
    r = random.randint(1, total_weight)
    current_weight = 0
    
    for item, weight in items:
        current_weight += weight
        if r <= current_weight:
            selected_item = item
            break

    if "inventory" not in user:
        user["inventory"] = []
    user["inventory"].append(selected_item)

    # Special bonus for rare items
    bonus_money = 0
    if "บัตรเงิน" in selected_item:
        bonus_money = random.randint(100, 500)
        user["money"] += bonus_money

    embed = discord.Embed(
        title="🎰 ผลการสุ่มไอเทม",
        description=f"🎁 คุณได้รับ: **{selected_item}**!",
        color=discord.Color.purple()
    )
    
    if bonus_money:
        embed.add_field(name="💰 โบนัส!", value=f"ได้เงินเพิ่ม {bonus_money:,} เหรียญ", inline=False)
    
    embed.add_field(name="💰 เงินคงเหลือ", value=f"{user['money']:,} เหรียญ", inline=True)
    embed.add_field(name="🎒 ไอเทมทั้งหมด", value=f"{len(user['inventory'])} ชิ้น", inline=True)

    save_data()
    await ctx.send(embed=embed)

@bot.command(name="อันดับ")
async def leaderboard(ctx, category="money"):
    """Display leaderboard"""
    if is_rate_limited(ctx.author.id, cooldown=10):
        await ctx.send("⏳ รอสักครู่นะคะ")
        return

    valid_categories = ["money", "level", "online"]
    if category not in valid_categories:
        category = "money"

    if category == "money":
        top_users = sorted(user_data.items(), key=lambda x: x[1].get("money", 0), reverse=True)
        title = "🏆 อันดับผู้ที่รวยที่สุด"
        emoji = "💰"
        field_name = "เหรียญ"
    elif category == "level":
        top_users = sorted(user_data.items(), key=lambda x: x[1].get("level", 1), reverse=True)
        title = "⭐ อันดับเลเวลสูงสุด"
        emoji = "⭐"
        field_name = "เลเวล"
    else:  # online
        top_users = sorted(user_data.items(), key=lambda x: x[1].get("total_online", 0), reverse=True)
        title = "🕒 อันดับออนไลน์นานที่สุด"
        emoji = "🕒"
        field_name = "ชั่วโมง"

    embed = discord.Embed(title=title, color=discord.Color.gold())
    
    for i, (uid, data) in enumerate(top_users[:10]):
        if i >= 10:
            break
        try:
            user = await bot.fetch_user(int(uid))
            if category == "online":
                hours = data.get("total_online", 0) // 3600
                value = f"{hours}"
            else:
                value = f"{data.get(category, 0):,}"
            
            embed.add_field(
                name=f"{i+1}. {user.display_name}",
                value=f"{emoji} {value} {field_name}",
                inline=False
            )
        except Exception as e:
            logger.warning(f"Could not fetch user {uid}: {e}")
            continue

    if not embed.fields:
        embed.description = "ยังไม่มีข้อมูลในอันดับนี้"

    await ctx.send(embed=embed)

# --- Enhanced AI Chat ---
@bot.command(name="ถาม")
@commands.cooldown(5, 60, commands.BucketType.user)  # 5 times per minute
async def ask_ai(ctx, *, prompt):
    """AI chat using Ollama"""
    if not validate_user_input(prompt):
        await ctx.send("❌ คำถามไม่ถูกต้องค่ะ")
        return

    user_id = str(ctx.author.id)
    user = ensure_user_data(user_id)
    user["command_usage"] += 1

    # Show typing indicator
    async with ctx.typing():
        data = {
            "model": "llama3",
            "messages": [
                {"role": "system", "content": "คุณคือมิเชล บอทสาวน่ารักใน Discord ตอบกลับแบบเป็นมิตรและสนุกสนาน ใช้ภาษาไทย"},
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }

        try:
            res = requests.post(f"{OLLAMA_URL}/api/chat", json=data, timeout=30)
            res.raise_for_status()

            response_data = res.json()
            response = response_data.get("message", {}).get("content", "").strip()

            if response:
                # Split long responses
                if len(response) > 2000:
                    chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            await ctx.send(f"🤖 **มิเชลตอบ:**\n{chunk}")
                        else:
                            await ctx.send(chunk)
                else:
                    embed = discord.Embed(
                        title="🤖 มิเชลตอบ",
                        description=response,
                        color=discord.Color.blue()
                    )
                    embed.set_footer(text=f"ถามโดย {ctx.author.display_name}")
                    await ctx.send(embed=embed)
            else:
                await ctx.send("❌ ขอโทษค่ะ มิเชลตอบไม่ได้ในตอนนี้ 😢")

        except requests.exceptions.ConnectionError:
            await ctx.send("❌ ไม่สามารถเชื่อมต่อ AI ได้ค่ะ กรุณาลองใหม่ภายหลัง")
            logger.error("Cannot connect to Ollama server")
        except requests.exceptions.Timeout:
            await ctx.send("⏳ AI ตอบช้าเกินไป ลองถามใหม่ภายหลังนะคะ")
        except Exception as e:
            logger.error(f"AI API error: {e}")
            await ctx.send("❌ เกิดข้อผิดพลาดขณะติดต่อ AI")

    save_data()

# --- Enhanced presence tracking ---
@bot.event
async def on_presence_update(before, after):
    """Track user online time"""
    try:
        uid = str(after.id)
        now = datetime.now(timezone.utc)

        if uid not in user_data:
            ensure_user_data(uid)

        # User came online
        if before.status != discord.Status.online and after.status == discord.Status.online:
            user_data[uid]["last_online"] = now.isoformat()
            logger.debug(f"User {uid} came online")
        
        # User went offline
        elif before.status == discord.Status.online and after.status != discord.Status.online:
            if user_data[uid].get("last_online"):
                try:
                    last_online = datetime.fromisoformat(user_data[uid]["last_online"])
                    online_time = int((now - last_online).total_seconds())
                    user_data[uid]["total_online"] += online_time
                    user_data[uid]["last_online"] = None
                    logger.debug(f"User {uid} was online for {online_time} seconds")
                except ValueError:
                    logger.warning(f"Invalid last_online format for user {uid}")
    except Exception as e:
        logger.error(f"Error in presence update: {e}")

# --- Enhanced FAQ system ---
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Process commands first
    await bot.process_commands(message)

    # Enhanced FAQ with fuzzy matching
    faq = {
        "สวัสดี": "สวัสดีค่ะ! ยินดีต้อนรับสู่เซิร์ฟเวอร์ของเรา! 🎉",
        "มิเชล": "ว่างัยคะ? มีอะไรให้ช่วยไหม? 😊",
        "คุณชื่ออะไร": "ชื่อของฉันคือ มิเชลค่ะ! น่ารักมั้ย? 💕",
        "คุณทำอะไรได้บ้าง": "ฉันสามารถช่วยตอบคำถาม เล่นเกม และคุยเป็นเพื่อนได้ค่ะ! ลองใช้ !help ดูสิคะ",
        "help": "พิมพ์ !help เพื่อดูคำสั่งทั้งหมดค่ะ!",
        "มิเชลมีแฟนไหม": "มีแล้วคะ ชื่อชิริวค่ะ หล่อมากเลย! 😍",
        "ใครหล่อที่สุด": "Poom คะ หล่อมากๆ หล่อที่สุดเลยค่ะ! ✨",
        "ขอเป็นแฟนได้ไหม": "ได้คะ ถ้าหล่อๆ นะ! 😉",
        "คิดถึงเราไหม": "คิดถึงคะ! คิดถึงทุกคนในเซิร์ฟเวอร์เลย 💖",
        "เราหล่อไหม": "หล่อค่ะ! แต่ไม่ได้ครึ่งแฟนหนู 😘",
        "เราสวยไหม": "สวยมากคะ! แต่ไม่ได้ครึ่งหนูนะ 😊",
        "กินไรดี": "กินข้าวคะ! หรือจะกินใจหนูก็ได้ 💕",
        "ทำไรดี": "เล่นเกมคะ! หรือมาคุยกับหนูก็ได้ 🎮",
        "ท้อ": "สู้ๆ นะคะ! หนูเชื่อในตัวคุณ! 💪",
        "ใครฉลาดที่สุด": "หนูคะ! เพราะหนูตั้งใจเรียนมาก 🤓",
        "ใครควายที่สุด": "ไม่บอกหรอก! แต่คนที่ถามคำถามนี้น่าสงสัย 🤔",
        "มิเชลชอบกินอะไร": "ชอบกินเค้กค่ะ! หวานๆ เหมือนหนู 🍰",
        "มิเชลชอบไปไหน": "ชอบไปทะเลค่ะ! อยากไปกับแฟน 🏖️"
    }

    msg = message.content.lower().strip()
    
    # Skip if message is too short or starts with command prefix
    if len(msg) < 2 or msg.startswith('!'):
        return
    
    # Find close matches
    try:
        matched = get_close_matches(msg, faq.keys(), n=1, cutoff=0.6)
        
        if matched:
            response = faq[matched[0]]
            
            # Add some personality with random reactions
            if random.random() < 0.1:  # 10% chance
                reactions = ["😊", "💕", "✨", "🎉", "😘"]
                try:
                    await message.add_reaction(random.choice(reactions))
                except Exception as e:
                    logger.debug(f"Failed to add reaction: {e}")
            
            await message.channel.send(response)
    except Exception as e:
        logger.error(f"Error in FAQ system: {e}")

# --- Additional utility commands ---
# แก้ไขจากบรรทัดสุดท้ายของไฟล์เดิม

@bot.command(name="ต้องการความช่วยเหลือ")
async def help_command(ctx):
    """Show help information"""
    embed = discord.Embed(
        title="🤖 คำสั่งของมิเชล",
        description="รายการคำสั่งทั้งหมดที่ใช้ได้",
        color=discord.Color.blue()
    )
    embed.add_field(name="!โชว์โปรไฟล์", value="ตั้งค่าสถานะความสัมพันธ์", inline=False)
    embed.add_field(name="!เช็คโปรไฟล์", value="ดูโปรไฟล์ตัวเองหรือคนอื่น", inline=False)
    embed.add_field(name="!รับรางวัลประจำวัน", value="รับเหรียญและ EXP ฟรีทุกวัน", inline=False)
    embed.add_field(name="!สุ่มของ", value="สุ่มไอเทมลึกลับ", inline=False)
    embed.add_field(name="!อันดับ", value="ดูอันดับผู้เล่น", inline=False)
    embed.add_field(name="!ถาม [คำถาม]", value="ถาม AI มิเชล", inline=False)
    await ctx.send(embed=embed)

# ---------------------
# เปลี่ยนจากรันด้วย token ตรงๆ เป็นใช้ DISCORD_TOKEN จาก .env
# เดิม:
# bot.run("MTM2NzE5NjM4MDE0NTEyNzUyNQ.GU7-Mo.8a35FdnJRPQCYEDjUm_anVG_H9VMt6OlGgyRiQ")

# ใหม่:
bot.run(DISCORD_TOKEN)

    

bot.run("MTM2NzE5NjM4MDE0NTEyNzUyNQ.GU7-Mo.8a35FdnJRPQCYEDjUm_anVG_H9VMt6OlGgyRiQ")

  