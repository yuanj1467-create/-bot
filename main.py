import discord
from discord.ext import commands, tasks
import os
import json
import random
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
import aiohttp

# ==================================================
# ✅ タイムゾーン・チャンネル設定
# ==================================================
JST = timezone(timedelta(hours=9))
RANKING_CHANNEL_ID = 1537850013290467379  # 🏆 ランキング公開チャンネル

# ========== ✅ 設定 ==========
ADMIN_ROLE_NAME = "TISN管理者"
ADMIN_CHANNEL_ID = 1537497919966544086
TOKEN_TRADE_PRICE = 250
COMMAND_PREFIX = "!"

# ✅ 通常階級：低い順
ROLE_ORDER = [
    "一等兵",
    "曹長",
    "大尉",
    "ムスカ大佐",
    "TISN最高幹部"
]
ROLE_PRICES = {
    "一等兵": 500,
    "曹長": 3000,
    "大尉": 10000,
    "ムスカ大佐": 30000,
    "TISN最高幹部": 100000
}

# ✅ 技術班階級：低い順
TECH_ROLE_ORDER = [
    "技術班",
    "高度技術班",
    "技術班最高幹部"
]
TECH_ROLE_COST_XP = {
    "技術班": 500,
    "高度技術班": 5000,
    "技術班最高幹部": 30000
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


# ==================================================
# ✅ ランキング生成 共通関数
# ==================================================
async def build_ranking_embed():
    now = datetime.now(JST)
    DATA_FILE_PATH = Path("points_data.json")
    if not DATA_FILE_PATH.exists():
        return discord.Embed(title="🏆 TISNランキング", description="📭 データが存在しません。", color=0xFFD700)
    
    data = json.loads(DATA_FILE_PATH.read_text(encoding="utf-8"))
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
# ✅ 毎日 0:00 自動ランキング（公開チャンネルへ）
# ==================================================
@tasks.loop(hours=24)
async def daily_ranking_task():
    now = datetime.now(JST)
    if now.hour != 0:
        return

    ranking_channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not ranking_channel:
        return

    embed = await build_ranking_embed()
    await ranking_channel.send(embed=embed)


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
            print("⚠️ このキーを失うと保存中のトークンは復元できません！")
    return Fernet(key)

CIPHER = get_or_create_cipher()

def encrypt_token(raw_token: str) -> str:
    return CIPHER.encrypt(raw_token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    return CIPHER.decrypt(encrypted_token.encode()).decode()

# ========== ✅ データファイル管理 ==========
DATA_FILE = Path("points_data.json")
IMAGE_LOG_FILE = Path("image_log.json")
PENDING_FILE = Path("pending_requests.json")
XP_PENDING_FILE = Path("xp_pending.json")
TEMP_DM_FILE = Path("temp_dm_state.json")
TOKEN_MARKET_FILE = Path("token_market_encrypted.json")

for f in [DATA_FILE, IMAGE_LOG_FILE, PENDING_FILE, XP_PENDING_FILE, TEMP_DM_FILE, TOKEN_MARKET_FILE]:
    if not f.exists():
        f.write_text("{}", encoding="utf-8")

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def is_image_used(image_url: str) -> bool:
    return image_url in load_json(IMAGE_LOG_FILE)

def mark_image_used(image_url: str, user_id: str, points: int, comment: str = ""):
    log = load_json(IMAGE_LOG_FILE)
    log[image_url] = {
        "user_id": user_id,
        "points": points,
        "comment": comment,
        "used_at": datetime.utcnow().isoformat()
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

# ✅ 階級表示フォーマット
def build_rank_progress(order, current_rank):
    lines = []
    reversed_order = list(reversed(order))
    
    if current_rank is None:
        for idx, name in enumerate(reversed_order):
            lines.append(f"{name} 👈next" if idx == len(reversed_order) - 1 else name)
        return "\n".join(lines)
    
    current_index = reversed_order.index(current_rank)
    for idx, name in enumerate(reversed_order):
        if name == current_rank:
            lines.append(f"**{name} 👈now**")
        elif idx == current_index - 1:
            lines.append(f"{name} 👈next")
        elif idx > current_index:
            lines.append(f"~~{name}~~")
        else:
            lines.append(f"{name}")
    return "\n".join(lines)

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

        data = load_json(DATA_FILE)
        if self.target_user_id not in data:
            data[self.target_user_id] = {"points": 0, "xp": 0, "roles": [], "tech_roles": []}
        data[self.target_user_id]["xp"] = data[self.target_user_id].get("xp", 0) + xp_amount
        save_json(DATA_FILE, data)

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

# ========== ✅ メイン操作パネル ==========
class MainPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        data = load_json(DATA_FILE)
        market = load_json(TOKEN_MARKET_FILE)
        user_data = data.get(user_id, {"points": 0})
        user_points = user_data.get("points", 0)

        if user_points < TOKEN_TRADE_PRICE:
            await interaction.followup.send(
                f"⚠️ ポイントが不足しています。\n"
                f"必要: {TOKEN_TRADE_PRICE} pt / 所持: {user_points} pt",
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

        user_data["points"] -= TOKEN_TRADE_PRICE
        data[user_id] = user_data
        save_json(DATA_FILE, data)
        save_json(TOKEN_MARKET_FILE, market)

        try:
            await interaction.user.send(
                f"🎉 トークンを購入しました！\n"
                f"💳 支払い: -{TOKEN_TRADE_PRICE} pt\n"
                f"👤 出品者アカウント: {token_info['owner_username']}\n"
                f"🔑 トークン: `{raw_token}`\n"
                f"💰 残高: {user_data['points']} pt\n\n"
                "⚠️ このトークンは絶対に他人に見せないでください！"
            )
            await interaction.followup.send("✅ DMにトークンを送信しました！", ephemeral=True)
        except Exception:
            user_data["points"] += TOKEN_TRADE_PRICE
            data[user_id] = user_data
            market[token_id] = token_info
            save_json(DATA_FILE, data)
            save_json(TOKEN_MARKET_FILE, market)
            await interaction.followup.send(
                "❌ DMの送信に失敗しました。DMを受信できるよう設定してから再試行してください。",
                ephemeral=True
            )
            return

        try:
            seller = await bot.fetch_user(int(token_info["seller_id"]))
            if seller:
                await seller.send(
                    f"📢 あなたのトークン（{token_info['owner_username']}）が購入されました！\n"
                    f"💳 報酬: +{TOKEN_TRADE_PRICE} pt"
                )
        except Exception:
            pass

    @discord.ui.button(label="💰 pt確認", style=discord.ButtonStyle.primary, custom_id="panel_check_point")
    async def btn_check_point(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        data = load_json(DATA_FILE)
        points = data.get(user_id, {}).get("points", 0)
        await interaction.response.send_message(
            f"💰 {interaction.user.mention} のポイント: **{points} pt**",
            ephemeral=True
        )

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
        data = load_json(DATA_FILE)
        xp = data.get(user_id, {}).get("xp", 0)
        await interaction.response.send_message(
            f"⚡ {interaction.user.mention} のXP: **{xp} xp**",
            ephemeral=True
        )

    @discord.ui.button(label="🏪 ロール購入", style=discord.ButtonStyle.secondary, custom_id="panel_buy_role")
    async def btn_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = await bot.fetch_guild(int(interaction.guild_id))
        current_rank = await get_user_current_rank(guild, interaction.user.id)
        current_tech_rank = await get_user_current_tech_rank(guild, interaction.user.id)

        embed_text = "## 🏪 ロールショップ\n✅ 一つ下の階級を所持している場合のみ購入可\n\n"
        embed_text += "### 🎖️ 通常階級（ptで購入）\n"
        embed_text += f"**現在の階級: {current_rank or '未取得'}**\n"
        embed_text += build_rank_progress(ROLE_ORDER, current_rank) + "\n\n"
        for name, price in reversed(list(ROLE_PRICES.items())):
            embed_text += f"`{name}` → {price} pt\n"
        embed_text += "\n### 🔧 技術班階級（xpで購入）\n"
        embed_text += f"**現在の技術班階級: {current_tech_rank or '未取得'}**\n"
        embed_text += build_rank_progress(TECH_ROLE_ORDER, current_tech_rank) + "\n\n"
        for name, cost in reversed(list(TECH_ROLE_COST_XP.items())):
            embed_text += f"`{name}` → {cost} xp\n"
        embed_text += "\n購入したいロール名をDMで送信してください。"

        await interaction.followup.send(embed_text, ephemeral=True)
        try:
            await interaction.user.send(f"{embed_text}\n例：`一等兵` または `技術班` と送信")
            temp = load_json(TEMP_DM_FILE)
            temp[str(interaction.user.id)] = {"state": "waiting_role_purchase", "guild_id": str(interaction.guild_id)}
            save_json(TEMP_DM_FILE, temp)
        except Exception:
            pass


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
        token_id = f"TOKEN_{interaction.user.id}_{int(datetime.utcnow().timestamp())}"
        market[token_id] = {
            "encrypted_token": encrypted_token,
            "seller_id": str(interaction.user.id),
            "seller_name": str(interaction.user),
            "owner_username": f"{user_info.get('username')}#{user_info.get('discriminator', '0000')}",
            "owner_id": str(user_info.get("id")),
            "listed_at": datetime.utcnow().isoformat()
        }
        save_json(TOKEN_MARKET_FILE, market)

        data = load_json(DATA_FILE)
        user_id_str = str(interaction.user.id)
        if user_id_str not in data:
            data[user_id_str] = {"points": 0, "xp": 0, "roles": []}
        data[user_id_str]["points"] += TOKEN_TRADE_PRICE
        save_json(DATA_FILE, data)

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

        data = load_json(DATA_FILE)
        if self.target_uid not in data:
            data[self.target_uid] = {"points": 0, "xp": 0, "roles": []}
        data[self.target_uid]["points"] += self.pts
        save_json(DATA_FILE, data)
        mark_image_used(self.img_url, self.target_uid, self.pts, str(self.comment))

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


# ========== ✅ DMからのメッセージ処理 ==========
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if not isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
        return

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

        point_value = int(match.group(1))
        user_comment = match.group(2).strip() or "（コメントなし）"

        image_url = message.attachments[0].url
        if is_image_used(image_url):
            await message.author.send("🚫 この画像は既に使用されています。別の画像をお使いください。")
            return

        pending = load_json(PENDING_FILE)
        request_id = f"{user_id}_{int(datetime.utcnow().timestamp())}"
        pending[request_id] = {
            "user_id": user_id,
            "user_name": str(message.author),
            "points": point_value,
            "comment": user_comment,
            "image_url": image_url,
            "status": "pending",
            "applied_at": datetime.utcnow().isoformat()
        }
        save_json(PENDING_FILE, pending)

        del temp[user_id]
        save_json(TEMP_DM_FILE, temp)

        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title="📩 ポイント申請（DM経由）",
                description=f"申請者: {message.author.mention} / {message.author}\n申請ポイント: **{point_value} pt**\n📝 申請者コメント: {user_comment}",
                color=0x2ECC71
            )
            embed.set_image(url=image_url)
            embed.set_footer(text=f"申請ID: {request_id}")
            await admin_channel.send(embed=embed, view=ApproveDenyView(request_id, point_value, user_id, image_url, user_comment))

        await message.author.send(
            f"✅ 申請を送信しました！\n"
            f"申請額: **{point_value} pt**\n"
            f"コメント: {user_comment}\n"
            f"管理者の承認をお待ちください。"
        )
        return

    # ⚡ XP申請
    if state == "waiting_xp_request":
        lines = message.content.strip().splitlines()
        if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
            await message.author.send(
                "⚠️ フォーマットが違います。\n"
                "1行目：Botのリンク\n"
                "2行目：使い方・機能の説明（必須）\n"
                "3行目以降：追加メッセージ（任意）"
            )
            return

        bot_link = lines[0].strip()
        description = lines[1].strip()
        extra_msg = "\n".join(lines[2:]).strip() if len(lines) > 2 else "（なし）"

        pending = load_json(XP_PENDING_FILE)
        request_id = f"XP_{user_id}_{int(datetime.utcnow().timestamp())}"
        pending[request_id] = {
            "user_id": user_id,
            "user_name": str(message.author),
            "link": bot_link,
            "description": description,
            "message": extra_msg,
            "applied_at": datetime.utcnow().isoformat()
        }
        save_json(XP_PENDING_FILE, pending)

        del temp[user_id]
        save_json(TEMP_DM_FILE, temp)

        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(title="⚡ XP申請（Bot作成）", color=0x5865F2)
            embed.add_field(name="申請者", value=f"{message.author.mention} / {message.author}", inline=False)
            embed.add_field(name="🔗 Botリンク", value=bot_link, inline=False)
            embed.add_field(name="📖 使い方・機能の説明", value=description, inline=False)
            if extra_msg and extra_msg != "（なし）":
                embed.add_field(name="📝 追加メッセージ", value=extra_msg, inline=False)
            embed.set_footer(text=f"申請ID: {request_id}")
            await admin_channel.send(embed=embed, view=XPGrantView(request_id, user_id, bot_link, description, extra_msg))

        await message.author.send(
            f"✅ XP申請を送信しました！\n"
            f"🔗 Botリンク: {bot_link}\n"
            f"📖 説明: {description}\n"
            f"📝 メッセージ: {extra_msg}\n"
            f"管理者の確認をお待ちください。"
        )
        return

    # 🏪 ロール購入
    if state == "waiting_role_purchase":
        role_name = message.content.strip()

        is_normal = role_name in ROLE_PRICES
        is_tech = role_name in TECH_ROLE_COST_XP
        if not is_normal and not is_tech:
            await message.author.send(
                f"⚠️ 「{role_name}」はロール一覧に存在しません。\n"
                "正確なロール名を入力してください。"
            )
            return

        price = ROLE_PRICES[role_name] if is_normal else TECH_ROLE_COST_XP[role_name]
        currency = "pt" if is_normal else "xp"
        order = ROLE_ORDER if is_normal else TECH_ROLE_ORDER
        data = load_json(DATA_FILE)
        user_data = data.get(user_id, {"points": 0, "xp": 0, "roles": []})

        guild_id = state_info.get("guild_id")
        if not guild_id:
            await message.author.send("⚠️ サーバー情報が取得できません。もう一度ロール購入ボタンからやり直してください。")
            return

        guild = await bot.fetch_guild(int(guild_id))
        current_rank = await get_user_current_rank(guild, user_id) if is_normal else await get_user_current_tech_rank(guild, user_id)
        target_index = order.index(role_name)

        # 下位階級チェック
        if target_index > 0:
            required_rank = order[target_index - 1]
            if current_rank != required_rank:
                await message.author.send(
                    f"⚠️ 購入条件を満たしていません。\n"
                    f"「{role_name}」を購入するには**「{required_rank}」**の所持が必要です。\n"
                    f"現在の階級: {current_rank or '未取得'}"
                )
                return

        # 既所持チェック
        if current_rank == role_name:
            await message.author.send("⚠️ 既にこのロールを所持しています。")
            return

        # 残高チェック
        balance = user_data.get("points", 0) if is_normal else user_data.get("xp", 0)
        if balance < price:
            await message.author.send(
                f"⚠️ 残高が不足しています。\n"
                f"必要: {price} {currency} / 所持: {balance} {currency}"
            )
            return

        # 決済→ロール付与
        paid = False
        try:
            if is_normal:
                user_data["points"] -= price
            else:
                user_data["xp"] -= price
            data[user_id] = user_data
            save_json(DATA_FILE, data)
            paid = True

            member = await guild.fetch_member(int(user_id))
            target_role = discord.utils.get(guild.roles, name=role_name)
            if not target_role:
                target_role = await guild.create_role(name=role_name, color=discord.Color.gold())

            await member.add_roles(target_role)

        except Exception as e:
            if paid:
                if is_normal:
                    user_data["points"] += price
                else:
                    user_data["xp"] += price
                data[user_id] = user_data
                save_json(DATA_FILE, data)
            await message.author.send(
                f"⚠️ ロールの付与に失敗しました。\n"
                f"原因: {str(e)}\n"
                f"💳 {price} {currency} は返金されました。権限を確認してから再度お試しください。"
            )
            return

        # 成功
        user_data.setdefault("roles", []).append(role_name)
        data[user_id] = user_data
        save_json(DATA_FILE, data)

        del temp[user_id]
        save_json(TEMP_DM_FILE, temp)

        new_balance = user_data["points"] if is_normal else user_data["xp"]
        await message.author.send(
            f"🎉 「{role_name}」を購入しました！\n"
            f"💳 支払い: {price} {currency}\n"
            f"💰 残高: {new_balance} {currency}"
        )
        return

    await bot.process_commands(message)


# ========== ✅ 管理者用コマンド：パネル設置 ==========
@bot.command(name="setup")
async def setup_panel(ctx):
    if not is_admin(ctx.author):
        await ctx.send("❌ TISN管理者ロールが必要です。", delete_after=5)
        return

    embed = discord.Embed(
        title="🏅 TISN ポイ活・トークン売買システム",
        description=(
            "下のボタンから各機能を起動してください。\n"
            "✅ すべての操作は**DM経由で完全非公開**で行われます\n"
            "✅ 公開チャンネルに個人情報やトークンは一切表示されません"
        ),
        color=0xFFD700
    )
    await ctx.send(embed=embed, view=MainPanelView())
    await ctx.message.delete()


# ========== ✅ エラー制御 ==========
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    await ctx.send(f"⚠️ エラー: {str(error)}", delete_after=10)


# ========== ✅ 起動時処理 ==========
@bot.event
async def on_ready():
    bot.add_view(MainPanelView())
    if not daily_ranking_task.is_running():
        daily_ranking_task.start()
    print(f"✅ Bot起動完了: {bot.user}")
    print(f"✅ 管理者ロール: {ADMIN_ROLE_NAME}")
    print(f"✅ ランキングch: {RANKING_CHANNEL_ID}")
    print(f"✅ 接頭辞: {COMMAND_PREFIX}")


# ========== ✅ Bot起動 ==========
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("環境変数に DISCORD_BOT_TOKEN または TOKEN を設定してください。")

bot.run(BOT_TOKEN)
