import asyncio

from discord import Intents
from discord.ext.commands import Bot
from ext import ExtCog


async def main() -> None:
    bot = Bot(command_prefix="!", intents=Intents.none())
    try:
        await bot.load_extension("ext")
        loaded = bot.get_cog("ExtCog")
        if loaded is None:
            raise RuntimeError("extension did not register ExtCog")
        print(f"same repr: {repr(type(loaded)) == repr(ExtCog)}")
        print(f"same class object: {type(loaded) is ExtCog}")
    finally:
        await bot.close()


asyncio.run(main())
