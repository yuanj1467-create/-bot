import discord
from discord.ext import commands
import os
import json
from pathlib import Path
from datetime import datetime

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === 設定 ===
ROLE_PRICES = {
    "一等兵": 100,
    "曹長": 500,
    "大尉": 1500,
    "ムスカ大佐": 5000,
    "TISN最高幹部": 15000
}

ADMIN_ROLE_NAME = "TISN管理者"  # 承認権限を持つロール
ADMIN_CHANNEL_ID = 1537497919966544086  # ← 管理者用通知チャンネルのIDをここに入れる（例: 123456789012345678）

# === データ保存 ===
DATA_FILE = Path("points_data.json")
IMAGE_LOG_FILE = Path("image_log.json")
PENDING_FILE = Path("pending_requests.json")

for f in [DATA_FILE, IMAGE_LOG_FILE, PENDING_FILE]:
    if not f.exists():
        f.write_text("{}", encoding="utf-8")

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# === 重複チェック：この画像は過去に使われたことがあるか ===
def is_image_used(image_url: str) -> bool:
    log = load_json(IMAGE_LOG_FILE)
    return image_url in log

# === 使用済み画像として記録 ===
def mark_image_used(image_url: str, user_id: str, points: int):
    log = load_json(IMAGE_LOG_FILE)
    log[image_url] = {
        "user_id": user_id,
        "points": points,
        "used_at": datetime.utcnow().isoformat()
    }
    save_json(IMAGE_LOG_FILE, log)

# ==============================================
# ✅ ユーザー：ポイント申請（画像＋数字）
# ==============================================
@bot.command(name="p", help="!p 数字 でポイント申請（証拠画像を添付）")
async def request_points(ctx, point: int):
    user_id = str(ctx.author.id)

    # 画像チェック
    if not ctx.message.attachments:
        await ctx.send("⚠️ 証拠のスクリーンショットを画像添付してください！")
        return

    # 1枚目の画像URLを取得
    image_url = ctx.message.attachments[0].url

    # ✅ 使い回しチェック
    if is_image_used(image_url):
        await ctx.send("🚫 この画像は既に使用されています！写真の使い回しは禁止です。")
        return

    # 申請を仮登録
    pending = load_json(PENDING_FILE)
    req_id = f"{user_id}_{datetime.utcnow().timestamp()}"
    pending[req_id] = {
        "user_id": user_id,
        "user_name": str(ctx.author),
        "points": point,
        "image_url": image_url,
        "status": "pending",
        "applied_at": datetime.utcnow().isoformat()
    }
    save_json(PENDING_FILE, pending)

    # ✅ 管理者チャンネルに通知
    guild = ctx.guild
    admin_channel = guild.get_channel(ADMIN_CHANNEL_ID) if ADMIN_CHANNEL_ID else None

    embed = discord.Embed(
        title="📩 ポイント申請が届いています",
        description=f"申請者: {ctx.author.mention}\n申請ポイント: **{point} pt**\n\n👇 下のボタンで承認または拒否",
        color=0x2ECC71
    )
    embed.set_image(url=image_url)
    embed.set_footer(text=f"申請ID: {req_id}")

    view = ApproveDenyView(req_id, point, user_id, image_url)

    if admin_channel:
        await admin_channel.send(embed=embed, view=view)
        await ctx.send(f"✅ 申請を送信しました！管理者の承認をお待ちください。\n申請額: {point} pt")
    else:
        await ctx.send("⚠️ 管理者チャンネルが設定されていません。")


# ==============================================
# ✅ 管理者用：承認/拒否ボタン
# ==============================================
class ApproveDenyView(discord.ui.View):
    def __init__(self, req_id, points, user_id, image_url):
        super().__init__(timeout=None)
        self.req_id = req_id
        self.points = points
        self.target_user_id = user_id
        self.image_url = image_url

    @discord.ui.button(label="✅ 承認", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 管理者権限チェック
        if not any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles):
            await interaction.response.send_message("❌ 管理者専用です", ephemeral=True)
            return

        # ポイント加算
        data = load_json(DATA_FILE)
        uid = self.target_user_id
        if uid not in data:
            data[uid] = {"points": 0, "roles": []}
        data[uid]["points"] += self.points
        save_json(DATA_FILE, data)

        # ✅ 画像を使用済みとして記録（使い回し防止）
        mark_image_used(self.image_url, uid, self.points)

        # 申請を削除
        pending = load_json(PENDING_FILE)
        if self.req_id in pending:
            del pending[self.req_id]
            save_json(PENDING_FILE, pending)

        await interaction.message.edit(
            embed=discord.Embed(title="✅ 承認済み", description=f"{self.points} pt を加算しました。", color=0x2ECC71),
            view=None
        )
        await interaction.response.send_message(f"✅ {interaction.user.mention} → **{self.points} pt** 加算完了", ephemeral=True)

        # 申請者にも通知
        try:
            user = interaction.guild.get_member(int(uid))
            if user:
                await user.send(f"🎉 ポイント申請が承認されました！ +{self.points} pt")
        except:
            pass

    @discord.ui.button(label="❌ 拒否", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles):
            await interaction.response.send_message("❌ 管理者専用です", ephemeral=True)
            return

        # 申請を削除
        pending = load_json(PENDING_FILE)
        if self.req_id in pending:
            del pending[self.req_id]
            save_json(PENDING_FILE, pending)

        await interaction.message.edit(
            embed=discord.Embed(title="❌ 拒否済み", description="ポイント申請は拒否されました。", color=0xE74C3C),
            view=None
        )
        await interaction.response.send_message("❌ 申請を拒否しました。", ephemeral=True)


# ==============================================
# ✅ 残高確認 / ショップ / 購入（変更なし）
# ==============================================
@bot.command(name="mypoint")
async def mypoint(ctx):
    data = load_json(DATA_FILE)
    pts = data.get(str(ctx.author.id), {}).get("points", 0)
    await ctx.send(f"💰 {ctx.author.mention} のポイント: {pts} pt")

@bot.command(name="shop")
async def shop(ctx):
    txt = "## 🛒 ロールショップ\n"
    for name, price in reversed(list(ROLE_PRICES.items())):
        txt += f"🏅 {name} → **{price} pt**\n"
    await ctx.send(txt)

@bot.command(name="buy")
async def buy(ctx, *, role_name):
    uid = str(ctx.author.id)
    data = load_json(DATA_FILE)
    user = data.get(uid, {"points":0, "roles":[]})
    guild = ctx.guild

    if role_name not in ROLE_PRICES:
        await ctx.send("⚠️ !shop でロール名を確認してください")
        return
    price = ROLE_PRICES[role_name]

    if role_name in user.get("roles", []):
        await ctx.send("⚠️ 既に所持しています")
        return
    if user["points"] < price:
        await ctx.send(f"⚠️ ポイント不足 必要:{price} pt / 所持:{user['points']} pt")
        return

    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        role = await guild.create_role(name=role_name, color=discord.Color.gold())

    await ctx.author.add_roles(role)
    user["points"] -= price
    user.setdefault("roles", []).append(role_name)
    data[uid] = user
    save_json(DATA_FILE, data)
    await ctx.send(f"🎉 「{role_name}」購入！ -{price} pt / 残高: {user['points']} pt")


@bot.event
async def on_ready():
    print(f"起動: {bot.user} | 承認制ポイ活システム")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TOKENを設定してください")
bot.run(TOKEN)