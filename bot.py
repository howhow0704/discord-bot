import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import re
from datetime import datetime, timedelta

# 設置日誌
current_time = datetime.now().strftime('%Y_%m_%d_%H_%M')
log_folder = r'C:\Users\jtjty\Desktop\mod\DC BOT\MC BOT\log'
os.makedirs(log_folder, exist_ok=True)
log_file = os.path.join(log_folder, f'{current_time}.log')

logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)

debug_handler = logging.FileHandler(log_file, encoding='utf-8')
debug_handler.setLevel(logging.DEBUG)
debug_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
debug_handler.setFormatter(debug_formatter)
logger.addHandler(debug_handler)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s [%(levelname)s]: %(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# 加載環境變數
load_dotenv(dotenv_path='api.env')
TOKEN = os.getenv('DISCORD_TOKEN')

# 檢查必要的環境變數
if not TOKEN:
    logger.critical("缺少 DISCORD_TOKEN 環境變數。請檢查 .env 文件。")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix='/', intents=intents, help_command=None)

# 定義一個簡單的歌曲資料結構
class Song:
    def __init__(self, source_url: str, webpage_url: str, title: str, thumbnail: str):
        self.source_url = source_url      # 用於播放的音頻流URL
        self.webpage_url = webpage_url    # YouTube影片連結
        self.title = title
        self.thumbnail = thumbnail        # 封面圖屬性
        
    def __repr__(self):
        return f"Song(title={self.title}, source_url={self.source_url}, webpage_url={self.webpage_url})"

# 音樂播放隊列
class MusicPlayer:
    def __init__(self, ctx, loop):
        self.ctx = ctx
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        self.current = None
        self.loop_flag = False
        self.queue_loop = False
        self.voice_client = ctx.voice_client
        self.loop = loop
        self.control_view = MusicControls(self.ctx, self)  # 儲存控制視圖
        self.control_message = None  # 儲存控制訊息
        self.task = asyncio.create_task(self.player_loop())

    async def player_loop(self):
        while True:
            self.next.clear()
            try:
                # 从队列中获取下一首歌曲
                song = await self.queue.get()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"從隊列中獲取歌曲時發生錯誤: {e}")
                continue
            
            self.current = song
            try:
                if not hasattr(song, 'source_url') or not hasattr(song, 'webpage_url'):
                    raise AttributeError("Song object is missing 'source_url' or 'webpage_url' attribute.")
    
                # 如果当前正在播放音频，先停止
                if self.voice_client.is_playing():
                    self.voice_client.stop()
    
                # 创建新的 FFmpegPCMAudio 对象播放音频
                source = discord.FFmpegPCMAudio(
                    song.source_url,
                    before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                )
                self.voice_client.play(
                    source,
                    after=self.after_play
                )
    
                # 获取当前时间并转换为 UTC+8
                now_time = datetime.utcnow() + timedelta(hours=8)
                formatted_time = now_time.strftime('%Y-%m-%d %H:%M:%S')
    
                # 准备嵌入消息
                embed = discord.Embed(
                    title="🎶 正在播放",
                    description=f"[{song.title}]({song.webpage_url})",
                    color=discord.Color.green(),
                    timestamp=now_time
                )
                embed.set_thumbnail(url=song.thumbnail)
                embed.add_field(
                    name="循環狀態",
                    value="🔁 循環已啟用" if self.loop_flag else "🔁 循環已停用",
                    inline=False
                )
                embed.set_footer(
                    text=f"請求者: {self.ctx.author.display_name} • {formatted_time}",
                    icon_url=self.ctx.author.avatar.url if self.ctx.author.avatar else None
                )
    
                # 更新控制訊息嵌入
                if not self.control_message or not hasattr(self.control_message, "edit"):
                    controls = MusicControls(self.ctx, self)
                    self.control_message = await self.ctx.send(embed=embed, view=controls)
                else:
                    try:
                        await self.control_message.edit(embed=embed, view=self.control_message.view)
                    except Exception as e:
                        logger.error(f"編輯控制訊息失敗，重新發送控制訊息：{e}")
                        controls = MusicControls(self.ctx, self)
                        self.control_message = await self.ctx.send(embed=embed, view=controls)
    
            except Exception as e:
                logger.error(f"播放歌曲時發生錯誤: {e}")
                self.current = None
                continue
            
            # 等待歌曲播放完成或被跳过
            await self.next.wait()
    
            # 循环逻辑
            if self.loop_flag or self.queue_loop:
                await self.queue.put(song)
    
            # 更新嵌入消息为播放清单状态
            if self.queue.empty():
                try:
                    embed = discord.Embed(
                        title="播放清單",
                        description="🎵 播放清單已播放完畢。",
                        color=discord.Color.red()
                    )
                    await self.ctx.send(embed=embed)  # 發送播放結束訊息
                except Exception as e:
                    logger.error(f"更新播放清單完成嵌入時出錯: {e}")

    def after_play(self, error):
        if error:
            logger.error(f"播放出錯: {error}")
        self.loop.call_soon_threadsafe(self.next.set)
        self.loop.call_soon_threadsafe(self.clear_current)

    def clear_current(self):
        self.current = None

    def add_song(self, song: Song):
        self.queue.put_nowait(song)

    def skip(self):
        if self.voice_client.is_playing():
            self.voice_client.stop()

    def pause(self):
        if self.voice_client.is_playing():
            self.voice_client.pause()

    def resume(self):
        if self.voice_client.is_paused():
            self.voice_client.resume()

    def stop(self):
        self.queue = asyncio.Queue()
        if self.voice_client.is_playing():
            self.voice_client.stop()

    def get_queue_snapshot(self):
        return list(self.queue._queue)
    
players = {}

# is_url判斷是否為URL
def is_url(search: str) -> bool:
    url_pattern = re.compile(
        r'^(?:http|ftp)s?://'  # http:// 或 https://
        r'(?:\S+(?::\S*)?@)?'  # 可选的用户:密码@
        r'(?:'  # IP地址或域名
        r'(?:(?:[1-9]\d?|1\d\d|2[01]\d|22[0-3])\.' +
        r'(?:1?\d{1,2}|2[0-4]\d|25[0-5])\.' +
        r'(?:1?\d{1,2}|2[0-4]\d|25[0-5])\.' +
        r'(?:1?\d{1,2}|2[0-4]\d|25[0-5])' +
        r')|' +
        r'(?:(?:[a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,})' +
        r')' +
        r'(?::\d{2,5})?' +  # 可选的端口
        r'(?:/\S*)?$', re.IGNORECASE)
    return re.match(url_pattern, search) is not None

# 搜尋返回多個歌曲結果
async def search_songs(search: str) -> list:
    ytdl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'default_search': 'ytsearch5',
        # 'source_address': '0.0.0.0'  # 已移除
    }

    try:
        with yt_dlp.YoutubeDL(ytdl_opts) as ytdl:
            info = ytdl.extract_info(search, download=False)
            if 'entries' in info:
                return [
                    Song(
                        entry['url'],                        # source_url 用於播放
                        entry['webpage_url'],                # YouTube影片連結
                        entry.get('title', '未知標題'),
                        entry.get('thumbnail', 'https://i.imgur.com/your-default-image.png')  # 提取封面圖，若無則設置預設圖片
                    )
                    for entry in info['entries']
                ]
            else:
                return [
                    Song(
                        info['url'],                         # source_url 用於播放
                        info['webpage_url'],                 # YouTube影片連結
                        info.get('title', '未知標題'),
                        info.get('thumbnail', 'https://i.imgur.com/your-default-image.png')  # 提取封面圖，若無則設置預設圖片
                    )
                ]
    except Exception as e:
        logger.error(f"搜索歌曲時發生錯誤: {e}")
        return []

# 定義 SongSelect 類
class SongSelect(discord.ui.Select):
    def __init__(self, songs: list, player: MusicPlayer, ctx: commands.Context):
        self.songs = songs
        self.player = player
        self.ctx = ctx

        options = [
            discord.SelectOption(label=song.title, description=f"选择 {i+1}", value=str(i))
            for i, song in enumerate(songs[:5])  # 仅展示前5首歌曲
        ]

        super().__init__(placeholder='选择要播放的歌曲...', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_index = int(self.values[0])  # 获取用户选择的索引
        selected_song = self.songs[selected_index]

        # 将选定的歌曲添加到播放队列
        self.player.add_song(selected_song)
        await interaction.response.send_message(
            f"➕ 已加入播放列表：**[{selected_song.title}]({selected_song.webpage_url})**", ephemeral=True
        )
        self.view.stop()  # 停止选择视图，避免用户重复选择

# 定義 SongSelectView 類
class SongSelectView(discord.ui.View):
    def __init__(self, songs: list, player: MusicPlayer, ctx: commands.Context, *, timeout=60):
        super().__init__(timeout=timeout)
        self.songs = songs
        self.player = player
        self.ctx = ctx
        self.user = ctx.author

        # 添加选择菜单
        self.add_item(SongSelect(songs, player, ctx))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await interaction.response.send_message("你无法操作这个选择菜单。", ephemeral=True)
            return False
        return True

# 定義 QueueEmbedView 類
class QueueEmbedView(discord.ui.View):
    def __init__(self, player: MusicPlayer, ctx: commands.Context):
        super().__init__(timeout=300)  # 5分鐘超時
        self.player = player
        self.ctx = ctx
        self.page = 0
        self.songs_per_page = 5
        queue_size = self.player.queue.qsize()
        self.total_pages = max(1, (queue_size + self.songs_per_page - 1) // self.songs_per_page)

        self.previous_button = discord.ui.Button(label="上一頁", style=discord.ButtonStyle.primary, emoji="⬅️")
        self.previous_button.callback = self.previous_page
        self.next_button = discord.ui.Button(label="下一頁", style=discord.ButtonStyle.primary, emoji="➡️")
        self.next_button.callback = self.next_page
        self.add_item(self.previous_button)
        self.add_item(self.next_button)

    def generate_embed(self):
        queue_snapshot = self.player.get_queue_snapshot()  # 獲取隊列快照
        if not queue_snapshot:
            embed = discord.Embed(
                title="📜 播放清單",
                description="目前播放清單為空。",
                color=discord.Color.blue()
            )
            return embed

        # 計算當前頁面的索引
        start_index = self.page * self.songs_per_page
        end_index = start_index + self.songs_per_page
        upcoming = queue_snapshot[start_index:end_index]
        
        # 整合歌曲列表到描述中
        song_list = "\n".join(
            [f"{i}. [{song.title}]({song.webpage_url})" for i, song in enumerate(upcoming, start=start_index + 1)]
        )
        
        embed = discord.Embed(
            title="📜 播放清單",
            description=f"第 {self.page + 1}/{self.total_pages} 頁\n\n" + "\n".join(
                [f"{i}. [{song.title}]({song.webpage_url})" for i, song in enumerate(upcoming, start=start_index + 1)]
            ),
            color=discord.Color.blue()
        )

        embed.set_footer(
            text=f"請求者: {self.ctx.author.display_name}",
            icon_url=self.ctx.author.avatar.url if self.ctx.author.avatar else None
        )
        return embed
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.ctx.author  # 確保只有發起者可以操作

    async def previous_page(self, interaction: discord.Interaction):
        if self.page > 0:
            self.page -= 1
            embed = self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("已經是第一頁了。", ephemeral=True)

    async def next_page(self, interaction: discord.Interaction):
        if self.page < self.total_pages - 1:
            self.page += 1
            embed = self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("已經是最後一頁了。", ephemeral=True)

# 定義 MusicControls 類
class MusicControls(discord.ui.View):
    def __init__(self, ctx: commands.Context, player: MusicPlayer):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.player = player

        # 创建按钮
        self.pause_resume_button = discord.ui.Button(
            label='暫停',
            emoji='⏸️',
            style=discord.ButtonStyle.secondary
        )
        self.pause_resume_button.callback = self.pause_resume

        self.skip_button = discord.ui.Button(
            label='跳過',
            emoji='⏭️',
            style=discord.ButtonStyle.primary
        )
        self.skip_button.callback = self.skip

        self.loop_button = discord.ui.Button(
            label='循環',
            emoji='🔁',
            style=discord.ButtonStyle.danger
        )
        self.loop_button.callback = self.toggle_loop

        self.view_queue_button = discord.ui.Button(
            label='查看播放清單',
            emoji='📜',
            style=discord.ButtonStyle.success,
            custom_id="view_queue"  # 使用固定 custom_id
        )
        self.view_queue_button.callback = self.view_queue  # 綁定回調函數

        # 添加按钮到 View
        self.add_item(self.pause_resume_button)
        self.add_item(self.skip_button)
        self.add_item(self.loop_button)
        self.add_item(self.view_queue_button)

    async def pause_resume(self, interaction: discord.Interaction):
        if self.player.voice_client.is_playing():
            self.player.pause()
            self.pause_resume_button.label = '繼續'
            self.pause_resume_button.emoji = '▶️'
            self.pause_resume_button.style = discord.ButtonStyle.success
            await interaction.response.send_message("⏸️ 已暫停播放！", ephemeral=True)
        elif self.player.voice_client.is_paused():
            self.player.resume()
            self.pause_resume_button.label = '暫停'
            self.pause_resume_button.emoji = '⏸️'
            self.pause_resume_button.style = discord.ButtonStyle.danger
            await interaction.response.send_message("▶️ 已恢復播放！", ephemeral=True)

        # 更新嵌入訊息
        message = interaction.message
        if message.embeds:
            embed = message.embeds[0]
            if self.player.voice_client.is_paused():
                embed.title = "🎶 已暫停"
                embed.color = discord.Color.orange()  # 更改顏色以反映暫停狀態
            else:
                embed.title = "🎶 正在播放"
                embed.color = discord.Color.green()
            try:
                await self.player.control_message.edit(embed=embed, view=self.player.control_view)
            except AttributeError as e:
                logger.error(f"無法編輯控制訊息：{e}")

    async def skip(self, interaction: discord.Interaction):
        if self.player.voice_client.is_playing():
            self.player.skip()
            await interaction.response.send_message("⏭️ 已跳過當前歌曲！", ephemeral=True)

    async def toggle_loop(self, interaction: discord.Interaction):
        self.player.loop_flag = not self.player.loop_flag
        if self.player.loop_flag:
            self.loop_button.style = discord.ButtonStyle.success
            status = '啟用'
        else:
            self.loop_button.style = discord.ButtonStyle.danger
            status = '停用'

        await interaction.response.send_message(f"🔁 循環播放已 {status}！", ephemeral=True)

        # 更新嵌入訊息中的循環狀態
        message = interaction.message
        if message.embeds:
            embed = message.embeds[0]
            # 更新循環狀態字段
            for idx, field in enumerate(embed.fields):
                if field.name == "循環狀態":
                    embed.set_field_at(
                        index=idx,
                        name="循環狀態",
                        value="🔁 循環已啟用" if self.player.loop_flag else "🔁 循環已停用",
                        inline=False
                    )
                    break
            try:
                await self.player.control_message.edit(embed=embed, view=self.player.control_view)
            except AttributeError as e:
                logger.error(f"無法編輯控制訊息：{e}")

    async def view_queue(self, interaction: discord.Interaction):
        if not self.player.queue.empty():
            view = QueueEmbedView(self.player, self.ctx)
            embed = view.generate_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message("🎵 播放清單為空！", ephemeral=True)

# 測試指令：/ping
@bot.hybrid_command(name='ping', description='測試機器人是否運行')
async def ping(ctx: commands.Context):
    await ctx.send('Pong!')

# 錯誤處理
@bot.event
async def on_application_command_error(interaction: discord.Interaction, error):
    if isinstance(error, commands.CommandNotFound):
        await interaction.response.send_message('指令不存在。', ephemeral=True)
    elif isinstance(error, commands.MissingPermissions):
        await interaction.response.send_message('你沒有執行這個指令的權限。', ephemeral=True)
    elif isinstance(error, commands.BotMissingPermissions):
        await interaction.response.send_message('機器人缺少必要的權限。', ephemeral=True)
    else:
        logger.error(f"Unhandled command error: {error}")
        if interaction.response.is_done():
            await interaction.followup.send(f'發生錯誤: {error}', ephemeral=True)
        else:
            await interaction.response.send_message(f'發生錯誤: {error}', ephemeral=True)

# 同步指令樹
@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logger.info('------')
    try:
        # 獲取所有註冊的指令名稱
        commands_list = bot.tree.get_commands()
        logger.info(f"Registered commands: {[cmd.name for cmd in commands_list]}")

        # 全域同步指令
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} commands globally.")
    except Exception as e:
        logger.error(f"Failed to sync commands globally: {e}")

# 混合指令：強制同步指令（全域）
@bot.hybrid_command(name='sync', description='手動同步指令到 Discord 全域')
async def sync_commands(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"已全域同步 {len(synced)} 條指令。")
    except Exception as e:
        await ctx.send(f"同步指令時發生錯誤：{e}")

# 混合指令：加入語音頻道
@bot.hybrid_command(name='join', description='將機器人加入到您目前所在的語音頻道')
async def join(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        try:
            await channel.connect()
            username = ctx.author.mention
            await ctx.send(f'**:inbox_tray: | 已加入語音頻道**\n已成功加入 {username} 的語音頻道')
        except Exception as e:
            await ctx.send(f'{ctx.author.mention} 加入語音頻道時發生錯誤: {e}')
    else:
        await ctx.send('你不在任何語音頻道中。')

# 混合指令：離開語音頻道
@bot.hybrid_command(name='leave', description='使機器人離開其所在的語音頻道')
async def leave(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    if ctx.voice_client:
        try:
            await ctx.voice_client.disconnect()
            players.pop(ctx.guild.id, None)  # 清理播放器實例
            await ctx.send('已離開語音頻道並清理播放資源。')
        except Exception as e:
            await ctx.send(f'離開語音頻道時發生錯誤: {e}')
    else:
        await ctx.send('我不在任何語音頻道中。')

# 混合指令：移動到用戶所在的語音頻道
@bot.hybrid_command(name='move', description='將機器人移動到您目前所在的語音頻道')
async def move(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client:
            try:
                await ctx.voice_client.move_to(channel)
                await ctx.send(f'已移動到語音頻道: {channel.name}')
            except Exception as e:
                await ctx.send(f'移動語音頻道時發生錯誤: {e}')
        else:
            try:
                await channel.connect()
                await ctx.send(f'已加入語音頻道: {channel.name}')
            except Exception as e:
                await ctx.send(f'加入語音頻道時發生錯誤: {e}')
    else:
        await ctx.send('你不在任何語音頻道中。')

# 混合指令：播放音樂
@bot.hybrid_command(name='play', description='根据提供的 URL 或歌名搜索并播放音乐。如果有正在播放的歌曲，则加入播放列表。')
async def play(ctx: commands.Context, *, search: str):
    is_interaction = hasattr(ctx, "interaction") and ctx.interaction is not None

    if is_interaction:
        await ctx.defer(ephemeral=False)
    else:
        await ctx.trigger_typing()

    if not ctx.author.voice:
        message = '❌ 你必须在一个语音频道中才能播放音乐！'
        if is_interaction:
            await ctx.interaction.followup.send(message)
        else:
            await ctx.send(message)
        return

    if not ctx.voice_client:
        try:
            await ctx.author.voice.channel.connect()
            if not is_interaction:
                await ctx.send(f'**:inbox_tray: | 已加入語音頻道**\n已成功加入 {ctx.author.mention} 的語音頻道')
        except Exception as e:
            message = f'❌ 无法加入语音频道: {e}'
            if is_interaction:
                await ctx.interaction.followup.send(message)
            else:
                await ctx.send(message)
            return

    player = players.get(ctx.guild.id)
    if not player:
        player = MusicPlayer(ctx, ctx.bot.loop)
        players[ctx.guild.id] = player

    try:
        songs = await search_songs(search)
    except Exception as e:
        message = f'❌ 搜索音乐时发生错误: {e}'
        if is_interaction:
            await ctx.interaction.followup.send(message)
        else:
            await ctx.send(message)
        return

    if not songs:
        message = '❌ 没有找到相关歌曲。'
        if is_interaction:
            await ctx.interaction.followup.send(message)
        else:
            await ctx.send(message)
        return

    if len(songs) > 1:
        # 展示选择菜单
        embed = discord.Embed(
            title="🔍 | 搜索结果",
            description="找到了多个结果，请选择您想播放的歌曲：",
            color=discord.Color.blue()
        )
        view = SongSelectView(songs, player, ctx)
        if is_interaction:
            await ctx.interaction.followup.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed, view=view)
    else:
        # 只有一首歌，直接添加到队列
        song = songs[0]
        player.add_song(song)
        await ctx.send(f"➕ 已加入播放列表：**[{song.title}]({song.webpage_url})**")

# 混合指令：暫停音樂
@bot.hybrid_command(name='pause', description='暫停當前播放的音樂。')
async def pause(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    player = players.get(ctx.guild.id)
    if player:
        player.pause()
        await ctx.send('已暫停音樂。')
    else:
        await ctx.send('目前沒有播放音樂。')

# 混合指令：繼續播放音樂
@bot.hybrid_command(name='resume', description='繼續播放暫停的音樂。')
async def resume(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    player = players.get(ctx.guild.id)
    if player:
        player.resume()
        await ctx.send('已繼續播放音樂。')
    else:
        await ctx.send('目前沒有暫停的音樂。')

# 混合指令：跳過當前音樂
@bot.hybrid_command(name='skip', description='跳過當前播放的歌曲。')
async def skip(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    player = players.get(ctx.guild.id)
    if player:
        player.skip()
        await ctx.send('已跳過當前音樂。')
    else:
        await ctx.send('目前沒有播放音樂。')

# 混合指令：循環當前音樂
@bot.hybrid_command(name='loop', description='循環播放當前歌曲。')
async def loop_song(ctx: commands.Context):
    await ctx.defer()
    player = players.get(ctx.guild.id)
    if player:
        player.loop_flag = not player.loop_flag
        status = '啟用' if player.loop_flag else '停用'
        await ctx.send(f'循環當前音樂已 {status}。')
    else:
        await ctx.send('目前沒有播放音樂。')

# 混合指令：查看當前播放歌曲
@bot.hybrid_command(name='np', description='顯示當前正在播放的歌曲。')
async def np(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    player = players.get(ctx.guild.id)
    if player and player.current:
        await ctx.send(f'當前播放: **[{player.current.title}]({player.current.webpage_url})**')
    else:
        await ctx.send('目前沒有播放音樂。')

# 混合指令：列出播放清單
@bot.hybrid_command(name='queue', description='列出播放清單中的所有歌曲。')
async def queue_(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    player = players.get(ctx.guild.id)
    if player and not player.queue.empty():
        upcoming = list(player.queue._queue)
        msg = '播放清單:\n'
        for i, song in enumerate(upcoming, 1):
            msg += f'{i}. [{song.title}]({song.webpage_url})\n'
        await ctx.send(msg)
    else:
        await ctx.send('播放清單為空。')

# 混合指令：循環播放播放清單
@bot.hybrid_command(name='queueloop', description='循環播放整個播放清單。')
async def queue_loop(ctx: commands.Context):
    await ctx.defer()  # 延迟响应
    player = players.get(ctx.guild.id)
    if player:
        player.queue_loop = not player.queue_loop
        status = '啟用' if player.queue_loop else '停用'
        await ctx.send(f'循環播放清單已 {status}。')
    else:
        await ctx.send('目前沒有播放音樂。')

# 混合指令：顯示幫助訊息
@bot.hybrid_command(name='help', description='顯示所有可用指令及其說明。')
async def help_command(ctx: commands.Context):
    embed = discord.Embed(
        title=":regional_indicator_q: | 指令說明 | 基本指令",
        description="若遇到錯誤可以先閱讀訊息所提示的方法來排錯喔",
        color=0x1ABC9C
    )
    embed.add_field(
        name="**/help**",
        value="**你目前就正在用這個喔，輸入會列出指令說明 **",
        inline=False
    )
    embed.add_field(
        name="**/join**",
        value=(
            "**將機器人加入到您目前所在的語音頻道 **\n"
            "**【！】若遇到錯誤 JOINFAIL **\n"
            "**可能是您沒有加入到任一語音頻道中，或是機器人無權限加入該頻道導致**"
        ),
        inline=False
    )
    embed.add_field(
        name="**/leave**",
        value=(
            "**使機器人離開其所在的語音頻道 **\n"
            "**【！】若遇到錯誤 LEAVEFAIL **\n"
            "**可能是機器人並沒有加入到任一語音頻道中導致**"
        ),
        inline=False
    )
    embed.add_field(
        name="**/play <歌名或URL>**",
        value="**根據提供的 URL 或歌名搜尋並播放音樂。如果有正在播放的歌曲，則加入播放清單。**",
        inline=False
    )
    embed.add_field(
        name="**/pause**",
        value="**暫停當前播放的音樂。**",
        inline=False
    )
    embed.add_field(
        name="**/resume**",
        value="**繼續播放暫停的音樂。**",
        inline=False
    )
    embed.add_field(
        name="**/skip**",
        value="**跳過當前播放的歌曲。**",
        inline=False
    )
    embed.add_field(
        name="**/loop**",
        value="**循環播放當前歌曲。**",
        inline=False
    )
    embed.add_field(
        name="**/np**",
        value="**顯示當前正在播放的歌曲。**",
        inline=False
    )
    embed.add_field(
        name="**/queue**",
        value="**列出播放清單中的所有歌曲。**",
        inline=False
    )
    embed.add_field(
        name="**/queueloop**",
        value="**循環播放整個播放清單。**",
        inline=False
    )
    embed.set_footer(text="使用上述指令來控制音樂播放。")
    await ctx.send(embed=embed)

# 啟動機器人
bot.run(TOKEN)
