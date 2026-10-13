from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.repositories.base import ClubSport, StoredClub
from app.schemas.club import ClubFilters, fold_name

_CLUB_COLUMNS = """
    c.id, c.usuario_id, c.nombre, c.descripcion, c.historia, c.email_contacto, c.telefono,
    c.sitio_web, c.direccion, c.comuna, c.provincia,
    c.latitud::float8 AS latitud, c.longitud::float8 AS longitud, c.logo, c.portada,
    c.redes_sociales, c.estado_verificacion, c.is_active, c.fecha_creacion,
    c.fecha_actualizacion,
    COALESCE((
        SELECT json_agg(
            json_build_object('id', d.id, 'nombre', d.nombre, 'descripcion', d.descripcion)
            ORDER BY lower(d.nombre), d.id
        )
        FROM club_deporte cd
        JOIN deporte d ON d.id = cd.deporte_id
        WHERE cd.club_id = c.id AND d.is_active
    ), '[]'::json) AS deportes
"""

# Every filter is optional: a NULL parameter disables its condition.
_SEARCH_CONDITIONS = """
    c.is_active
    AND (%(nombre)s::text IS NULL OR c.nombre ILIKE %(nombre)s ESCAPE '\\')
    AND (%(zona)s::text IS NULL
         OR c.direccion ILIKE %(zona)s ESCAPE '\\'
         OR c.comuna ILIKE %(zona)s ESCAPE '\\'
         OR c.provincia ILIKE %(zona)s ESCAPE '\\')
    AND (%(deporte_id)s::int IS NULL OR EXISTS (
        SELECT 1 FROM club_deporte cd JOIN deporte d ON d.id = cd.deporte_id
        WHERE cd.club_id = c.id AND d.is_active AND d.id = %(deporte_id)s
    ))
    AND (%(deporte)s::text IS NULL OR EXISTS (
        SELECT 1 FROM club_deporte cd JOIN deporte d ON d.id = cd.deporte_id
        WHERE cd.club_id = c.id AND d.is_active
          AND translate(lower(btrim(d.nombre)), 'áéíóúüñ', 'aeiouun') = %(deporte)s
    ))
"""

# Columns the API may write; values never reach SQL text, only placeholders.
_WRITABLE_COLUMNS = frozenset({
    "nombre", "descripcion", "historia", "email_contacto", "telefono", "sitio_web",
    "direccion", "comuna", "provincia", "latitud", "longitud", "logo", "portada",
    "redes_sociales",
})


class PostgresClubRepository:
    """PostgreSQL-backed repository over the sportmatch_clubs tables (club,
    deporte, club_deporte)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def close(self) -> None:
        self._pool.close()

    # -- directory ----------------------------------------------------------

    def search_clubs(self, filters: ClubFilters) -> tuple[list[StoredClub], int]:
        params: dict[str, Any] = {
            "nombre": _contains(filters.nombre),
            "zona": _contains(filters.zona),
            "deporte_id": filters.deporte_id,
            "deporte": fold_name(filters.deporte) if filters.deporte else None,
            "limit": filters.page_size,
            "offset": (filters.page - 1) * filters.page_size,
        }
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT count(*) AS total FROM club c WHERE {_SEARCH_CONDITIONS}", params)
            total = cur.fetchone()["total"]
            if total == 0:
                return [], 0
            cur.execute(
                f"""
                SELECT {_CLUB_COLUMNS}
                FROM club c
                WHERE {_SEARCH_CONDITIONS}
                ORDER BY lower(c.nombre), c.id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cur.fetchall()
        return [_to_club(row) for row in rows], total

    def get_club(self, club_id: UUID, *, include_inactive: bool = False) -> StoredClub | None:
        condition = "c.id = %s" if include_inactive else "c.id = %s AND c.is_active"
        return self._get_one(condition, club_id)

    def get_club_by_owner(self, usuario_id: UUID) -> StoredClub | None:
        return self._get_one("c.usuario_id = %s", usuario_id)

    def _get_one(self, condition: str, value: object) -> StoredClub | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_CLUB_COLUMNS} FROM club c WHERE {condition} "
                "ORDER BY c.fecha_creacion LIMIT 1",
                (value,),
            )
            row = cur.fetchone()
        return _to_club(row) if row is not None else None

    # -- club administration (HU-31) ------------------------------------------

    def create_club(
        self, usuario_id: UUID, values: dict[str, Any], deporte_ids: list[int] | None = None
    ) -> StoredClub | None:
        columns = {"usuario_id": usuario_id, **_writable(values)}
        insert = sql.SQL("INSERT INTO club ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
        )
        with self._pool.connection() as conn, conn.transaction():
            # Serializes concurrent creations by the same account, since the
            # table has no unique constraint on usuario_id.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"club-owner:{usuario_id}",),
            )
            if conn.execute("SELECT 1 FROM club WHERE usuario_id = %s", (usuario_id,)).fetchone():
                return None
            club_id = conn.execute(insert, _adapt(columns)).fetchone()[0]
            if deporte_ids:
                _replace_sports(conn, club_id, deporte_ids)
        return self.get_club(club_id, include_inactive=True)

    def update_club(
        self, club_id: UUID, changes: dict[str, Any], deporte_ids: list[int] | None = None
    ) -> StoredClub:
        columns = _writable(changes) if changes else {}
        update = sql.SQL("UPDATE club SET {} WHERE id = {}").format(
            sql.SQL(", ").join([
                *(
                    sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column))
                    for column in columns
                ),
                sql.SQL("fecha_actualizacion = now()"),
            ]),
            sql.Placeholder("club_id"),
        )
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(update, {**_adapt(columns), "club_id": club_id})
            if deporte_ids is not None:
                _replace_sports(conn, club_id, deporte_ids)
        club = self.get_club(club_id, include_inactive=True)
        assert club is not None
        return club

    def list_sports(self) -> list[ClubSport]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, nombre, descripcion FROM deporte WHERE is_active ORDER BY lower(nombre), id"
            )
            return [ClubSport(**row) for row in cur.fetchall()]

    def get_sport(self, deporte_id: int) -> ClubSport | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, nombre, descripcion FROM deporte WHERE id = %s AND is_active",
                (deporte_id,),
            )
            row = cur.fetchone()
        return ClubSport(**row) if row is not None else None

    def add_sport(self, club_id: UUID, deporte_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO club_deporte (club_id, deporte_id) VALUES (%s, %s)
                ON CONFLICT (club_id, deporte_id) DO NOTHING
                """,
                (club_id, deporte_id),
            )

    def remove_sport(self, club_id: UUID, deporte_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM club_deporte WHERE club_id = %s AND deporte_id = %s",
                (club_id, deporte_id),
            )


def _replace_sports(conn: Any, club_id: UUID, deporte_ids: list[int]) -> None:
    """Make the club's active sports exactly deporte_ids. Links to sports that
    are inactive in the catalog are kept: the representative cannot see them."""
    conn.execute(
        """
        DELETE FROM club_deporte cd
        USING deporte d
        WHERE d.id = cd.deporte_id AND d.is_active
          AND cd.club_id = %s AND cd.deporte_id <> ALL(%s)
        """,
        (club_id, deporte_ids),
    )
    conn.execute(
        """
        INSERT INTO club_deporte (club_id, deporte_id)
        SELECT %s, unnest(%s::int[])
        ON CONFLICT (club_id, deporte_id) DO NOTHING
        """,
        (club_id, deporte_ids),
    )


def _contains(text: str | None) -> str | None:
    """ILIKE pattern for a partial match; %, _ and \\ typed by the user match literally."""
    if not text:
        return None
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _writable(values: dict[str, Any]) -> dict[str, Any]:
    unknown = set(values) - _WRITABLE_COLUMNS
    if unknown:
        raise ValueError(f"Columns not writable: {sorted(unknown)}")
    if not values:
        raise ValueError("Nothing to write")
    return values


def _adapt(values: dict[str, Any]) -> dict[str, Any]:
    return {
        column: Jsonb(value) if column == "redes_sociales" else value
        for column, value in values.items()
    }


def _to_club(row: dict[str, Any]) -> StoredClub:
    deportes = [ClubSport(**item) for item in row.pop("deportes")]
    return StoredClub(**row, deportes=deportes)
