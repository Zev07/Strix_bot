import asyncio
import os
import shutil

import discord
import yt_dlp
from discord import Bot
from discord.player import FFmpegPCMAudio
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = discord.Bot(intents=intents)

FFMPEG_EXEC = shutil.which("ffmpeg") or "ffmpeg"

FFMPEG_OPTS = {
    "executable": FFMPEG_EXEC,
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}

COOKIES_PATH = "cookies.txt"

YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": False,
    "default_search": "auto",
    "source_address": "0.0.0.0",
    "cookiefile": COOKIES_PATH,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

INACTIVITY_TIMEOUT = 300
inactivity_tasks = {}

def cancel_inactivity_task(guild_id):
    task = inactivity_tasks.get(guild_id)
    if task and not task.done():
        task.cancel()
    inactivity_tasks[guild_id] = None

async def start_inactivity_timer(ctx):
    await asyncio.sleep(INACTIVITY_TIMEOUT)
    vc = ctx.voice_client
    if vc and not vc.is_playing():
        await vc.disconnect()
        try:
            await ctx.send_followup("Saí do canal por inatividade.")
        except Exception:
            pass

def reset_inactivity_timer(ctx):
    guild_id = ctx.guild.id
    cancel_inactivity_task(guild_id)
    loop = asyncio.get_event_loop()
    inactivity_tasks[guild_id] = loop.create_task(start_inactivity_timer(ctx))

music_queues = {}

def get_queue(guild_id):
    if guild_id not in music_queues:
        music_queues[guild_id] = []
    return music_queues[guild_id]

async def play_next(ctx):
    queue = get_queue(ctx.guild.id)
    if queue:
        source = queue.pop(0)
        vc = ctx.voice_client or await ctx.author.voice.channel.connect()
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        coro = ctx.followup.send(f"Tocando: **{source.title}**")
        asyncio.create_task(coro)
        reset_inactivity_timer(ctx)
    else:
        vc = ctx.voice_client
        if vc:
            await vc.disconnect()

class SafeFFmpegPCMAudio(FFmpegPCMAudio):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not hasattr(self, "_process"):
            self._process = None

    def _kill_process(self):
        if self._process:
            try:
                super()._kill_process()
            except Exception:
                pass

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.title = data.get("title")

    @classmethod
    async def from_url(cls, url, *, loop):
        data = await asyncio.to_thread(ytdl.extract_info, url, download=False)
        info = data["entries"][0] if "entries" in data else data
        return cls(SafeFFmpegPCMAudio(info["url"], **FFMPEG_OPTS), data=info)


@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    print(f"Bot está em {len(bot.guilds)} servidores")
    
    commands = [cmd.name for cmd in bot.pending_application_commands]
    print(f"Comandos registrados: {commands}")
    
    if not commands:
        print("ERRO: Nenhum comando foi registrado!")
    else:
        print(f"✅ {len(commands)} comandos foram registrados e serão sincronizados automaticamente")

@bot.slash_command(description="Entra no canal de voz")
async def entrar(ctx):
    if ctx.author.voice:
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
            await ctx.respond("Entrei no canal!")
        else:
            await ctx.respond("Já estou no canal.")
    else:
        await ctx.respond("Você precisa estar em um canal de voz.")

@bot.slash_command(description="Sai do canal de voz")
async def sair(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.respond("Saí do canal.")
    else:
        await ctx.respond("Não estou em nenhum canal.")

@bot.slash_command(description="Toca música (com fila)")
async def play(ctx, query: str):
    if not ctx.author.voice:
        await ctx.respond("Você precisa estar em um canal de voz.")
        return

    await ctx.defer()
    vc = ctx.voice_client or await ctx.author.voice.channel.connect()

    try:
        source = await YTDLSource.from_url(query, loop=bot.loop)
        queue = get_queue(ctx.guild.id)
        if vc.is_playing() or queue:
            queue.append(source)
            await ctx.followup.send(f"Adicionado à fila: **{source.title}**")
        else:
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
            await ctx.followup.send(f"Tocando: **{source.title}**")
        reset_inactivity_timer(ctx)
    except Exception as e:
        await ctx.followup.send(f"Erro: {e}")

@bot.slash_command(description="Pausa a música")
async def pause(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.respond("Música pausada.")
        reset_inactivity_timer(ctx)
    else:
        await ctx.respond("Não estou tocando nada.")

@bot.slash_command(description="Continua a música pausada")
async def resume(ctx):
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.respond("Música retomada.")
        reset_inactivity_timer(ctx)
    else:
        await ctx.respond("Nada está pausado.")

@bot.slash_command(description="Para a música e limpa a fila")
async def stop(ctx):
    vc = ctx.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        music_queues[ctx.guild.id] = []
        await ctx.respond("Música parada e fila limpa.")
        reset_inactivity_timer(ctx)
    else:
        await ctx.respond("Não estou tocando nada.")

@bot.slash_command(description="Pula para a próxima música da fila")
async def skip(ctx):
    vc = ctx.voice_client
    queue = get_queue(ctx.guild.id)
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.respond("Pulando para a próxima música...")
        reset_inactivity_timer(ctx)
    else:
        await ctx.respond("Não estou tocando nada.")

bot.run(TOKEN)
