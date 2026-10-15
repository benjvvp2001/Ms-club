from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.repositories.base import (
    ApplyResult,
    ClubSport,
    OutboxEvent,
    OutboxMessage,
    ReviewResult,
    StoredClub,
    StoredComunicado,
    StoredConvocatoria,
    StoredPlan,
    StoredPostulacion,
    StoredSuscripcion,
    StoredTransaccion,
)
from app.schemas.club import ClubFilters
from app.schemas.common import fold_name
from app.schemas.comunicado import (
    ESTADOS_POR_AUDIENCIA,
    ClubComunicadoFilters,
    ComunicadoFilters,
)
from app.schemas.convocatoria import ConvocatoriaFilters
from app.schemas.postulacion import CandidatoFilters, PostulacionFilters

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


# Effective state of a convocatoria, computed with the database clock.
_ESTADO = """
    CASE
        WHEN v.fecha_prueba <= now() THEN 'FINALIZADA'
        WHEN v.cerrada_en IS NOT NULL THEN 'CERRADA'
        ELSE 'ABIERTA'
    END
"""

def _convocatoria_select(active_clubs_only: bool = True) -> str:
    club_join = "JOIN club c ON c.id = v.club_id" + (" AND c.is_active" if active_clubs_only else "")
    return f"""
    SELECT v.id, v.club_id, c.nombre AS club_nombre, c.logo AS club_logo,
           d.id AS deporte_id, d.nombre AS deporte_nombre, d.descripcion AS deporte_descripcion,
           v.titulo, v.descripcion, v.categoria_edad, v.edad_minima, v.edad_maxima,
           v.fecha_prueba, v.requisitos, v.cupos, v.direccion, v.comuna, v.cerrada_en,
           v.fecha_creacion, {_ESTADO} AS estado,
           (SELECT count(*) FROM postulacion_convocatoria p
            WHERE p.convocatoria_id = v.id AND p.estado <> 'RECHAZADO') AS postulaciones_activas
    FROM convocatoria v
    {club_join}
    JOIN deporte d ON d.id = v.deporte_id
"""


_CONVOCATORIA_SELECT = _convocatoria_select()

_POSTULACION_COLUMNS = """
    p.id, p.convocatoria_id, p.usuario_id, p.estado, p.mensaje, p.antecedentes,
    p.perfil_deportivo, p.comentario_club, p.fecha_creacion, p.fecha_actualizacion
"""

_COMUNICADO_SELECT = """
    SELECT c.id, c.club_id, cl.nombre AS club_nombre, cl.logo AS club_logo, c.titulo,
           c.contenido, c.audiencia, c.prioridad, c.fecha_creacion,
           (SELECT count(*) FROM comunicado_destinatario d
            WHERE d.comunicado_id = c.id) AS destinatarios
    FROM comunicado_club c
    JOIN club cl ON cl.id = c.club_id
"""

_PLAN_ALIASES = """
    p.id AS plan_id, p.nombre AS plan_nombre, p.tipo AS plan_tipo,
    p.descripcion AS plan_descripcion, p.precio AS plan_precio,
    p.duracion_dias AS plan_duracion_dias, p.is_active AS plan_is_active
"""

_TRANSACCION_SELECT = f"""
    SELECT t.id, t.usuario_id, t.club_id, t.iniciada_por, t.buy_order, t.session_id,
           t.token_ws, t.amount, t.status, t.response_code, t.authorization_code,
           t.payment_type_code, t.card_last4, t.installments_number, t.transaction_date,
           t.fecha_creacion, t.fecha_actualizacion, {_PLAN_ALIASES}
    FROM transaccion_pago t
    JOIN plan_suscripcion p ON p.id = t.plan_id
"""

_SUSCRIPCION_SELECT = f"""
    SELECT s.id, s.usuario_id, s.club_id, s.fecha_inicio, s.fecha_fin,
           CASE WHEN s.fecha_fin > now() THEN 'ACTIVE' ELSE 'EXPIRED' END AS estado,
           {_PLAN_ALIASES}
    FROM suscripcion s
    JOIN plan_suscripcion p ON p.id = s.plan_id
"""

# Create or extend the buyer's subscription. One atomic statement, so two
# payments approved at the same moment cannot lose days. The partial unique
# indexes (usuario_id / club_id) make ON CONFLICT pick the buyer's row.
_SUSCRIPCION_UPSERT = """
    INSERT INTO suscripcion
        (usuario_id, club_id, plan_id, fecha_inicio, fecha_fin, ultima_transaccion_id)
    VALUES (%(usuario_id)s, %(club_id)s, %(plan_id)s, now(),
            now() + make_interval(days => %(dias)s), %(transaccion_id)s)
    ON CONFLICT ({column}) WHERE {column} IS NOT NULL DO UPDATE SET
        plan_id = EXCLUDED.plan_id,
        fecha_inicio = CASE WHEN suscripcion.fecha_fin > now()
                            THEN suscripcion.fecha_inicio ELSE now() END,
        fecha_fin = GREATEST(suscripcion.fecha_fin, now()) + make_interval(days => %(dias)s),
        ultima_transaccion_id = EXCLUDED.ultima_transaccion_id,
        fecha_actualizacion = now()
"""

# Read by the Ms_gateway notifications worker (004_revision_postulaciones).
_OUTBOX = sql.Identifier("clubs_api", "notification_outbox")

_CONVOCATORIA_CONDITIONS = f"""
    {_ESTADO} = %(estado)s
    AND (%(club_id)s::uuid IS NULL OR v.club_id = %(club_id)s)
    AND (%(deporte_id)s::int IS NULL OR v.deporte_id = %(deporte_id)s)
    AND (%(deporte)s::text IS NULL
         OR translate(lower(btrim(d.nombre)), 'áéíóúüñ', 'aeiouun') = %(deporte)s)
    AND (%(categoria)s::text IS NULL OR v.categoria_edad ILIKE %(categoria)s ESCAPE '\\')
    AND (%(edad)s::int IS NULL OR (
        (v.edad_minima IS NULL OR v.edad_minima <= %(edad)s)
        AND (v.edad_maxima IS NULL OR v.edad_maxima >= %(edad)s)
    ))
    AND (%(fecha_desde)s::date IS NULL
         OR (v.fecha_prueba AT TIME ZONE 'America/Santiago')::date >= %(fecha_desde)s)
    AND (%(fecha_hasta)s::date IS NULL
         OR (v.fecha_prueba AT TIME ZONE 'America/Santiago')::date <= %(fecha_hasta)s)
"""

_CONVOCATORIA_COLUMNS = frozenset({
    "deporte_id", "titulo", "descripcion", "categoria_edad", "edad_minima", "edad_maxima",
    "fecha_prueba", "requisitos", "cupos", "direccion", "comuna",
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


    # -- convocatorias (HU-32) --------------------------------------------------

    def create_convocatoria(self, club_id: UUID, values: dict[str, Any]) -> StoredConvocatoria:
        unknown = set(values) - _CONVOCATORIA_COLUMNS
        if unknown:
            raise ValueError(f"Columns not writable: {sorted(unknown)}")
        columns = {"club_id": club_id, **values}
        insert = sql.SQL("INSERT INTO convocatoria ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
        )
        with self._pool.connection() as conn:
            convocatoria_id = conn.execute(insert, columns).fetchone()[0]
        convocatoria = self.get_convocatoria(convocatoria_id)
        assert convocatoria is not None
        return convocatoria

    def get_convocatoria(
        self, convocatoria_id: UUID, *, include_inactive_clubs: bool = False
    ) -> StoredConvocatoria | None:
        select = _convocatoria_select(active_clubs_only=not include_inactive_clubs)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{select} WHERE v.id = %s", (convocatoria_id,))
            row = cur.fetchone()
        return _to_convocatoria(row) if row is not None else None

    def search_convocatorias(
        self, filters: ConvocatoriaFilters
    ) -> tuple[list[StoredConvocatoria], int]:
        params: dict[str, Any] = {
            "estado": filters.estado,
            "club_id": filters.club_id,
            "deporte_id": filters.deporte_id,
            "deporte": fold_name(filters.deporte) if filters.deporte else None,
            "categoria": _contains(filters.categoria),
            "edad": filters.edad,
            "fecha_desde": filters.fecha_desde,
            "fecha_hasta": filters.fecha_hasta,
            "limit": filters.page_size,
            "offset": (filters.page - 1) * filters.page_size,
        }
        order = "v.fecha_prueba DESC, v.id" if filters.estado == "FINALIZADA" else "v.fecha_prueba, v.id"
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT count(*) AS total
                FROM convocatoria v
                JOIN club c ON c.id = v.club_id AND c.is_active
                JOIN deporte d ON d.id = v.deporte_id
                WHERE {_CONVOCATORIA_CONDITIONS}
                """,
                params,
            )
            total = cur.fetchone()["total"]
            if total == 0:
                return [], 0
            cur.execute(
                f"""
                {_CONVOCATORIA_SELECT}
                WHERE {_CONVOCATORIA_CONDITIONS}
                ORDER BY {order}
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cur.fetchall()
        return [_to_convocatoria(row) for row in rows], total

    def close_convocatoria(self, convocatoria_id: UUID) -> StoredConvocatoria:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE convocatoria
                SET cerrada_en = now(), fecha_actualizacion = now()
                WHERE id = %s AND cerrada_en IS NULL
                """,
                (convocatoria_id,),
            )
        convocatoria = self.get_convocatoria(convocatoria_id)
        assert convocatoria is not None
        return convocatoria


    # -- postulaciones (HU-33) ----------------------------------------------------

    def create_postulacion(
        self, convocatoria_id: UUID, usuario_id: UUID, values: dict[str, Any]
    ) -> tuple[ApplyResult, StoredPostulacion | None]:
        with self._pool.connection() as conn, conn.transaction():
            # Lock the convocatoria: concurrent applications to it run one by one.
            row = conn.execute(
                f"""
                SELECT v.cupos, {_ESTADO} AS estado, c.is_active
                FROM convocatoria v
                JOIN club c ON c.id = v.club_id
                WHERE v.id = %s
                FOR UPDATE OF v
                """,
                (convocatoria_id,),
            ).fetchone()
            if row is None or not row[2]:
                return "no_encontrada", None
            cupos, estado, _ = row
            if estado != "ABIERTA":
                return "no_abierta", None
            if conn.execute(
                "SELECT 1 FROM postulacion_convocatoria WHERE convocatoria_id = %s AND usuario_id = %s",
                (convocatoria_id, usuario_id),
            ).fetchone():
                return "duplicada", None
            taken = conn.execute(
                """
                SELECT count(*) FROM postulacion_convocatoria
                WHERE convocatoria_id = %s AND estado <> 'RECHAZADO'
                """,
                (convocatoria_id,),
            ).fetchone()[0]
            if taken >= cupos:
                return "sin_cupos", None
            postulacion_id = conn.execute(
                """
                INSERT INTO postulacion_convocatoria
                    (convocatoria_id, usuario_id, mensaje, antecedentes, perfil_deportivo)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    convocatoria_id,
                    usuario_id,
                    values.get("mensaje"),
                    Jsonb(values.get("antecedentes", [])),
                    Jsonb(values["perfil_deportivo"]),
                ),
            ).fetchone()[0]
        postulaciones = self._load_postulaciones(
            f"SELECT {_POSTULACION_COLUMNS} FROM postulacion_convocatoria p WHERE p.id = %s",
            (postulacion_id,),
        )
        return "creada", postulaciones[0]

    def list_postulaciones(
        self, usuario_id: UUID, filters: PostulacionFilters
    ) -> tuple[list[StoredPostulacion], int]:
        params = {
            "usuario_id": usuario_id,
            "estado": filters.estado,
            "limit": filters.page_size,
            "offset": (filters.page - 1) * filters.page_size,
        }
        conditions = "p.usuario_id = %(usuario_id)s AND (%(estado)s::text IS NULL OR p.estado = %(estado)s)"
        with self._pool.connection() as conn:
            total = conn.execute(
                f"SELECT count(*) FROM postulacion_convocatoria p WHERE {conditions}", params
            ).fetchone()[0]
        if total == 0:
            return [], 0
        postulaciones = self._load_postulaciones(
            f"""
            SELECT {_POSTULACION_COLUMNS}
            FROM postulacion_convocatoria p
            WHERE {conditions}
            ORDER BY p.fecha_creacion DESC, p.id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return postulaciones, total

    # -- revisión de candidatos (HU-34) -----------------------------------------------

    def list_candidatos(
        self, convocatoria_id: UUID, filters: CandidatoFilters
    ) -> tuple[list[StoredPostulacion], int, dict[str, int]]:
        params = {
            "convocatoria_id": convocatoria_id,
            "estado": filters.estado,
            "limit": filters.page_size,
            "offset": (filters.page - 1) * filters.page_size,
        }
        with self._pool.connection() as conn:
            conteo = dict(
                conn.execute(
                    """
                    SELECT estado, count(*) FROM postulacion_convocatoria
                    WHERE convocatoria_id = %(convocatoria_id)s
                    GROUP BY estado
                    """,
                    params,
                ).fetchall()
            )
        total = conteo.get(filters.estado, 0) if filters.estado else sum(conteo.values())
        if total == 0:
            return [], 0, conteo
        candidatos = self._load_postulaciones(
            f"""
            SELECT {_POSTULACION_COLUMNS}
            FROM postulacion_convocatoria p
            WHERE p.convocatoria_id = %(convocatoria_id)s
              AND (%(estado)s::text IS NULL OR p.estado = %(estado)s)
            ORDER BY p.fecha_creacion, p.id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return candidatos, total, conteo

    def get_postulacion(self, postulacion_id: UUID) -> StoredPostulacion | None:
        postulaciones = self._load_postulaciones(
            f"SELECT {_POSTULACION_COLUMNS} FROM postulacion_convocatoria p WHERE p.id = %s",
            (postulacion_id,),
        )
        return postulaciones[0] if postulaciones else None

    def review_postulacion(
        self,
        postulacion_id: UUID,
        estado: str,
        *,
        comentario: str | None,
        set_comentario: bool,
        evento: OutboxEvent,
    ) -> tuple[ReviewResult, StoredPostulacion]:
        with self._pool.connection() as conn, conn.transaction():
            convocatoria_id = conn.execute(
                "SELECT convocatoria_id FROM postulacion_convocatoria WHERE id = %s",
                (postulacion_id,),
            ).fetchone()[0]
            # Same lock order as applying (convocatoria, then postulación): the
            # cupos check below cannot race with a new application.
            cupos = conn.execute(
                "SELECT cupos FROM convocatoria WHERE id = %s FOR UPDATE", (convocatoria_id,)
            ).fetchone()[0]
            anterior = conn.execute(
                "SELECT estado FROM postulacion_convocatoria WHERE id = %s FOR UPDATE",
                (postulacion_id,),
            ).fetchone()[0]
            sin_cupos = False
            if anterior == "RECHAZADO" and estado != "RECHAZADO":
                taken = conn.execute(
                    """
                    SELECT count(*) FROM postulacion_convocatoria
                    WHERE convocatoria_id = %s AND estado <> 'RECHAZADO'
                    """,
                    (convocatoria_id,),
                ).fetchone()[0]
                sin_cupos = taken >= cupos
            result: ReviewResult
            if sin_cupos:
                result = "sin_cupos"
            else:
                conn.execute(
                    """
                    UPDATE postulacion_convocatoria
                    SET estado = %s,
                        comentario_club = CASE WHEN %s THEN %s ELSE comentario_club END,
                        fecha_actualizacion = now()
                    WHERE id = %s
                    """,
                    (estado, set_comentario, comentario, postulacion_id),
                )
                result = "sin_cambios" if estado == anterior else "actualizada"
            if result == "actualizada":
                conn.execute(
                    sql.SQL(
                        "INSERT INTO {} (user_id, kind, title, body, data) VALUES (%s, %s, %s, %s, %s)"
                    ).format(_OUTBOX),
                    (evento.user_id, evento.kind, evento.title, evento.body, Jsonb(evento.data)),
                )
        postulacion = self.get_postulacion(postulacion_id)
        assert postulacion is not None
        return result, postulacion

    # -- comunicados de clubes (HU-38) ------------------------------------------------

    def create_comunicado(
        self, club_id: UUID, values: dict[str, Any], mensaje: OutboxMessage
    ) -> StoredComunicado:
        comunicado_id = values["id"]
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO comunicado_club (id, club_id, titulo, contenido, audiencia, prioridad)
                VALUES (%(id)s, %(club_id)s, %(titulo)s, %(contenido)s, %(audiencia)s, %(prioridad)s)
                """,
                {**values, "club_id": club_id},
            )
            usuario_ids = sorted(
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT p.usuario_id
                    FROM postulacion_convocatoria p
                    JOIN convocatoria v ON v.id = p.convocatoria_id
                    WHERE v.club_id = %s AND p.estado = ANY(%s)
                    """,
                    (club_id, list(ESTADOS_POR_AUDIENCIA[values["audiencia"]])),
                ).fetchall()
            )
            if usuario_ids:
                conn.execute(
                    """
                    INSERT INTO comunicado_destinatario (comunicado_id, usuario_id)
                    SELECT %s, unnest(%s::uuid[])
                    """,
                    (comunicado_id, usuario_ids),
                )
                conn.execute(
                    sql.SQL(
                        "INSERT INTO {} (user_id, kind, title, body, data) "
                        "SELECT unnest(%s::uuid[]), %s, %s, %s, %s"
                    ).format(_OUTBOX),
                    (usuario_ids, mensaje.kind, mensaje.title, mensaje.body, Jsonb(mensaje.data)),
                )
        comunicados = self._load_comunicados(f"{_COMUNICADO_SELECT} WHERE c.id = %s", (comunicado_id,))
        return comunicados[0]

    def list_comunicados_club(
        self, club_id: UUID, filters: ClubComunicadoFilters
    ) -> tuple[list[StoredComunicado], int]:
        params = {
            "club_id": club_id,
            "audiencia": filters.audiencia,
            "prioridad": filters.prioridad,
            "limit": filters.page_size,
            "offset": (filters.page - 1) * filters.page_size,
        }
        conditions = """
            c.club_id = %(club_id)s
            AND (%(audiencia)s::text IS NULL OR c.audiencia = %(audiencia)s)
            AND (%(prioridad)s::text IS NULL OR c.prioridad = %(prioridad)s)
        """
        return self._page_comunicados("comunicado_club c", conditions, params)

    def list_comunicados_usuario(
        self, usuario_id: UUID, filters: ComunicadoFilters
    ) -> tuple[list[StoredComunicado], int]:
        params = {
            "usuario_id": usuario_id,
            "club_id": filters.club_id,
            "prioridad": filters.prioridad,
            "limit": filters.page_size,
            "offset": (filters.page - 1) * filters.page_size,
        }
        conditions = """
            EXISTS (SELECT 1 FROM comunicado_destinatario me
                    WHERE me.comunicado_id = c.id AND me.usuario_id = %(usuario_id)s)
            AND (%(club_id)s::uuid IS NULL OR c.club_id = %(club_id)s)
            AND (%(prioridad)s::text IS NULL OR c.prioridad = %(prioridad)s)
        """
        return self._page_comunicados("comunicado_club c", conditions, params)

    def _page_comunicados(
        self, source: str, conditions: str, params: dict[str, Any]
    ) -> tuple[list[StoredComunicado], int]:
        with self._pool.connection() as conn:
            total = conn.execute(
                f"SELECT count(*) FROM {source} WHERE {conditions}", params
            ).fetchone()[0]
        if total == 0:
            return [], 0
        comunicados = self._load_comunicados(
            f"""
            {_COMUNICADO_SELECT}
            WHERE {conditions}
            ORDER BY c.fecha_creacion DESC, c.id
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
        )
        return comunicados, total

    def _load_comunicados(self, query: str, params: Any) -> list[StoredComunicado]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [StoredComunicado(**row) for row in cur.fetchall()]

    # -- pagos con Webpay Plus (HU-40) -----------------------------------------------

    def list_planes(self, tipo: str | None = None) -> list[StoredPlan]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, nombre, tipo, descripcion, precio, duracion_dias, is_active
                FROM plan_suscripcion
                WHERE is_active AND (%s::text IS NULL OR tipo = %s)
                ORDER BY precio, id
                """,
                (tipo, tipo),
            )
            return [StoredPlan(**row) for row in cur.fetchall()]

    def get_plan(self, plan_id: int) -> StoredPlan | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, nombre, tipo, descripcion, precio, duracion_dias, is_active
                FROM plan_suscripcion WHERE id = %s AND is_active
                """,
                (plan_id,),
            )
            row = cur.fetchone()
        return StoredPlan(**row) if row is not None else None

    def get_suscripcion(
        self, *, usuario_id: UUID | None = None, club_id: UUID | None = None
    ) -> StoredSuscripcion | None:
        if (usuario_id is None) == (club_id is None):
            raise ValueError("Give exactly one of usuario_id and club_id")
        column, value = ("s.usuario_id", usuario_id) if usuario_id else ("s.club_id", club_id)
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{_SUSCRIPCION_SELECT} WHERE {column} = %s", (value,))
            row = cur.fetchone()
        return _to_suscripcion(row) if row is not None else None

    def create_transaccion(self, values: dict[str, Any]) -> StoredTransaccion:
        with self._pool.connection() as conn:
            transaccion_id = conn.execute(
                """
                INSERT INTO transaccion_pago
                    (usuario_id, club_id, iniciada_por, plan_id, buy_order, session_id,
                     token_ws, amount)
                VALUES (%(usuario_id)s, %(club_id)s, %(iniciada_por)s, %(plan_id)s,
                        %(buy_order)s, %(session_id)s, %(token_ws)s, %(amount)s)
                RETURNING id
                """,
                {"usuario_id": None, "club_id": None, **values},
            ).fetchone()[0]
        transaccion = self._get_transaccion("t.id = %s", transaccion_id)
        assert transaccion is not None
        return transaccion

    def get_transaccion(
        self, *, token_ws: str | None = None, buy_order: str | None = None
    ) -> StoredTransaccion | None:
        if (token_ws is None) == (buy_order is None):
            raise ValueError("Give exactly one of token_ws and buy_order")
        if token_ws is not None:
            return self._get_transaccion("t.token_ws = %s", token_ws)
        return self._get_transaccion("t.buy_order = %s", buy_order)

    def _get_transaccion(self, condition: str, value: object) -> StoredTransaccion | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"{_TRANSACCION_SELECT} WHERE {condition}", (value,))
            row = cur.fetchone()
        return _to_transaccion(row) if row is not None else None

    def settle_transaccion(
        self, transaccion_id: UUID, status: str, resultado: dict[str, Any] | None
    ) -> tuple[StoredTransaccion, bool, StoredSuscripcion | None]:
        resultado = resultado or {}
        with self._pool.connection() as conn, conn.transaction():
            # Only a PENDING payment changes: a second confirmation of the same
            # token finds nothing here and cannot extend the subscription again.
            row = conn.execute(
                """
                UPDATE transaccion_pago
                SET status = %(status)s,
                    response_code = %(response_code)s,
                    authorization_code = %(authorization_code)s,
                    payment_type_code = %(payment_type_code)s,
                    card_last4 = %(card_last4)s,
                    installments_number = %(installments_number)s,
                    transaction_date = %(transaction_date)s,
                    fecha_actualizacion = now()
                WHERE id = %(id)s AND status = 'PENDING'
                RETURNING usuario_id, club_id, plan_id
                """,
                {
                    "response_code": None, "authorization_code": None,
                    "payment_type_code": None, "card_last4": None,
                    "installments_number": None, "transaction_date": None,
                    **resultado, "status": status, "id": transaccion_id,
                },
            ).fetchone()
            changed = row is not None
            if changed and status == "APPROVED":
                usuario_id, club_id, plan_id = row
                dias = conn.execute(
                    "SELECT duracion_dias FROM plan_suscripcion WHERE id = %s", (plan_id,)
                ).fetchone()[0]
                column = "usuario_id" if usuario_id is not None else "club_id"
                conn.execute(
                    _SUSCRIPCION_UPSERT.format(column=column),
                    {
                        "usuario_id": usuario_id, "club_id": club_id, "plan_id": plan_id,
                        "dias": dias, "transaccion_id": transaccion_id,
                    },
                )
        transaccion = self._get_transaccion("t.id = %s", transaccion_id)
        assert transaccion is not None
        suscripcion = None
        if transaccion.status == "APPROVED":
            suscripcion = self.get_suscripcion(
                usuario_id=transaccion.usuario_id, club_id=transaccion.club_id
            )
        return transaccion, changed, suscripcion

    def _load_postulaciones(self, query: str, params: Any) -> list[StoredPostulacion]:
        """Run a postulaciones query and attach each one's convocatoria."""
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            if not rows:
                return []
            cur.execute(
                f"{_convocatoria_select(active_clubs_only=False)} WHERE v.id = ANY(%s)",
                (list({row["convocatoria_id"] for row in rows}),),
            )
            convocatorias = {c.id: c for c in map(_to_convocatoria, cur.fetchall())}
        postulaciones = []
        for row in rows:
            convocatoria = convocatorias[row.pop("convocatoria_id")]
            postulaciones.append(StoredPostulacion(**row, convocatoria=convocatoria))
        return postulaciones


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


def _to_convocatoria(row: dict[str, Any]) -> StoredConvocatoria:
    deporte = ClubSport(
        id=row.pop("deporte_id"),
        nombre=row.pop("deporte_nombre"),
        descripcion=row.pop("deporte_descripcion"),
    )
    return StoredConvocatoria(**row, deporte=deporte)


def _split_plan(row: dict[str, Any]) -> StoredPlan:
    return StoredPlan(
        id=row.pop("plan_id"),
        nombre=row.pop("plan_nombre"),
        tipo=row.pop("plan_tipo"),
        descripcion=row.pop("plan_descripcion"),
        precio=row.pop("plan_precio"),
        duracion_dias=row.pop("plan_duracion_dias"),
        is_active=row.pop("plan_is_active"),
    )


def _to_transaccion(row: dict[str, Any]) -> StoredTransaccion:
    plan = _split_plan(row)
    return StoredTransaccion(**row, plan=plan)


def _to_suscripcion(row: dict[str, Any]) -> StoredSuscripcion:
    plan = _split_plan(row)
    return StoredSuscripcion(**row, plan=plan)
