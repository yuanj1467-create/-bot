import discord
from discord.ext import commands
import os
import json
import random
import re
import base64
from pathlib import Path
from datetime import datetime
from cryptography.fernet import Fernet
import aiohttp

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!！", intents=intents)

# === 設定 ===
ROLE_PRICES = {
    "一等兵": 500,
    "曹長": 3000,
    "大尉": 10000,
    "ムスカ大佐": 30000,
    "TISN最高幹部": 100000
}

ADMIN_ROLE_NAME = "TISN管理者"
ADMIN_CHANNEL_ID = 1537497919966544086
TOKEN_TRADE_PRICE = 250

# === トークン暗号化キーの準備（厳重保管） ===
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
            print(f"🔑 新しい暗号化キーを生成しました: {key.decode()}")
            print("⚠️ このキーを無くすと保管中のトークンは復元できなくなります！")
    return Fernet(key)

CIPHER = get_or_create_cipher()

def encrypt_token(raw_token: str) -> str:
    return CIPHER.encrypt(raw_token.encode()).decode()

def decrypt_token(encrypted: str) -> str:
    return CIPHER.decrypt(encrypted.encode()).decode()

# === ファイル ===
DATA_FILE = Path("points_data.json")
IMAGE_LOG_FILE = Path("image_log.json")
PENDING_FILE = Path("pending_requests.json")
TEMP_DM_FILE = Path("temp_dm_state.json")
TOKEN_MARKET_FILE = Path("token_market_encrypted.json")  # 暗号化済みトークンのみ保存

for f in [DATA_FILE, IMAGE_LOG_FILE, PENDING_FILE, TEMP_DM_FILE, TOKEN_MARKET_FILE]:
    if not f.exists():
        f.write_text("{}", encoding="utf-8")

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def is_image_used(image_url: str) -> bool:
    return image_url in load_json(IMAGE_LOG_FILE)
def mark_image_used(image_url: str, user_id: str, points: int):
    log = load_json(IMAGE_LOG_FILE)
    log[image_url] = {"user_id": user_id, "points": points, "used_at": datetime.utcnow().isoformat()}
    save_json(IMAGE_LOG_FILE, log)


# ==============================================
# ✅ 【最重要】実際にDiscord APIに問い合わせてトークンの存在を確認
# ==============================================
async def verify_real_discord_token(token: str) -> tuple[bool, str, dict | None]:
    """
    本当にDiscordに存在する有効なトークンか確認する
    戻り値: (有効か, メッセージ, ユーザー情報 or None)
    """
    # まず形式チェック（Discordトークンの正規表現）
    if not re.fullmatch(r"[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27}", token):
        return False, "❌ Discordトークンの形式が違います。正しいトークンを入力してください。", None

    headers = {"Authorization": token, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get("https://discord.com/api/v9/users/@me", headers=headers) as resp:
                if resp.status == 200:
                    user_data = await resp.json()
                    return True, f"✅ 有効なトークンです！所有者: {user_data.get('username', 'Unknown')}#{user_data.get('discriminator', '0000')}", user_data
                elif resp.status == 401:
                    return False, "❌ 無効なトークンです（認証エラー）。存在しないか期限切れです。", None
                elif resp.status == 429:
                    return False, "⚠️ API制限中。少し時間をおいて再試行してください。", None
                else:
                    return False, f"❌ 確認に失敗しました（ステータス: {resp.status}）", None
    except Exception as e:
        return False, f"❌ 通信エラー: {e}", None


# ==============================================
# ✅ ボタンビュー
# ==============================================
class RequestButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 ポイント申請", style=discord.ButtonStyle.primary, custom_id="request_points_btn")
    async def request_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "✅ DMを送信しました！画像と数字をDMから送ってください。", ephemeral=True
        )
        try:
            user = interaction.user
            await user.send(
                "## 📩 ポイント申請\n"
                "このチャンネルに **証拠の画像（通知のスクリーンショット）** と **数字（通知数）** を送ってください。\n\n"
                "✅ 送信例：画像を添付して `150` とだけ書いて送る\n"
                "⚠️ 同じ画像の使い回しは禁止です\n"
                "⚠️ 承認されるまでポイントは加算されません"
            )
            temp = load_json(TEMP_DM_FILE)
            temp[str(user.id)] = {"state": "waiting_for_input", "guild_id": str(interaction.guild_id)}
            save_json(TEMP_DM_FILE, temp)
        except Exception:
            await interaction.followup.send(
                "⚠️ DMを送信できませんでした。サーバー設定から「メッセージを許可」してから再度押してください。",
                ephemeral=True
            )


@bot.command(name="setup")
async def setup_board(ctx):
    if not any(r.name == ADMIN_ROLE_NAME for r in ctx.author.roles):
        await ctx.send("❌ 管理者専用コマンドです", delete_after=5)
        return
    embed = discord.Embed(
        title="🏅 ポイ活システム",
        description=(
            "下のボタンを押してポイントを申請してください。\n"
            "✅ 申請は **DMで完全非公開** で行われます\n"
            "✅ 他の人には内容は見えません\n"
            "✅ 承認後、ポイントを使ってロールやトークンを購入できます"
        ),
        color=0xFFD700
    )
    embed.set_footer(text=f"ロール購入: !shop / トークン売買: !token_sell / !token_buy")
    await ctx.send(embed=embed, view=RequestButtonView())
    await ctx.message.delete()


# ==============================================
# ✅ DM申請受信
# ==============================================
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
        return

    user_id = str(message.author.id)
    temp = load_json(TEMP_DM_FILE)

    if temp.get(user_id, {}).get("state") == "waiting_for_input":
        if not message.attachments:
            await message.author.send("⚠️ 画像が見当たりません。画像を添付して数字と一緒に送ってください。")
            return
        try:
            point = int(message.content.strip())
        except ValueError:
            await message.author.send("⚠️ 数字だけを入力してください。（例: `150`）")
            return

        image_url = message.attachments[0].url
        if is_image_used(image_url):
            await message.author.send("🚫 この画像は既に使用されています！別の写真を送ってください。")
            return

        pending = load_json(PENDING_FILE)
        req_id = f"{user_id}_{datetime.utcnow().timestamp()}"
        pending[req_id] = {
            "user_id": user_id, "user_name": str(message.author),
            "points": point, "image_url": image_url,
            "status": "pending", "applied_at": datetime.utcnow().isoformat()
        }
        save_json(PENDING_FILE, pending)
        del temp[user_id]
        save_json(TEMP_DM_FILE, temp)

        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(
                title="📩 ポイント申請（DM経由）",
                description=f"申請者: {message.author.mention} / {message.author}\n申請ポイント: **{point} pt**",
                color=0x2ECC71
            )
            embed.set_image(url=image_url)
            embed.set_footer(text=f"申請ID: {req_id}")
            await admin_channel.send(embed=embed, view=ApproveDenyView(req_id, point, user_id, image_url))

        await message.author.send(f"✅ 申請を送信しました！\n申請額: **{point} pt**\n管理者の承認をお待ちください。")
        return

    await bot.process_commands(message)


# ==============================================
# ✅ 管理者用承認/拒否
# ==============================================
class ApproveDenyView(discord.ui.View):
    def __init__(self, req_id, points, user_id, image_url):
        super().__init__(timeout=None)
        self.req_id, self.points, self.target_user_id, self.image_url = req_id, points, user_id, image_url

    @discord.ui.button(label="✅ 承認", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button):
        if not any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles):
            await interaction.response.send_message("❌ 管理者専用です", ephemeral=True); return
        data = load_json(DATA_FILE)
        uid = self.target_user_id
        if uid not in data: data[uid] = {"points": 0, "roles": []}
        data[uid]["points"] += self.points
        save_json(DATA_FILE, data)
        mark_image_used(self.image_url, uid, self.points)
        pending = load_json(PENDING_FILE)
        if self.req_id in pending: del pending[self.req_id]; save_json(PENDING_FILE, pending)
        await interaction.message.edit(
            embed=discord.Embed(title="✅ 承認済み", description=f"{self.points} pt 加算完了", color=0x2ECC71), view=None
        )
        await interaction.response.send_message(f"✅ {self.points} pt 加算完了", ephemeral=True)
        try:
            user = await bot.fetch_user(int(uid))
            if user: await user.send(f"🎉 ポイント申請が承認されました！ +{self.points} pt")
        except: pass

    @discord.ui.button(label="❌ 拒否", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button):
        if not any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles):
            await interaction.response.send_message("❌ 管理者専用です", ephemeral=True); return
        pending = load_json(PENDING_FILE)
        if self.req_id in pending: del pending[self.req_id]; save_json(PENDING_FILE, pending)
        await interaction.message.edit(
            embed=discord.Embed(title="❌ 拒否済み", description="申請は拒否されました", color=0xE74C3C), view=None
        )
        await interaction.response.send_message("❌ 拒否しました", ephemeral=True)
        try:
            user = await bot.fetch_user(int(self.target_user_id))
            if user: await user.send("❌ 残念ながらポイント申請は拒否されました。")
        except: pass


# ==============================================
# ✅ 共通コマンド
# ==============================================
@bot.command(name="mypoint")
async def mypoint(ctx):
    data = load_json(DATA_FILE)
    pts = data.get(str(ctx.author.id), {}).get("points", 0)
    await ctx.send(f"💰 {ctx.author.mention} のポイント: **{pts} pt**")

@bot.command(name="shop")
async def shop(ctx):
    txt = "## 🛒 ロールショップ（高い順）\n"
    for name, price in reversed(list(ROLE_PRICES.items())):
        txt += f"🏅 **{name}** → {price} pt\n"
    txt += f"\n💎 トークン売買: `!token_sell [コード]`(+{TOKEN_TRADE_PRICE}pt) / `!token_buy`(-{TOKEN_TRADE_PRICE}pt)"
    await ctx.send(txt)

@bot.command(name="buy")
async def buy(ctx, *, role_name):
    uid = str(ctx.author.id)
    data = load_json(DATA_FILE)
    user = data.get(uid, {"points":0, "roles":[]})
    guild = ctx.guild or (await bot.fetch_guilds().__anext__())
    if role_name not in ROLE_PRICES:
        await ctx.send("⚠️ `!shop` でロール名を確認してください"); return
    price = ROLE_PRICES[role_name]
    if role_name in user.get("roles", []):
        await ctx.send("⚠️ 既に所持しています"); return
    if user["points"] < price:
        await ctx.send(f"⚠️ ポイント不足\n必要: {price} pt / 所持: {user['points']} pt"); return
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        role = await guild.create_role(name=role_name, color=discord.Color.gold())
    await ctx.author.add_roles(role)
    user["points"] -= price
    user.setdefault("roles", []).append(role_name)
    data[uid] = user
    save_json(DATA_FILE, data)
    await ctx.send(f"🎉 「{role_name}」購入完了！\n💳 支払: {price} pt / 💰 残高: {user['points']} pt")


# ==============================================
# ✅ トークン売買（実在チェック＋暗号化保管）
# ==============================================
WARNING_TEXT = (
    "⚠️⚠️⚠️ 絶対に自分の本アカウントのトークンを入力しないでください！⚠️⚠️⚠️\n"
    "トークンが流出するとアカウントを乗っ取られる危険が極めて高いです。\n"
    "この機能は**捨てアカウント・テストアカウントのトークン専用**です。\n"
    "本アカウントのトークンを入力した場合の損害は一切責任を負いません。\n"
    "本当によろしいですか？"
)

class ConfirmSellView(discord.ui.View):
    def __init__(self, raw_token: str, author_id: str, author_name: str):
        super().__init__(timeout=120)
        self.raw_token = raw_token
        self.author_id = author_id
        self.author_name = author_name

    @discord.ui.button(label="✅ 理解した上で出品する", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, button):
        if str(interaction.user.id) != self.author_id:
            await interaction.response.send_message("❌ 申請者本人だけが確認できます", ephemeral=True); return

        await interaction.response.defer(ephemeral=True)

        # ✅ APIに問い合わせて実在確認
        ok, msg, user_data = await verify_real_discord_token(self.raw_token)
        if not ok:
            await interaction.followup.send(msg, ephemeral=True)
            # 管理者にも通知
            admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_ch:
                await admin_ch.send(
                    f"🚫 無効トークンを出品しようとしました\n"
                    f"ユーザー: {interaction.user.mention}\n理由: {msg}"
                )
            return

        # ✅ 有効なトークン → 暗号化して保存
        encrypted = encrypt_token(self.raw_token)
        market = load_json(TOKEN_MARKET_FILE)
        tid = f"T_{self.author_id}_{int(datetime.utcnow().timestamp())}"
        market[tid] = {
            "encrypted_token": encrypted,  # 平文は絶対に保存しない！
            "seller_id": self.author_id,
            "seller_name": self.author_name,
            "owner_username": f"{user_data.get('username')}#{user_data.get('discriminator')}",
            "owner_id": str(user_data.get("id")),
            "listed_at": datetime.utcnow().isoformat()
        }
        save_json(TOKEN_MARKET_FILE, market)

        # ✅ ポイント付与
        data = load_json(DATA_FILE)
        if self.author_id not in data:
            data[self.author_id] = {"points": 0, "roles": []}
        data[self.author_id]["points"] += TOKEN_TRADE_PRICE
        save_json(DATA_FILE, data)

        await interaction.followup.send(
            f"✅ トークンを出品しました！\n"
            f"🔍 確認結果: {msg}\n"
            f"💳 報酬: +{TOKEN_TRADE_PRICE} pt\n"
            f"💰 残高: {data[self.author_id]['points']} pt\n\n"
            f"🔒 トークンは暗号化されて安全に保管されます。",
            ephemeral=True
        )

        # ✅ 管理者通知（平文トークンは管理者にも送らない！安全のため）
        admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_ch:
            await admin_ch.send(
                f"💎 有効なトークンが出品されました\n"
                f"出品者: {interaction.user.mention}\n"
                f"所有者: {market[tid]['owner_username']}\n"
                f"🔒 トークンは暗号化済みのため閲覧できません。"
            )

    @discord.ui.button(label="❌ キャンセル", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button):
        if str(interaction.user.id) != self.author_id:
            await interaction.response.send_message("❌ 申請者本人だけがキャンセルできます", ephemeral=True); return
        await interaction.response.send_message("❌ 出品をキャンセルしました", ephemeral=True)
        self.stop()


@bot.command(name="token_sell")
async def token_sell(ctx, *, raw_token: str):
    """トークンを出品 → APIで実在確認 → 暗号化保管 → 250pt獲得"""
    uid = str(ctx.author.id)

    # 長すぎるメッセージを削除（トークンが見えないように）
    try:
        await ctx.message.delete()
    except:
        pass

    # DMで実行させる（公開チャンネルでトークンを見せないため）
    if not isinstance(ctx.channel, discord.DMChannel):
        await ctx.send(
            "⚠️ トークンは **DMでのみ** 送信してください！\n"
            "公開チャンネルで送信すると他の人に見えてしまい危険です。\n"
            "BotとのDMから `!token_sell [コード]` を再実行してください。",
            delete_after=10
        )
        return

    # 強力な警告
    await ctx.send(
        f"{WARNING_TEXT}\n\n"
        f"📝 入力されたトークンでDiscord APIへの存在確認を実行します。\n"
        "下のボタンを押してください。",
        view=ConfirmSellView(raw_token.strip(), uid, str(ctx.author))
    )


@bot.command(name="token_buy")
async def token_buy(ctx):
    """250pt支払い → 暗号化されたトークンをランダム取得 → 復号してDMで通知"""
    uid = str(ctx.author.id)
    data = load_json(DATA_FILE)
    market = load_json(TOKEN_MARKET_FILE)
    user = data.get(uid, {"points": 0, "roles": []})

    if user["points"] < TOKEN_TRADE_PRICE:
        await ctx.send(f"⚠️ ポイント不足！必要: {TOKEN_TRADE_PRICE} pt / 所持: {user['points']} pt", ephemeral=True)
        return
    if not market:
        await ctx.send("📭 現在出品されているトークンがありません！", ephemeral=True)
        return

    # ランダムに1つ選ぶ
    tid = random.choice(list(market.keys()))
    item = market.pop(tid)
    save_json(TOKEN_MARKET_FILE, market)

    # 復号
    try:
        raw_token = decrypt_token(item["encrypted_token"])
    except Exception:
        await ctx.send("❌ トークンの復号に失敗しました。別のトークンをお試しください。", ephemeral=True)
        return

    # 支払い
    user["points"] -= TOKEN_TRADE_PRICE
    data[uid] = user
    save_json(DATA_FILE, data)

    # 購入者にだけDMでトークンを通知
    try:
        await ctx.author.send(
            f"🎉 トークンを購入しました！\n"
            f"💳 支払: -{TOKEN_TRADE_PRICE} pt\n"
            f"👤 元アカウント: {item['owner_username']}\n"
            f"🔑 トークン: `{raw_token}`\n"
            f"💰 残高: {user['points']} pt\n\n"
            f"⚠️ このトークンは第三者に絶対に見せないでください。"
        )
        await ctx.send("✅ DMにトークンを送信しました！", ephemeral=True)
    except Exception:
        await ctx.send("❌ DMを送信できません。「メッセージを許可」して再度実行してください。", ephemeral=True)
        # ロールバック
        user["points"] += TOKEN_TRADE_PRICE
        data[uid] = user
        market[tid] = item
        save_json(DATA_FILE, data)
        save_json(TOKEN_MARKET_FILE, market)
        return

    # 出品者に通知
    try:
        seller = await bot.fetch_user(int(item["seller_id"]))
        if seller:
            await seller.send(
                f"📢 あなたのトークン（{item['owner_username']}）が購入されました！\n"
                f"💳 既に +{TOKEN_TRADE_PRICE} pt は付与済みです。"
            )
    except:
        pass


@bot.event
async def on_ready():
    bot.add_view(RequestButtonView())
    print(f"起動: {bot.user} | ポイ活＋実在確認トークン売買システム")
    print(f"管理者通知チャンネル: {ADMIN_CHANNEL_ID}")
    print("🔒 トークンは全て暗号化して保管されます")


TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN を環境変数に設定してください")

bot.run(TOKEN)