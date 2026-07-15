from discord.ext.commands import Bot, Cog


class ExtCog(Cog):
    pass


async def setup(bot: Bot) -> None:
    await bot.add_cog(ExtCog())
