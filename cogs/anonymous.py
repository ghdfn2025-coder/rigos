\
from __future__ import annotations

from datetime import datetime, timezone
import re

import discord
from discord import app_commands
from discord.ext import commands

from database import GuildSettings


URL_RE = re.compile(r"(https?://|www\.|discord\.gg/|discord(?:app)?\.com/invite/)", re.I)


def format_timestamp(value: datetime) -> str:
    unix = int(value.timestamp())
    return f"<t:{unix}:F> (<t:{unix}:R>)"


def truncate(text: str, limit: int = 1000) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def owner_only(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        raise app_commands.NoPrivateMessage()
    if interaction.guild.owner_id != interaction.user.id:
        raise app_commands.CheckFailure("이 명령어는 서버 소유자만 사용할 수 있습니다.")
    return True


class AnonymousCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.database  # type: ignore[attr-defined]

    async def _reply(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)

    async def _get_text_channel(
        self,
        guild: discord.Guild,
        channel_id: int | None,
    ) -> discord.TextChannel | None:
        if channel_id is None:
            return None
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    @app_commands.command(name="익명", description="리고스를 통해 익명 메시지를 전송합니다.")
    @app_commands.describe(
        내용="전송할 내용을 입력해 주세요. ///를 입력하면 줄바꿈됩니다."
    )
    @app_commands.guild_only()
    async def anonymous(
        self,
        interaction: discord.Interaction,
        내용: app_commands.Range[str, 2, 4000],
    ) -> None:
        assert interaction.guild is not None
        assert interaction.channel_id is not None

        await interaction.response.defer(ephemeral=True, thinking=True)

        settings = await self.db.get_settings(interaction.guild.id)
        post_channel = await self._get_text_channel(
            interaction.guild, settings.post_channel_id
        )
        log_channel = await self._get_text_channel(
            interaction.guild, settings.log_channel_id
        )

        if post_channel is None or log_channel is None:
            await interaction.followup.send(
                "리고스의 게시 채널 또는 로그 채널이 아직 설정되지 않았습니다.\n"
                "서버 소유자에게 문의해 주세요.",
                ephemeral=True,
            )
            return

        if (
            settings.command_channel_id is not None
            and interaction.channel_id != settings.command_channel_id
        ):
            command_channel = interaction.guild.get_channel(
                settings.command_channel_id
            )
            mention = (
                command_channel.mention
                if isinstance(command_channel, discord.abc.GuildChannel)
                else "설정된 명령 채널"
            )
            await interaction.followup.send(
                f"`/익명` 명령어는 {mention}에서만 사용할 수 있습니다.",
                ephemeral=True,
            )
            return

        original = 내용.strip()
        published = original.replace("///", "\n").strip()

        if len(published) < 2:
            await interaction.followup.send(
                "메시지는 2자 이상 입력해 주세요.",
                ephemeral=True,
            )
            return

        if len(published) > settings.max_length:
            await interaction.followup.send(
                f"메시지는 최대 {settings.max_length:,}자까지 입력할 수 있습니다.",
                ephemeral=True,
            )
            return

        if URL_RE.search(published):
            await interaction.followup.send(
                "외부 링크 또는 Discord 초대 링크가 포함된 메시지는 전송할 수 없습니다.",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        last_sent_at = await self.db.get_last_sent_at(
            interaction.guild.id, interaction.user.id
        )
        if last_sent_at is not None:
            elapsed = (now - last_sent_at).total_seconds()
            remaining = settings.cooldown_seconds - int(elapsed)
            if remaining > 0:
                await interaction.followup.send(
                    f"잠시 후 다시 이용해 주세요.\n"
                    f"다음 메시지는 {remaining}초 후 전송할 수 있습니다.",
                    ephemeral=True,
                )
                return

        author_name = str(interaction.user)
        record_id = await self.db.create_message(
            guild_id=interaction.guild.id,
            author_id=interaction.user.id,
            author_name=author_name,
            original_content=original,
            published_content=published,
            command_channel_id=interaction.channel_id,
            post_channel_id=post_channel.id,
        )

        try:
            public_message = await post_channel.send(
                published,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self.db.attach_public_message(record_id, public_message.id)

            log_embed = discord.Embed(
                title="✦ 리고스 기록",
                description=f"익명 메시지 `#{record_id}`가 전송되었습니다.",
                timestamp=now,
            )
            log_embed.add_field(
                name="작성자",
                value=(
                    f"{interaction.user.mention}\n"
                    f"표시 이름: `{discord.utils.escape_markdown(author_name)}`\n"
                    f"사용자 ID: `{interaction.user.id}`"
                ),
                inline=False,
            )
            log_embed.add_field(
                name="원문",
                value=truncate(discord.utils.escape_markdown(original)),
                inline=False,
            )
            log_embed.add_field(
                name="게시 내용",
                value=truncate(discord.utils.escape_markdown(published)),
                inline=False,
            )
            log_embed.add_field(
                name="채널",
                value=(
                    f"작성: <#{interaction.channel_id}>\n"
                    f"게시: {post_channel.mention}"
                ),
                inline=True,
            )
            log_embed.add_field(
                name="게시 메시지",
                value=f"[바로가기]({public_message.jump_url})",
                inline=True,
            )
            log_embed.set_footer(text=f"메시지 ID: {record_id}")

            await log_channel.send(
                embed=log_embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            await self.db.delete_record(record_id)
            raise

        await interaction.delete_original_response()

    anonymous_admin = app_commands.Group(
        name="익명관리",
        description="리고스의 설정과 기록을 관리합니다.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @anonymous_admin.command(name="설정", description="리고스의 채널과 기본값을 설정합니다.")
    @app_commands.describe(
        게시채널="익명 메시지가 게시될 채널",
        로그채널="작성자와 원문이 기록될 비공개 채널",
        명령채널="/익명 명령어를 사용할 채널",
        쿨타임="사용자별 전송 대기 시간(초)",
        최대글자수="한 메시지의 최대 글자 수",
    )
    @app_commands.check(owner_only)
    async def settings(
        self,
        interaction: discord.Interaction,
        게시채널: discord.TextChannel | None = None,
        로그채널: discord.TextChannel | None = None,
        명령채널: discord.TextChannel | None = None,
        쿨타임: app_commands.Range[int, 0, 86400] | None = None,
        최대글자수: app_commands.Range[int, 2, 2000] | None = None,
    ) -> None:
        assert interaction.guild is not None

        if all(
            value is None
            for value in (게시채널, 로그채널, 명령채널, 쿨타임, 최대글자수)
        ):
            await self._reply(
                interaction,
                "변경할 항목을 하나 이상 입력해 주세요.",
            )
            return

        updated = await self.db.update_settings(
            interaction.guild.id,
            post_channel_id=게시채널.id if 게시채널 else None,
            log_channel_id=로그채널.id if 로그채널 else None,
            command_channel_id=명령채널.id if 명령채널 else None,
            cooldown_seconds=쿨타임,
            max_length=최대글자수,
        )

        lines = ["✅ 리고스 설정을 저장했습니다."]
        if 게시채널:
            lines.append(f"게시 채널: {게시채널.mention}")
        if 로그채널:
            lines.append(f"로그 채널: {로그채널.mention}")
        if 명령채널:
            lines.append(f"명령 채널: {명령채널.mention}")
        if 쿨타임 is not None:
            lines.append(f"쿨타임: {쿨타임}초")
        if 최대글자수 is not None:
            lines.append(f"최대 글자 수: {최대글자수:,}자")

        if 로그채널:
            everyone = interaction.guild.default_role
            overwrite = 로그채널.overwrites_for(everyone)
            if overwrite.view_channel is not False:
                lines.append(
                    "\n⚠️ 로그 채널의 `@everyone` 채널 보기 권한을 거부로 설정해 주세요."
                )

        await self._reply(interaction, "\n".join(lines))

    @anonymous_admin.command(name="조회", description="메시지 ID로 익명 작성 기록을 조회합니다.")
    @app_commands.describe(메시지_id="로그에 표시된 숫자 메시지 ID")
    @app_commands.check(owner_only)
    async def lookup(
        self,
        interaction: discord.Interaction,
        메시지_id: app_commands.Range[int, 1],
    ) -> None:
        assert interaction.guild is not None

        row = await self.db.get_message(interaction.guild.id, 메시지_id)
        if row is None:
            await self._reply(interaction, "해당 메시지 ID의 기록을 찾을 수 없습니다.")
            return

        public_message_link = "게시 메시지 없음"
        channel = interaction.guild.get_channel(row["post_channel_id"])
        if isinstance(channel, discord.TextChannel) and row["public_message_id"]:
            public_message_link = (
                f"https://discord.com/channels/{interaction.guild.id}/"
                f"{row['post_channel_id']}/{row['public_message_id']}"
            )

        embed = discord.Embed(
            title=f"✦ 리고스 기록 #{row['id']}",
            timestamp=row["created_at"],
        )
        embed.add_field(
            name="작성자",
            value=(
                f"<@{row['author_id']}>\n"
                f"작성 당시 이름: `{discord.utils.escape_markdown(row['author_name'])}`\n"
                f"사용자 ID: `{row['author_id']}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="원문",
            value=truncate(discord.utils.escape_markdown(row["original_content"])),
            inline=False,
        )
        embed.add_field(
            name="게시 내용",
            value=truncate(discord.utils.escape_markdown(row["published_content"])),
            inline=False,
        )
        embed.add_field(
            name="작성 시각",
            value=format_timestamp(row["created_at"]),
            inline=False,
        )
        embed.add_field(
            name="게시 메시지",
            value=f"[바로가기]({public_message_link})"
            if public_message_link.startswith("http")
            else public_message_link,
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @anonymous_admin.command(name="상태", description="리고스의 현재 설정을 확인합니다.")
    @app_commands.check(owner_only)
    async def status(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        settings = await self.db.get_settings(interaction.guild.id)

        def channel_text(channel_id: int | None) -> str:
            return f"<#{channel_id}>" if channel_id else "설정되지 않음"

        embed = discord.Embed(title="✦ 리고스 설정 상태")
        embed.add_field(
            name="채널",
            value=(
                f"게시 채널: {channel_text(settings.post_channel_id)}\n"
                f"로그 채널: {channel_text(settings.log_channel_id)}\n"
                f"명령 채널: {channel_text(settings.command_channel_id)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="전송 설정",
            value=(
                f"쿨타임: `{settings.cooldown_seconds}초`\n"
                f"최대 글자 수: `{settings.max_length:,}자`\n"
                "외부 링크: `차단`\n"
                "모든 멘션: `비활성화`"
            ),
            inline=False,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @anonymous_admin.command(name="통계", description="리고스의 이용 통계를 확인합니다.")
    @app_commands.check(owner_only)
    async def statistics(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        stats = await self.db.get_statistics(interaction.guild.id)

        embed = discord.Embed(title="✦ 리고스 이용 통계")
        embed.add_field(name="오늘", value=f"{stats['today']:,}개")
        embed.add_field(name="이번 주", value=f"{stats['this_week']:,}개")
        embed.add_field(name="전체", value=f"{stats['total']:,}개")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @anonymous_admin.command(
        name="로그정리",
        description="지정한 기간보다 오래된 익명 기록을 삭제합니다.",
    )
    @app_commands.describe(보존일수="최근 몇 일의 기록을 남길지 입력해 주세요.")
    @app_commands.check(owner_only)
    async def purge_logs(
        self,
        interaction: discord.Interaction,
        보존일수: app_commands.Range[int, 1, 3650],
    ) -> None:
        assert interaction.guild is not None

        count = await self.db.count_old_records(
            interaction.guild.id, 보존일수
        )
        if count == 0:
            await self._reply(
                interaction,
                f"{보존일수}일보다 오래된 기록이 없습니다.",
            )
            return

        deleted = await self.db.purge_old_records(
            interaction.guild.id, 보존일수
        )
        await self._reply(
            interaction,
            f"✅ {보존일수}일보다 오래된 기록 {deleted:,}개를 삭제했습니다.\n"
            "삭제된 데이터베이스 기록은 복구할 수 없습니다.",
        )

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = str(error) or "이 명령어를 사용할 권한이 없습니다."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "이 명령어는 서버에서만 사용할 수 있습니다."
        else:
            message = "명령어 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
            raise error

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
