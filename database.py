\
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg


@dataclass(slots=True)
class GuildSettings:
    guild_id: int
    post_channel_id: int | None
    log_channel_id: int | None
    command_channel_id: int | None
    cooldown_seconds: int
    max_length: int


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)
        await self._create_tables()

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("데이터베이스가 연결되지 않았습니다.")
        return self.pool

    async def _create_tables(self) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    post_channel_id BIGINT,
                    log_channel_id BIGINT,
                    command_channel_id BIGINT,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                    max_length INTEGER NOT NULL DEFAULT 1000,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS anonymous_messages (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    author_id BIGINT NOT NULL,
                    author_name TEXT NOT NULL,
                    original_content TEXT NOT NULL,
                    published_content TEXT NOT NULL,
                    command_channel_id BIGINT NOT NULL,
                    post_channel_id BIGINT NOT NULL,
                    public_message_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_anonymous_messages_guild_created
                ON anonymous_messages(guild_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_anonymous_messages_author_created
                ON anonymous_messages(guild_id, author_id, created_at DESC);
                """
            )

    async def get_settings(self, guild_id: int) -> GuildSettings:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM guild_settings WHERE guild_id = $1",
            guild_id,
        )
        if row is None:
            await pool.execute(
                "INSERT INTO guild_settings (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
                guild_id,
            )
            row = await pool.fetchrow(
                "SELECT * FROM guild_settings WHERE guild_id = $1",
                guild_id,
            )
        assert row is not None
        return GuildSettings(
            guild_id=row["guild_id"],
            post_channel_id=row["post_channel_id"],
            log_channel_id=row["log_channel_id"],
            command_channel_id=row["command_channel_id"],
            cooldown_seconds=row["cooldown_seconds"],
            max_length=row["max_length"],
        )

    async def update_settings(
        self,
        guild_id: int,
        *,
        post_channel_id: int | None = None,
        log_channel_id: int | None = None,
        command_channel_id: int | None = None,
        cooldown_seconds: int | None = None,
        max_length: int | None = None,
    ) -> GuildSettings:
        await self.get_settings(guild_id)
        pool = self._require_pool()

        await pool.execute(
            """
            UPDATE guild_settings
            SET
                post_channel_id = COALESCE($2, post_channel_id),
                log_channel_id = COALESCE($3, log_channel_id),
                command_channel_id = COALESCE($4, command_channel_id),
                cooldown_seconds = COALESCE($5, cooldown_seconds),
                max_length = COALESCE($6, max_length),
                updated_at = NOW()
            WHERE guild_id = $1
            """,
            guild_id,
            post_channel_id,
            log_channel_id,
            command_channel_id,
            cooldown_seconds,
            max_length,
        )
        return await self.get_settings(guild_id)

    async def create_message(
        self,
        *,
        guild_id: int,
        author_id: int,
        author_name: str,
        original_content: str,
        published_content: str,
        command_channel_id: int,
        post_channel_id: int,
    ) -> int:
        pool = self._require_pool()
        return await pool.fetchval(
            """
            INSERT INTO anonymous_messages (
                guild_id, author_id, author_name,
                original_content, published_content,
                command_channel_id, post_channel_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            guild_id,
            author_id,
            author_name,
            original_content,
            published_content,
            command_channel_id,
            post_channel_id,
        )

    async def attach_public_message(self, record_id: int, message_id: int) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            UPDATE anonymous_messages
            SET public_message_id = $2
            WHERE id = $1
            """,
            record_id,
            message_id,
        )

    async def delete_record(self, record_id: int) -> None:
        pool = self._require_pool()
        await pool.execute(
            "DELETE FROM anonymous_messages WHERE id = $1",
            record_id,
        )

    async def get_message(self, guild_id: int, record_id: int) -> asyncpg.Record | None:
        pool = self._require_pool()
        return await pool.fetchrow(
            """
            SELECT *
            FROM anonymous_messages
            WHERE guild_id = $1 AND id = $2
            """,
            guild_id,
            record_id,
        )

    async def get_last_sent_at(self, guild_id: int, author_id: int) -> datetime | None:
        pool = self._require_pool()
        return await pool.fetchval(
            """
            SELECT created_at
            FROM anonymous_messages
            WHERE guild_id = $1 AND author_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            guild_id,
            author_id,
        )

    async def get_statistics(self, guild_id: int) -> dict[str, int]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE created_at >= date_trunc('day', NOW())
                ) AS today,
                COUNT(*) FILTER (
                    WHERE created_at >= date_trunc('week', NOW())
                ) AS this_week,
                COUNT(*) AS total
            FROM anonymous_messages
            WHERE guild_id = $1
            """,
            guild_id,
        )
        assert row is not None
        return {
            "today": int(row["today"]),
            "this_week": int(row["this_week"]),
            "total": int(row["total"]),
        }

    async def count_old_records(self, guild_id: int, days: int) -> int:
        pool = self._require_pool()
        return int(
            await pool.fetchval(
                """
                SELECT COUNT(*)
                FROM anonymous_messages
                WHERE guild_id = $1
                  AND created_at < NOW() - make_interval(days => $2)
                """,
                guild_id,
                days,
            )
        )

    async def purge_old_records(self, guild_id: int, days: int) -> int:
        pool = self._require_pool()
        result = await pool.execute(
            """
            DELETE FROM anonymous_messages
            WHERE guild_id = $1
              AND created_at < NOW() - make_interval(days => $2)
            """,
            guild_id,
            days,
        )
        return int(result.split()[-1])
