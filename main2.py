import os
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import random
import re
from cryptography.fernet import Fernet
import aiohttp
# ==================================================
# ✅ タイムゾーンをJSTに統一（全ての時間はこれを使う）
# ==================================================
JST = timezone(timedelta(hours=9))
RANKING_CHANNEL_ID = 1542491472862117899  # 🏆 ランキング公開チャンネル　pt,xpランキングのところ
# ========== ✅ 設定 ==========
ADMIN_ROLE_NAME = "TISN管理者"
ADMIN_CHANNEL_ID = 1540618118773084204 #モデレーター専用部屋
TOKEN_TRADE_PRICE = 250
COMMAND_PREFIX = "!"
# ✅ 通常階級：低い順
ROLE_ORDER = [
    "一等兵",
    "上等兵",
    "伍長",
    "曹長",
    "大尉",
    "少佐",
    "ムスカ大佐",
    "TISN最高幹部",
    "TISN管理者"
]
ROLE_PRICES = {
    "一等兵": 500,
    "上等兵": 1000,
    "伍長": 5000,
    "曹長": 10000,
    "大尉": 30000,
    "少佐": 50000,
    "ムスカ大佐": 200000,
    "TISN最高幹部": 500000,
    "TISN管理者": 1000000
}
# ✅ 技術班階級：低い順
TECH_ROLE_ORDER = [
    "技術班",
    "高度技術班",
    "技術班最高幹部"
]
TECH_ROLE_COST_XP = {
    "技術班": 500,
    "高度技術班": 7500,
    "技術班最高幹部": 50000
}
# ==================================================
# ✅ 権限ロール設定（個別購入可・前の階級不要）
# ==================================================
PERM_ROLE_PRICES = {
    "【権限】メッセージ管理": 40000,
    "【権限】広報局発言権": 60000,
    "【権限】メンバータイムアウト": 60000,
    "【権限】キック": 100000,
    "【権限】BAN": 400000
}
# ✅ 管理者が選べるXP額一覧
XP_OPTIONS = [0, 100, 150, 200, 300, 500, 750, 1000, 2000, 5000, 10000]
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.dm_messages = True
intents.members = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
# ========== ✅ 暗号化キー管理 ==========
def get_or_create_cipher():
    key_env = os.environ.get("TOKEN_ENCRYPT_KEY")
    if key_env:
        key = key_env.encode()
    else:
        key_file = Path(".token_key")
        if key_file.exists():
            key = key_file.read_bytes()
        else:
            key = Fernet.generate_key()
            key_file.write_bytes(key)
            print(f"🔑 新規暗号化キー生成: {key.decode()}")
            print("⚠️ このキーを失うと保存中のデータは復元できません！")
    return Fernet(key)
CIPHER = get_or_create_cipher()
def encrypt_token(raw_token: str) -> str:
    return CIPHER.encrypt(raw_token.encode()).decode()
def decrypt_token(encrypted_token: str) -> str:
    return CIPHER.decrypt(encrypted_token.encode()).decode()
# ========== ✅ データファイル管理（PT・XPは暗号化版） ==========
DATA_FILE = Path("points_data_encrypted.json")
IMAGE_LOG_FILE = Path("image_log.json")
PENDING_FILE = Path("pending_requests.json")
XP_PENDING_FILE = Path("xp_pending.json")
TEMP_DM_FILE = Path("temp_dm_state.json")
TOKEN_MARKET_FILE = Path("token_market_encrypted.json")
# ✅ 暗号化して保存
def save_json_encrypted(path, data):
    json_text = json.dumps(data, ensure_ascii=False, indent=2)
    encrypted = CIPHER.encrypt(json_text.encode("utf-8")).decode()
    path.write_text(encrypted, encoding="utf-8")
# ✅ 復号して読み込み
def load_json_encrypted(path):
    if not path.exists():
        return {}
    encrypted_text = path.read_text(encoding="utf-8").strip()
    if not encrypted_text:
        return {}
    try:
        decrypted = CIPHER.decrypt(encrypted_text.encode("utf-8")).decode()
        return json.loads(decrypted)
    except Exception as e:
        print(f"⚠️ 復号エラー {path.name}: {e}")
        print("💡 暗号化キーが正しいか確認してください！")
        return {}
# ✅ 暗号化しない通常ファイル
def load_json(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}
def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
# ✅ 初期ファイル作成
for f in [IMAGE_LOG_FILE, PENDING_FILE, XP_PENDING_FILE, TEMP_DM_FILE]:
    if not f.exists():
        f.write_text("{}", encoding="utf-8")
if not DATA_FILE.exists():
    save_json_encrypted(DATA_FILE, {})
if not TOKEN_MARKET_FILE.exists():
    TOKEN_MARKET_FILE.write_text("{}", encoding="utf-8")
# ==================================================
# ✅ 共通関数：PT・XP変更時に本人と管理者へ通知
# ==================================================
async def send_value_change_notice(user_id: str, mode: str, before: int, after: int, reason: str = ""):
    diff = after - before
    sign = "+" if diff >= 0 else ""
    notice_text = f"""📊 【{mode.upper()} 値が更新されました】
変化前: {before} {mode.upper()} → 変化後: {after} {mode.upper()}
変更量: {sign}{diff} {mode.upper()}"""
    if reason:
        notice_text += f"\n📝 理由: {reason}"
    # ① 本人にDM送信
    try:
        target_user = await bot.fetch_user(int(user_id))
        if target_user:
            await target_user.send(notice_text)
    except Exception:
        pass
    # ② 管理者チャンネルに送信
    try:
        admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_ch:
            user_name = ""
            try:
                u = await bot.fetch_user(int(user_id))
                user_name = f"{u.name} （ID:{user_id}）"
            except:
                user_name = f"ID:{user_id}"
            admin_notice = f"""📊 【{mode.upper()} 更新通知】
対象: {user_name}
変化前: {before} {mode.upper()} → 変化後: {after} {mode.upper()}
変更量: {sign}{diff} {mode.upper()}"""
            if reason:
                admin_notice += f"\n📝 理由: {reason}"
            await admin_ch.send(admin_notice)
    except Exception:
        pass
# ==================================================
# ✅ ランキング生成 共通関数
# ==================================================
async def build_ranking_embed():
    now = datetime.now(JST)
    data = load_json_encrypted(DATA_FILE)
    if not data:
        return discord.Embed(title="🏆 TISNランキング", description="📭 まだデータがありません。", color=0xFFD700)
    xp_ranking = sorted(data.items(), key=lambda x: x[1].get("xp", 0), reverse=True)[:10]
    pt_ranking = sorted(data.items(), key=lambda x: x[1].get("points", 0), reverse=True)[:10]
    xp_text = ""
    for i, (uid, d) in enumerate(xp_ranking, 1):
        try:
            user = await bot.fetch_user(int(uid))
            name = user.name if user else f"User{uid[:6]}"
        except:
            name = f"User{uid[:6]}"
        xp_text += f"**{i}.** {name} → **{d.get('xp', 0)} XP**\n"
    pt_text = ""
    for i, (uid, d) in enumerate(pt_ranking, 1):
        try:
            user = await bot.fetch_user(int(uid))
            name = user.name if user else f"User{uid[:6]}"
        except:
            name = f"User{uid[:6]}"
        pt_text += f"**{i}.** {name} → **{d.get('points', 0)} PT**\n"
    embed = discord.Embed(
        title="🏆 TISN ランキング発表",
        description=f"📅 {now.strftime('%Y/%m/%d %H:%M')} 現在\n✨ 上位10名を表示",
        color=0xFFD700
    )
    embed.add_field(name="✨ XP ランキング TOP10", value=xp_text or "データなし", inline=False)
    embed.add_field(name="💰 PT ランキング TOP10", value=pt_text or "データなし", inline=False)
    embed.set_footer(text="毎日 0:00 に自動更新 | !ranking で再表示")
    return embed
# ==================================================
# ✅ 手動ランキングコマンド
# ==================================================
@bot.command(name="ranking")
async def show_ranking(ctx):
    embed = await build_ranking_embed()
    await ctx.send(embed=embed)
# ==================================================
# ✅ 毎日 0:00 JST に正確に自動ランキング
# ==================================================
@tasks.loop(time=datetime.time(datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)))
async def daily_ranking_task():
    """✅ 毎日日本時間0:00に正確に実行（24時間ずれる問題を完全解消）"""
    print("🏆 定刻：ランキング自動更新を実行")
    ranking_channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not ranking_channel:
        print("⚠️ ランキングチャンネルが見つかりません")
        return
    embed = await build_ranking_embed()
    await ranking_channel.send(embed=embed)
    print("✅ ランキングを送信完了")
# ==================================================
# ✅ 管理者専用：全員のPTを一括で増減
# ==================================================
@bot.command(name="edit_all_pt")
async def edit_all_pt(ctx, amount: int, *, reason: str = "理由なし"):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return
    data = load_json_encrypted(DATA_FILE)
    if not data:
        await ctx.send("📭 ユーザーデータが存在しません。", delete_after=5)
        return
    count = 0
    for user_id, u_data in data.items():
        before = u_data.get("points", 0)
        after = before + amount
        u_data["points"] = after
        data[user_id] = u_data
        await send_value_change_notice(user_id, "pt", before, after, reason)
        count += 1
    save_json_encrypted(DATA_FILE, data)
    await ctx.send(f"✅ 全{count}名のPTを {amount:+d} PT に変更しました。\n📝 理由：{reason}", delete_after=15)
# ==================================================
# ✅ 管理者専用：全員のXPを一括で増減
# ==================================================
@bot.command(name="edit_all_xp")
async def edit_all_xp(ctx, amount: int, *, reason: str = "理由なし"):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return
    data = load_json_encrypted(DATA_FILE)
    if not data:
        await ctx.send("📭 ユーザーデータが存在しません。", delete_after=5)
        return
    count = 0
    for user_id, u_data in data.items():
        before = u_data.get("xp", 0)
        after = before + amount
        u_data["xp"] = after
        data[user_id] = u_data
        await send_value_change_notice(user_id, "xp", before, after, reason)
        count += 1
    save_json_encrypted(DATA_FILE, data)
    await ctx.send(f"✅ 全{count}名のXPを {amount:+d} XP に変更しました。\n📝 理由：{reason}", delete_after=15)
# ==================================================
# ✅ 管理者用 個別ユーザー PT操作
# ==================================================
@bot.command(name="edit_pt")
async def edit_pt(ctx, member: discord.Member, amount: int, *, reason: str = "理由なし"):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return
    data = load_json_encrypted(DATA_FILE)
    user_id = str(member.id)
    if user_id not in data:
        data[user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
    before = data[user_id].get("points", 0)
    after = before + amount
    data[user_id]["points"] = after
    save_json_encrypted(DATA_FILE, data)
    await send_value_change_notice(user_id, "pt", before, after, reason)
    await ctx.send(
        f"✅ {member.mention} のPTを {before} → {after} に変更しました（{amount:+d} PT）\n"
        f"📝 理由：{reason}",
        delete_after=15
    )
# ==================================================
# ✅ 管理者用 個別ユーザー XP操作
# ==================================================
@bot.command(name="edit_xp")
async def edit_xp(ctx, member: discord.Member, amount: int, *, reason: str = "理由なし"):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return
    data = load_json_encrypted(DATA_FILE)
    user_id = str(member.id)
    if user_id not in data:
        data[user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
    before = data[user_id].get("xp", 0)
    after = before + amount
    data[user_id]["xp"] = after
    save_json_encrypted(DATA_FILE, data)
    await send_value_change_notice(user_id, "xp", before, after, reason)
    await ctx.send(
        f"✅ {member.mention} のXPを {before} → {after} に変更しました（{amount:+d} XP）\n"
        f"📝 理由：{reason}",
        delete_after=15
    )
# ==================================================
# ✅ 管理者用 ロール指定 PT一括変更
# ==================================================
@bot.command(name="edit_pt_role")
async def edit_pt_role(ctx, role_name: str, amount: int, *, reason: str = "理由なし"):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return
    guild = ctx.guild
    target_role = discord.utils.get(guild.roles, name=role_name)
    if not target_role:
        await ctx.send(f"❌ ロール「{role_name}」が見つかりません。", delete_after=10)
        return
    data = load_json_encrypted(DATA_FILE)
    count = 0
    for member in target_role.members:
        user_id = str(member.id)
        if user_id not in data:
            data[user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        before = data[user_id].get("points", 0)
        after = before + amount
        data[user_id]["points"] = after
        await send_value_change_notice(user_id, "pt", before, after, reason)
        count += 1
    save_json_encrypted(DATA_FILE, data)
    await ctx.send(
        f"✅ ロール「{role_name}」のメンバー {count}名 のPTを {amount:+d} PT に変更しました。\n"
        f"📝 理由：{reason}",
        delete_after=15
    )
# ==================================================
# ✅ 管理者用 ロール指定 XP一括変更
# ==================================================
@bot.command(name="edit_xp_role")
async def edit_xp_role(ctx, role_name: str, amount: int, *, reason: str = "理由なし"):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return
    guild = ctx.guild
    target_role = discord.utils.get(guild.roles, name=role_name)
    if not target_role:
        await ctx.send(f"❌ ロール「{role_name}」が見つかりません。", delete_after=10)
        return
    data = load_json_encrypted(DATA_FILE)
    count = 0
    for member in target_role.members:
        user_id = str(member.id)
        if user_id not in data:
            data[user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        before = data[user_id].get("xp", 0)
        after = before + amount
        data[user_id]["xp"] = after
        await send_value_change_notice(user_id, "xp", before, after, reason)
        count += 1
    save_json_encrypted(DATA_FILE, data)
    await ctx.send(
        f"✅ ロール「{role_name}」のメンバー {count}名 のXPを {amount:+d} XP に変更しました。\n"
        f"📝 理由：{reason}",
        delete_after=15
    )
# ========== ✅ ログインボーナス管理 ==========
LOGIN_BONUS_FILE = Path("login_bonus.json")
if not LOGIN_BONUS_FILE.exists():
    LOGIN_BONUS_FILE.write_text("{}", encoding="utf-8")
def load_login_data():
    return json.loads(LOGIN_BONUS_FILE.read_text(encoding="utf-8"))
def save_login_data(data):
    LOGIN_BONUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
def calc_login_bonus_amount(streak_days: int) -> int:
    base = 10
    bonus = base + (streak_days * 5)
    return min(bonus, 100)
def is_image_used(image_url: str) -> bool:
    return image_url in load_json(IMAGE_LOG_FILE)
def mark_image_used(image_url: str, user_id: str, points: int, comment: str = ""):
    log = load_json(IMAGE_LOG_FILE)
    log[image_url] = {
        "user_id": user_id,
        "points": points,
        "comment": comment,
        "used_at": datetime.now(JST).isoformat()
    }
    save_json(IMAGE_LOG_FILE, log)
# ========== ✅ 管理者判定 ==========
def is_admin(user):
    return any(role.name == ADMIN_ROLE_NAME for role in getattr(user, "roles", []))
# ✅ 通常階級の現在位置を取得
async def get_user_current_rank(guild, user_id):
    try:
        member = await guild.fetch_member(int(user_id))
    except Exception:
        return None
    for rank in reversed(ROLE_ORDER):
        if discord.utils.get(member.roles, name=rank):
            return rank
    return None
# ✅ 技術班階級の現在位置を取得
async def get_user_current_tech_rank(guild, user_id):
    try:
        member = await guild.fetch_member(int(user_id))
    except Exception:
        return None
    for rank in reversed(TECH_ROLE_ORDER):
        if discord.utils.get(member.roles, name=rank):
            return rank
    return None
# ✅ 次の階級名と値段を取得
def get_next_rank_info(current_rank, rank_order, price_table):
    if current_rank is None:
        return rank_order[0], price_table[rank_order[0]]
    current_index = rank_order.index(current_rank)
    if current_index >= len(rank_order) - 1:
        return None, None
    next_rank = rank_order[current_index + 1]
    return next_rank, price_table[next_rank]
# ========== ✅ トークン有効性確認 ==========
async def verify_real_discord_token(token: str):
    token = token.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}", token):
        return False, "❌ Discordトークンの形式が正しくありません。", None
    headers = {"Authorization": token, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get("https://discord.com/api/v9/users/@me", headers=headers) as resp:
                if resp.status == 200:
                    user_data = await resp.json()
                    username = f"{user_data.get('username', 'Unknown')}#{user_data.get('discriminator', '0000')}"
                    return True, f"✅ 有効なトークンです。所有者: {username}", user_data
                elif resp.status == 401:
                    return False, "❌ 無効なトークンです。認証に失敗しました。", None
                elif resp.status == 429:
                    return False, "⚠️ API制限に達しました。しばらくしてから再試行してください。", None
                else:
                    return False, f"❌ 確認エラー: ステータスコード {resp.status}", None
    except Exception as e:
        return False, f"❌ 通信エラー: {str(e)}", None
# ========== ✅ 警告文 ==========
WARNING_MESSAGE = (
    "⚠️⚠️⚠️ **絶対に自分の本アカウントのトークンを入力しないでください！**⚠️⚠️⚠️\n"
    "トークンが流出するとアカウントを完全に乗っ取られます。\n"
    "**捨てアカウント・テスト用アカウント**のトークンだけを使用してください。\n"
    "本アカウントのトークンを入力した場合の損害については一切責任を負いません。"
)
# ========== ✅ XP付与ボタン ==========
class XPGrantView(discord.ui.View):
    def __init__(self, req_id, target_user_id, link, desc, msg):
        super().__init__(timeout=None)
        self.req_id = req_id
        self.target_user_id = target_user_id
        self.link = link
        self.desc = desc
        self.msg = msg
    @discord.ui.select(
        placeholder="付与するXP額を選択",
        options=[discord.SelectOption(label=f"{xp} xp", value=str(xp)) for xp in XP_OPTIONS]
    )
    async def select_xp(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ TISN管理者ロールが必要です。", ephemeral=True)
            return
        xp_amount = int(select.values[0])
        data = load_json_encrypted(DATA_FILE)
        if self.target_user_id not in data:
            data[self.target_user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        
        before = data[self.target_user_id].get("xp", 0)
        after = before + xp_amount
        data[self.target_user_id]["xp"] = after
        save_json_encrypted(DATA_FILE, data)
        await send_value_change_notice(self.target_user_id, "xp", before, after, "申請による付与")
        pending = load_json(XP_PENDING_FILE)
        if self.req_id in pending:
            del pending[self.req_id]
            save_json(XP_PENDING_FILE, pending)
        embed = discord.Embed(title="✅ XP付与済み", color=0x2ECC71)
        embed.add_field(name="申請者", value=f"<@{self.target_user_id}>", inline=False)
        embed.add_field(name="Botリンク", value=self.link, inline=False)
        embed.add_field(name="使い方説明", value=self.desc, inline=False)
        if self.msg and self.msg != "（なし）":
            embed.add_field(name="追加メッセージ", value=self.msg, inline=False)
        embed.add_field(name="付与XP", value=f"**{xp_amount} xp**", inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(f"✅ {xp_amount} xp を付与しました。", ephemeral=True)
        try:
            target_user = await bot.fetch_user(int(self.target_user_id))
            dm_text = f"🎉 XP申請が承認されました！\n📦 作成したBot: {self.link}\n💰 付与XP: **{xp_amount} xp**"
            if self.msg and self.msg != "（なし）":
                dm_text += f"\n📝 管理者から: {self.msg}"
            if target_user:
                await target_user.send(dm_text)
        except Exception:
            pass
# ========== ✅ 昇格確認画面：通常階級用 ==========
class PromoteConfirmView(discord.ui.View):
    def __init__(self, next_rank, cost_pt):
        super().__init__(timeout=120)
        self.next_rank = next_rank
        self.cost_pt = cost_pt
    @discord.ui.button(label="✅ 昇格を確定", style=discord.ButtonStyle.green)
    async def confirm_promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        guild = interaction.guild
        member = await guild.fetch_member(interaction.user.id)
        user_data = data.setdefault(user_id, {"points": 0, "xp": 0, "roles": [], "tech_roles": []})
        if user_data["points"] < self.cost_pt:
            await interaction.response.edit_message(
                content=f"⚠️ 申し訳ありません！PTが不足しています。\n必要: {self.cost_pt} PT / 所持: {user_data['points']} PT",
                view=None
            )
            return
        before_pt = user_data["points"]
        after_pt = before_pt - self.cost_pt
        user_data["points"] = after_pt
        save_json_encrypted(DATA_FILE, data)
        await send_value_change_notice(user_id, "pt", before_pt, after_pt, f"階級昇格：{self.next_rank} へ")
        role = discord.utils.get(guild.roles, name=self.next_rank)
        if role:
            await member.add_roles(role)
        await interaction.response.edit_message(
            content=f"🎉 **昇格完了！** {self.next_rank} になりました！\n💳 消費PT: {self.cost_pt} PT\n💰 残高: {after_pt} PT",
            view=None
        )
    @discord.ui.button(label="❌ キャンセル", style=discord.ButtonStyle.red)
    async def cancel_promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ 昇格をキャンセルしました。", view=None)
# ========== ✅ 昇格確認画面：技術班階級用 ==========
class TechPromoteConfirmView(discord.ui.View):
    def __init__(self, next_rank, cost_xp):
        super().__init__(timeout=120)
        self.next_rank = next_rank
        self.cost_xp = cost_xp
    @discord.ui.button(label="✅ 昇格を確定", style=discord.ButtonStyle.green)
    async def confirm_tech_promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        guild = interaction.guild
        member = await guild.fetch_member(interaction.user.id)
        user_data = data.setdefault(user_id, {"points": 0, "xp": 0, "roles": [], "tech_roles": []})
        if user_data["xp"] < self.cost_xp:
            await interaction.response.edit_message(
                content=f"⚠️ 申し訳ありません！XPが不足しています。\n必要: {self.cost_xp} XP / 所持: {user_data['xp']} XP",
                view=None
            )
            return
        before_xp = user_data["xp"]
        after_xp = before_xp - self.cost_xp
        user_data["xp"] = after_xp
        save_json_encrypted(DATA_FILE, data)
        await send_value_change_notice(user_id, "xp", before_xp, after_xp, f"技術班階級昇格：{self.next_rank} へ")
        role = discord.utils.get(guild.roles, name=self.next_rank)
        if role:
            await member.add_roles(role)
        await interaction.response.edit_message(
            content=f"🎉 **技術班昇格完了！** {self.next_rank} になりました！\n💳 消費XP: {self.cost_xp} XP\n💰 残高: {after_xp} XP",
            view=None
        )
    @discord.ui.button(label="❌ キャンセル", style=discord.ButtonStyle.red)
    async def cancel_tech_promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ 昇格をキャンセルしました。", view=None)
# ========== ✅ 権限ロール購入用選択画面 ==========
class PermRoleBuyView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=180)
        self.add_item(PermRoleSelect(options))
class PermRoleSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="購入する権限ロールを選択", options=options)
    async def callback(self, interaction: discord.Interaction):
        role_name = self.values[0]
        price = PERM_ROLE_PRICES[role_name]
        user_id = str(interaction.user.id)
        guild = interaction.guild
        member = await guild.fetch_member(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        user_data = data.setdefault(user_id, {"points": 0, "xp": 0, "roles": [], "tech_roles": []})
        # 所持確認
        if any(r.name == role_name for r in member.roles):
            await interaction.response.send_message(f"⚠️ すでに「{role_name}」を所持しています。", ephemeral=True)
            return
        # PT確認
        before_pt = user_data["points"]
        if before_pt < price:
            await interaction.response.send_message(
                f"⚠️ PTが不足しています。\n必要: {price} PT / 所持: {before_pt} PT\nあと{price - before_pt}PT 足りません。",
                ephemeral=True
            )
            return
        # 確定
        after_pt = before_pt - price
        user_data["points"] = after_pt
        save_json_encrypted(DATA_FILE, data)
        await send_value_change_notice(user_id, "pt", before_pt, after_pt, f"権限ロール購入：{role_name}")
        # ロール付与
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            await member.add_roles(role)
            await interaction.response.send_message(
                f"🎉 購入完了！「{role_name}」が付与されました！\n"
                f"💳 支払い: {price} PT\n"
                f"💰 残高: {after_pt} PT",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ サーバーに「{role_name}」ロールが見つかりません。\n"
                f"管理者に作成してもらってから再度お試しください。\n"
                f"💳 支払いは保留中です（PTは減っていません）",
                ephemeral=True
            )
# ========== ✅ メイン操作パネル ==========
class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    # ✅ ログインボーナス
    @discord.ui.button(label="🎁 ログインボーナス", style=discord.ButtonStyle.success, custom_id="panel_login_bonus")
    async def btn_login_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ✅ 修正：期限切れ/二重応答を安全にスキップ
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            return
        user_id = str(interaction.user.id)
        today = datetime.now(JST).date().isoformat()
        yesterday = (datetime.now(JST).date() - timedelta(days=1)).isoformat()
        login_data = load_login_data()
        user_login = login_data.setdefault(user_id, {"last_date": None, "streak": 0})
        last_date = user_login.get("last_date")
        if last_date == today:
            await interaction.followup.send(
                f"⚠️ 今日はすでにログインボーナスを受け取っています！\n📊 連続日数: {user_login['streak']} 日",
                ephemeral=True
            )
            return
        if last_date == yesterday:
            user_login["streak"] += 1
        else:
            user_login["streak"] = 0
        streak = user_login["streak"]
        bonus_pt = calc_login_bonus_amount(streak)
        data = load_json_encrypted(DATA_FILE)
        if user_id not in data:
            data[user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        before = data[user_id]["points"]
        after = before + bonus_pt
        data[user_id]["points"] = after
        save_json_encrypted(DATA_FILE, data)
        user_login["last_date"] = today
        login_data[user_id] = user_login
        save_login_data(login_data)
        detail_text = f"10 + {streak}×5 = {bonus_pt} PT" if streak > 0 else f"10 PT"
        if bonus_pt >= 100:
            detail_text += " 🎉 上限に達しました！"
        await interaction.followup.send(
            f"✅ ログインボーナスを受け取りました！\n"
            f"💰 獲得PT: **+{bonus_pt} PT**\n"
            f"📊 連続日数: {streak + 1} 日\n"
            f"📋 内訳: {detail_text}\n"
            f"💳 所持PT: {after} PT",
            ephemeral=True
        )
    @discord.ui.button(label="📩 ポイント申請", style=discord.ButtonStyle.primary, custom_id="panel_point_request")
    async def btn_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ DMを送信しました！内容を確認してください。", ephemeral=True)
        try:
            await interaction.user.send(
                "## 📩 ポイント申請フォーム\n"
                "このDMに **証拠の画像（通知のスクリーンショット）** を添付し、\n"
                "**数字（通知の数）** と **任意のコメント（空欄可）** を一緒に書いて送信してください。\n\n"
                "✅ 例：画像を添付して「150 先月分の通知です」と送信\n"
                "⚠️ 同じ画像の再利用は禁止です\n"
                "⚠️ 管理者の承認後にポイントが加算されます"
            )
            temp = load_json(TEMP_DM_FILE)
            temp[str(interaction.user.id)] = {"state": "waiting_point_request"}
            save_json(TEMP_DM_FILE, temp)
        except Exception:
            await interaction.followup.send(
                "⚠️ BotからのDMを受信できるように設定してから再度お試しください。",
                ephemeral=True
            )
    @discord.ui.button(label="💎 トークンを売る(pt)", style=discord.ButtonStyle.success, custom_id="panel_token_sell")
    async def btn_token_sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ DMを送信しました！そちらから手続きを進めてください。", ephemeral=True)
        try:
            await interaction.user.send(
                f"## 💎 トークン出品手続き\n{WARNING_MESSAGE}\n\n"
                "下のボタンを押してトークンを入力してください。"
            )
            await interaction.user.send(view=TokenSellConfirmView(str(interaction.user.id)))
        except Exception:
            await interaction.followup.send("⚠️ DMを受信できるよう設定してください。", ephemeral=True)
    @discord.ui.button(label="🛒 トークンを買う(pt)", style=discord.ButtonStyle.secondary, custom_id="panel_token_buy")
    async def btn_token_buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ✅ 修正：期限切れ/二重応答を安全にスキップ
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            return
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        market = load_json(TOKEN_MARKET_FILE)
        user_data = data.get(user_id, {"points": 0})
        user_points = user_data.get("points", 0)
        if user_points < TOKEN_TRADE_PRICE:
            await interaction.followup.send(
                f"⚠️ ポイントが不足しています。\n必要: {TOKEN_TRADE_PRICE} pt / 所持: {user_points} pt",
                ephemeral=True
            )
            return
        if not market:
            await interaction.followup.send("📭 現在出品されているトークンがありません。", ephemeral=True)
            return
        token_id = random.choice(list(market.keys()))
        token_info = market.pop(token_id)
        try:
            raw_token = decrypt_token(token_info["encrypted_token"])
        except Exception:
            await interaction.followup.send("❌ トークンの復号に失敗しました。", ephemeral=True)
            return
        before_pt = user_data["points"]
        after_pt = before_pt - TOKEN_TRADE_PRICE
        user_data["points"] = after_pt
        save_json_encrypted(DATA_FILE, data)
        await send_value_change_notice(user_id, "pt", before_pt, after_pt, "トークン購入")
        save_json(TOKEN_MARKET_FILE, market)
        try:
            await interaction.user.send(
                f"🎉 トークンを購入しました！\n"
                f"💳 支払い: -{TOKEN_TRADE_PRICE} pt\n"
                f"👤 出品者アカウント: {token_info['owner_username']}\n"
                f"🔑 トークン: `{raw_token}`\n"
                f"💰 残高: {after_pt} pt\n\n"
                "⚠️ このトークンは絶対に他人に見せないでください！"
            )
            await interaction.followup.send("✅ DMにトークンを送信しました！", ephemeral=True)
        except Exception:
            user_data["points"] += TOKEN_TRADE_PRICE
            data[user_id] = user_data
            market[token_id] = token_info
            save_json(TOKEN_MARKET_FILE, market)
            await interaction.followup.send(
                "❌ DMの送信に失敗しました。DMを受信できるよう設定してから再試行してください。",
                ephemeral=True
            )
            return
        try:
            seller = await bot.fetch_user(int(token_info["seller_id"]))
            if seller:
                s_before = data[token_info["seller_id"]]["points"]
                s_after = s_before + TOKEN_TRADE_PRICE
                data[token_info["seller_id"]]["points"] = s_after
                save_json_encrypted(DATA_FILE, data)
                await send_value_change_notice(token_info["seller_id"], "pt", s_before, s_after, "トークンが購入された報酬")
                await seller.send(
                    f"📢 あなたのトークン（{token_info['owner_username']}）が購入されました！\n"
                    f"💳 報酬: +{TOKEN_TRADE_PRICE} pt"
                )
        except Exception:
            pass
    @discord.ui.button(label="⚡ XP申請", style=discord.ButtonStyle.blurple, custom_id="panel_xp_request")
    async def btn_xp_req(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("✅ DMを送信しました！手順を確認してください。", ephemeral=True)
        try:
            await interaction.user.send(
                "## ⚡ XP申請フォーム\n"
                "作成したBotのリンクと、**使い方の説明（必須）**、**任意のメッセージ（空欄可）** を\n"
                "以下のように記入してこのDMに送信してください。\n\n"
                "✅ 例：\n"
                "https://discord.com/api/oauth2/authorize?client_id=xxxx\n"
                "メンバーの発言回数を集計するBotです。/count で確認できます。\n"
                "先月分の活動をまとめました。\n\n"
                "⚠️ 1行目：Botリンク\n"
                "⚠️ 2行目：使い方・機能の説明（必須）\n"
                "⚠️ 3行目以降：追加メッセージ（任意）"
            )
            temp = load_json(TEMP_DM_FILE)
            temp[str(interaction.user.id)] = {"state": "waiting_xp_request"}
            save_json(TEMP_DM_FILE, temp)
        except Exception:
            await interaction.followup.send("⚠️ DMを受信できるよう設定してください。", ephemeral=True)
    @discord.ui.button(label="⚡ XP確認", style=discord.ButtonStyle.blurple, custom_id="panel_check_xp")
    async def btn_check_xp(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        xp = data.get(user_id, {}).get("xp", 0)
        await interaction.response.send_message(f"⚡ {interaction.user.mention} のXP: **{xp} xp**", ephemeral=True)
    @discord.ui.button(label="💰 pt確認", style=discord.ButtonStyle.primary, custom_id="panel_check_point")
    async def btn_check_point(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        points = data.get(user_id, {}).get("points", 0)
        await interaction.response.send_message(f"💰 {interaction.user.mention} のポイント: **{points} pt**", ephemeral=True)
    @discord.ui.button(label="🎖️ 階級昇格(pt)", style=discord.ButtonStyle.green, custom_id="panel_promote_rank")
    async def btn_promote_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ✅ 修正：期限切れ/二重応答を安全にスキップ
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            return
        guild = interaction.guild
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        user_data = data.setdefault(user_id, {"points": 0, "xp": 0, "roles": [], "tech_roles": []})
        current_rank = await get_user_current_rank(guild, interaction.user.id)
        next_rank, cost_pt = get_next_rank_info(current_rank, ROLE_ORDER, ROLE_PRICES)
        if not next_rank:
            await interaction.followup.send("⚠️ すでに最高階級です。これ以上昇格できません。", ephemeral=True)
            return
        if user_data["points"] < cost_pt:
            await interaction.followup.send(
                f"⚠️ PTが足りません！\n"
                f"🎖️ 次の階級: {next_rank}\n"
                f"💳 必要PT: {cost_pt} PT\n"
                f"💰 所持PT: {user_data['points']} PT\n"
                f"📉 不足: {cost_pt - user_data['points']} PT",
                ephemeral=True
            )
            return
        await interaction.followup.send(
            f"📋 昇格確認\n"
            f"🎖️ 現在の階級: {current_rank or '未取得'}\n"
            f"⬆️ 次の階級: **{next_rank}**\n"
            f"💳 消費PT: {cost_pt} PT\n"
            f"💰 昇格後残高: {user_data['points'] - cost_pt} PT\n\n"
            f"本当に{next_rank}に昇格しますか？",
            view=PromoteConfirmView(next_rank, cost_pt),
            ephemeral=True
        )
    @discord.ui.button(label="🔧 技術班昇格(xp)", style=discord.ButtonStyle.primary, custom_id="panel_promote_tech")
    async def btn_promote_tech(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ✅ 修正：期限切れ/二重応答を安全にスキップ
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            return
        guild = interaction.guild
        user_id = str(interaction.user.id)
        data = load_json_encrypted(DATA_FILE)
        user_data = data.setdefault(user_id, {"points": 0, "xp": 0, "roles": [], "tech_roles": []})
        current_rank = await get_user_current_tech_rank(guild, interaction.user.id)
        next_rank, cost_xp = get_next_rank_info(current_rank, TECH_ROLE_ORDER, TECH_ROLE_COST_XP)
        if not next_rank:
            await interaction.followup.send("⚠️ すでに最高技術班階級です。これ以上昇格できません。", ephemeral=True)
            return
        if user_data["xp"] < cost_xp:
            await interaction.followup.send(
                f"⚠️ XPが足りません！\n"
                f"🔧 次の階級: {next_rank}\n"
                f"💳 必要XP: {cost_xp} XP\n"
                f"💰 所持XP: {user_data['xp']} XP\n"
                f"📉 不足: {cost_xp - user_data['xp']} XP",
                ephemeral=True
            )
            return
        await interaction.followup.send(
            f"📋 技術班昇格確認\n"
            f"🔧 現在の階級: {current_rank or '未取得'}\n"
            f"⬆️ 次の階級: **{next_rank}**\n"
            f"💳 消費XP: {cost_xp} XP\n"
            f"💰 昇格後残高: {user_data['xp'] - cost_xp} XP\n\n"
            f"本当に{next_rank}に昇格しますか？",
            view=TechPromoteConfirmView(next_rank, cost_xp),
            ephemeral=True
        )
    @discord.ui.button(label="🔐 権限ロールを購入", style=discord.ButtonStyle.grey, custom_id="panel_buy_perm")
    async def btn_buy_perm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ✅ 修正：期限切れ/二重応答を安全にスキップ
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except (discord.NotFound, discord.HTTPException):
            return
        user_id = str(interaction.user.id)
        guild = interaction.guild
        data = load_json_encrypted(DATA_FILE)
        user_data = data.setdefault(user_id, {"points": 0, "xp": 0, "roles": [], "tech_roles": [], "perm_roles": []})
        user_pt = user_data.get("points", 0)
        lines = ["📋 購入可能な権限ロール（好きなものを個別に購入できます）\n"]
        lines.append(f"💰 所持PT: {user_pt} PT\n")
        select_opts = []
        for role_name, price in PERM_ROLE_PRICES.items():
            has_already = any(r.name == role_name for r in interaction.user.roles)
            if has_already:
                lines.append(f"✅ {role_name} — 【所持済み】")
            else:
                status = f"💳 {price} PT"
                if user_pt >= price:
                    status += " ✅ 購入可"
                else:
                    status += f" ❌ 不足（あと{price - user_pt}PT）"
                lines.append(f"🔘 {role_name} — {status}")
                select_opts.append(discord.SelectOption(label=f"{role_name} / {price}PT", value=role_name))
        if not select_opts:
            await interaction.followup.send("✅ 必要な権限はすべて所持しています！", ephemeral=True)
            return
        embed = discord.Embed(title="🔐 権限ロール購入", description="\n".join(lines), color=0x95A5A6)
        await interaction.followup.send(embed=embed, view=PermRoleBuyView(select_opts), ephemeral=True)

# ========== ✅ トークン出品フロー ==========
class TokenSellConfirmView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=None)
        self.user_id = user_id
    @discord.ui.button(label="✅ 理解してトークンを入力", style=discord.ButtonStyle.red)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("❌ 本人だけが実行できます。", ephemeral=True)
            return
        await interaction.response.send_modal(TokenSellInputModal())

class TokenSellInputModal(discord.ui.Modal, title="トークンを出品"):
    token_input = discord.ui.TextInput(
        label="Discordトークンを入力",
        style=discord.TextStyle.short,
        required=True,
        min_length=50
    )
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_token = str(self.token_input).strip()
        is_valid, message, user_info = await verify_real_discord_token(raw_token)
        if not is_valid:
            await interaction.followup.send(f"{message}\n⚠️ トークンは拒否されました。", ephemeral=True)
            admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_channel:
                await admin_channel.send(
                    f"🚫 無効トークンを出品しようとしました\n"
                    f"ユーザー: {interaction.user.mention}\n理由: {message}"
                )
            return
        encrypted_token = encrypt_token(raw_token)
        market = load_json(TOKEN_MARKET_FILE)
        token_id = f"TOKEN_{interaction.user.id}_{int(datetime.now(JST).timestamp())}"
        market[token_id] = {
            "encrypted_token": encrypted_token,
            "seller_id": str(interaction.user.id),
            "seller_name": str(interaction.user),
            "owner_username": f"{user_info.get('username', 'Unknown')}#{user_info.get('discriminator', '0000')}",
            "owner_id": str(user_info.get("id")),
            "listed_at": datetime.now(JST).isoformat()
        }
        save_json(TOKEN_MARKET_FILE, market)
        data = load_json_encrypted(DATA_FILE)
        user_id_str = str(interaction.user.id)
        if user_id_str not in data:
            data[user_id_str] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        before = data[user_id_str]["points"]
        after = before + TOKEN_TRADE_PRICE
        data[user_id_str]["points"] = after
        save_json_encrypted(DATA_FILE, data)
        await send_value_change_notice(user_id_str, "pt", before, after, "トークン出品報酬")
        await interaction.followup.send(
            f"✅ トークンを出品しました！\n"
            f"{message}\n"
            f"💳 報酬: +{TOKEN_TRADE_PRICE} pt\n"
            f"💰 残高: {data[user_id_str]['points']} pt\n"
            f"🔒 トークンは暗号化され安全に保管されました。",
            ephemeral=True
        )
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            await admin_channel.send(
                f"💎 有効トークンが出品されました\n"
                f"出品者: {interaction.user.mention}\n"
                f"所有者: {market[token_id]['owner_username']}\n"
                f"🔒 トークンは暗号化済み"
            )

# ========== ✅ 承認/拒否モーダル ==========
class ApproveModal(discord.ui.Modal, title="✅ 承認"):
    comment = discord.ui.TextInput(label="ユーザーへのメッセージ（任意）", style=discord.TextStyle.long, required=False)
    def __init__(self, req_id, pts, target_uid, img_url):
        super().__init__()
        self.req_id = req_id
        self.pts = pts
        self.target_uid = target_uid
        self.img_url = img_url
    async def on_submit(self, interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ TISN管理者ロールが必要です。", ephemeral=True)
            return
        data = load_json_encrypted(DATA_FILE)
        if self.target_uid not in data:
            data[self.target_uid] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        before = data[self.target_uid]["points"]
        after = before + self.pts
        data[self.target_uid]["points"] = after
        save_json_encrypted(DATA_FILE, data)
        mark_image_used(self.img_url, self.target_uid, self.pts, str(self.comment))
        await send_value_change_notice(self.target_uid, "pt", before, after, f"ポイント申請承認 {str(self.comment)}")
        pending = load_json(PENDING_FILE)
        if self.req_id in pending:
            del pending[self.req_id]
            save_json(PENDING_FILE, pending)
        embed = discord.Embed(title="✅ 承認済み", description=f"{self.pts} pt 加算完了", color=0x2ECC71)
        if self.comment:
            embed.add_field(name="📝 管理者からのメッセージ", value=str(self.comment), inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(f"✅ {self.pts} pt 加算しました。", ephemeral=True)
        try:
            target_user = await bot.fetch_user(int(self.target_uid))
            msg = f"🎉 ポイント申請が承認されました！ +{self.pts} pt"
            if self.comment:
                msg += f"\n📝 メッセージ: {self.comment}"
            if target_user:
                await target_user.send(msg)
        except Exception:
            pass

class DenyModal(discord.ui.Modal, title="❌ 拒否"):
    comment = discord.ui.TextInput(label="拒否の理由（任意）", style=discord.TextStyle.long, required=False)
    def __init__(self, req_id, target_uid):
        super().__init__()
        self.req_id = req_id
        self.target_uid = target_uid
    async def on_submit(self, interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ TISN管理者ロールが必要です。", ephemeral=True)
            return
        pending = load_json(PENDING_FILE)
        if self.req_id in pending:
            del pending[self.req_id]
            save_json(PENDING_FILE, pending)
        embed = discord.Embed(title="❌ 拒否済み", description="申請を拒否しました。", color=0xE74C3C)
        if self.comment:
            embed.add_field(name="📝 理由", value=str(self.comment), inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("❌ 拒否しました。", ephemeral=True)
        try:
            target_user = await bot.fetch_user(int(self.target_uid))
            msg = "❌ 残念ながらポイント申請は拒否されました。"
            if self.comment:
                msg += f"\n📝 理由: {self.comment}"
            if target_user:
                await target_user.send(msg)
        except Exception:
            pass

# ========== ✅ 承認/拒否ボタン ==========
class ApproveDenyView(discord.ui.View):
    def __init__(self, request_id: str, points: int, target_user_id: str, image_url: str, user_comment: str = ""):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.points = points
        self.target_user_id = target_user_id
        self.image_url = image_url
        self.user_comment = user_comment
    @discord.ui.button(label="✅ 承認", style=discord.ButtonStyle.green)
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ TISN管理者ロールが必要です。", ephemeral=True)
            return
        await interaction.response.send_modal(ApproveModal(self.request_id, self.points, self.target_user_id, self.image_url))
    @discord.ui.button(label="❌ 拒否", style=discord.ButtonStyle.red)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ TISN管理者ロールが必要です。", ephemeral=True)
            return
        await interaction.response.send_modal(DenyModal(self.request_id, self.target_user_id))

# ========== ✅ DMからのメッセージ処理 — 二重実行バグ修正版 ==========
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # ==============================================
    # ✅ 【重要修正】サーバー上のコマンドは二重実行させない
    # ==============================================
    if not isinstance(message.channel, discord.DMChannel):
        # ! で始まるコマンドはBot標準の自動実行に任せてスキップ
        if not message.content.startswith(COMMAND_PREFIX):
            await bot.process_commands(message)
        return

    # === 以降はDMメッセージ専用処理 ===
    user_id = str(message.author.id)
    temp = load_json(TEMP_DM_FILE)
    state_info = temp.get(user_id, {})
    state = state_info.get("state")

    # 📩 ポイント申請
    if state == "waiting_point_request":
        if not message.attachments:
            await message.author.send("⚠️ 画像が見当たりません。画像を添付して数字とコメントと一緒に送ってください。")
            return
        content = message.content.strip()
        match = re.match(r"^(\d+)\s*(.*)$", content)
        if not match:
            await message.author.send("⚠️ 先頭に数字を入力し、その後にコメントを書いてください。\n例：`150 先月分の通知です`")
            return
        points = int(match.group(1))
        comment = match.group(2).strip() or "（コメントなし）"
        image_url = message.attachments[0].url
        if is_image_used(image_url):
            await message.author.send("⚠️ この画像はすでに使用されています。別の画像を使用してください。")
            return
        req_id = f"PTS_REQ_{user_id}_{int(datetime.now(JST).timestamp())}"
        pending = load_json(PENDING_FILE)
        pending[req_id] = {
            "user_id": user_id,
            "points": points,
            "comment": comment,
            "image_url": image_url,
            "submitted_at": datetime.now(JST).isoformat()
        }
        save_json(PENDING_FILE, pending)
        temp[user_id] = {"state": None}
        save_json(TEMP_DM_FILE, temp)
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(title="📩 ポイント申請 届いてます！", color=0x3498DB)
            embed.add_field(name="申請者", value=f"<@{user_id}>", inline=False)
            embed.add_field(name="申請PT", value=f"**{points} pt**", inline=False)
            embed.add_field(name="コメント", value=comment, inline=False)
            embed.set_image(url=image_url)
            await admin_channel.send(embed=embed, view=ApproveDenyView(req_id, points, user_id, image_url, comment))
        await message.author.send(
            f"✅ 申請を送信しました！\n"
            f"💳 申請PT: {points} pt\n"
            f"📝 コメント: {comment}\n"
            f"⏳ 管理者の承認をお待ちください。"
        )
        return

    # ⚡ XP申請
    if state == "waiting_xp_request":
        lines = message.content.strip().splitlines()
        if len(lines) < 2:
            await message.author.send(
                "⚠️ フォーマットが正しくありません。\n"
                "1行目：Botの招待リンク\n"
                "2行目：使い方・機能の説明（必須）\n"
                "3行目以降：追加メッセージ（任意）"
            )
            return
        link = lines[0].strip()
        desc = lines[1].strip()
        msg_extra = "\n".join(lines[2:]).strip() if len(lines) > 2 else "（なし）"
        if not link.startswith("http"):
            await message.author.send("⚠️ 1行目はURL（http～）を記入してください。")
            return
        req_id = f"XP_REQ_{user_id}_{int(datetime.now(JST).timestamp())}"
        pending = load_json(XP_PENDING_FILE)
        pending[req_id] = {
            "user_id": user_id,
            "link": link,
            "description": desc,
            "message": msg_extra,
            "submitted_at": datetime.now(JST).isoformat()
        }
        save_json(XP_PENDING_FILE, pending)
        temp[user_id] = {"state": None}
        save_json(TEMP_DM_FILE, temp)
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(title="⚡ XP申請 届いてます！", color=0x9B59B6)
            embed.add_field(name="申請者", value=f"<@{user_id}>", inline=False)
            embed.add_field(name="Botリンク", value=link, inline=False)
            embed.add_field(name="使い方説明", value=desc, inline=False)
            if msg_extra != "（なし）":
                embed.add_field(name="追加メッセージ", value=msg_extra, inline=False)
            await admin_channel.send(embed=embed, view=XPGrantView(req_id, user_id, link, desc, msg_extra))
        await message.author.send(
            f"✅ XP申請を送信しました！\n"
            f"📦 Botリンク: {link}\n"
            f"📝 説明: {desc}\n"
            f"⏳ 管理者がXP額を決定します。しばらくお待ちください。"
        )
        return

    # ✅ その他のDMメッセージ（状態なし）
    await message.author.send(
        "👋 メインパネルから操作してください！\n"
        "`!panel` と入力するとボタン一覧が表示されます。"
    )

# ========== ✅ メインパネル表示コマンド ==========
@bot.command(name="panel")
async def show_main_panel(ctx):
    await ctx.send("## 🎛️ TISN 操作パネル", view=MainPanelView())

# ========== ✅ Bot起動準備 ==========
@bot.event
async def on_ready():
    print(f"✅ ログイン完了: {bot.user}")
    # ✅ 永続View登録（再起動してもボタンが動く）
    bot.add_view(MainPanelView())
    # ✅ ランキングタスク起動（重複防止）
    if not daily_ranking_task.is_running():
        daily_ranking_task.start()
        print("⏰ ランキング自動更新タスクを起動しました（毎日 0:00 JST）")
    await bot.change_presence(activity=discord.Game(name="TISN ポイント管理システム"))

# ========== ✅ Bot起動 ==========
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("⚠️ 環境変数 DISCORD_BOT_TOKEN を設定してください。")
    else:
        bot.run(TOKEN)