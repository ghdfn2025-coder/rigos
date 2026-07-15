\
from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.anonymous import AnonymousCog
from database import Database


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("rigos")


class RigosBot(commands.Bot):
    def __init__(self, database: Database, dev_guild_id: int | None) -> None:
        intents = discord.Intents.none()
        intents.guilds = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.database = database
        self.dev_guild_id = dev_guild_id

    async def setup_hook(self) -> None:
        await self.database.connect()
        await self.add_cog(AnonymousCog(self))

        if self.dev_guild_id:
            guild = discord.Object(id=self.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("개발 서버 %s에 명령어 %d개 동기화", self.dev_guild_id, len(synced))
        else:
            synced = await self.tree.sync()
            log.info("전역 명령어 %d개 동기화", len(synced))

    async def close(self) -> None:
        await self.database.close()
        await super().close()

    async def on_ready(self) -> None:
        if self.user:
            log.info("%s (%s) 로그인 완료", self.user, self.user.id)
            await self.change_presence(
                activity=discord.Game(name="/익명 | 개발: 모카")
            )


def parse_optional_int(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError("DEV_GUILD_ID는 숫자여야 합니다.") from exc


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    database_url = os.getenv("DATABASE_URL")
    dev_guild_id = parse_optional_int(os.getenv("DEV_GUILD_ID"))

    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경 변수가 없습니다.")
    if not database_url:
        raise RuntimeError("DATABASE_URL 환경 변수가 없습니다.")

    database = Database(database_url)
    bot = RigosBot(database, dev_guild_id)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
