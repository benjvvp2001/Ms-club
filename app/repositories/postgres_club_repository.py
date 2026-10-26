from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import errors, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.repositories.base import (
    AccesoDeportista,
    AccesoHilo,
    AgregarMiembroResult,
    ApplyResult,
    AsignarResult,
    CanalEliminado,
    CanalInactividad,
    ClaimResult,
    ClubSport,
    CreateClubResult,
    CrearCanalResult,
    DecisionInactividadResult,
    HiloEliminado,
    OutboxEvent,
    OutboxMessage,
    ReviewResult,
    StoredAdmin,
    StoredAsignacion,
    StoredCanal,
    StoredClub,
    StoredComunicado,
    StoredConvocatoria,
    StoredHilo,
    StoredMensaje,
    StoredMensajeCanal,
    StoredMiembroCanal,
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
    c.id, c.usuario_id, c.rut, c.nombre, c.descripcion, c.historia, c.email_contacto, c.telefono,
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
           v.fecha_creacion, v.chat_postulantes_habilitado, {_ESTADO} AS estado,
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

_ADMIN_COLUMNS = "club_id, usuario_id, rol, nombre, agregado_por, fecha_alta"

_ASIGNACION_COLUMNS = """
    id, club_id, email, asignada_por, fecha_creacion, aceptada_por, aceptada_en, revocada_en
"""

# Neither accepted nor revoked (the partial indexes of 009 use the same condition).
_ASIGNACION_PENDIENTE = "aceptada_en IS NULL AND revocada_en IS NULL"
_ASIGNACION_S_PENDIENTE = "s.aceptada_en IS NULL AND s.revocada_en IS NULL"

_MENSAJE_COLUMNS = "id, hilo_id, autor_id, de, texto, client_message_id, enviado_en"


def _postulante_activo(club: str, usuario: str) -> str:
    """SQL condition (G3): usuario has a postulación other than RECHAZADO to a
    convocatoria of club. club and usuario are SQL expressions."""
    return f"""EXISTS (
        SELECT 1 FROM postulacion_convocatoria pa
        JOIN convocatoria va ON va.id = pa.convocatoria_id
        WHERE va.club_id = {club} AND pa.usuario_id = {usuario} AND pa.estado <> 'RECHAZADO')"""


# "Nombre A." of a postulación p as the club sees it (its perfil_deportivo);
# the mock repository's _nombre_perfil is its twin.
_NOMBRE_PERFIL = r"""COALESCE(NULLIF(btrim(left(btrim(regexp_replace(
    concat_ws(' ', p.perfil_deportivo->>'nombre', p.perfil_deportivo->>'apellido_inicial'),
    '\s+', ' ', 'g')), 100)), ''), 'Deportista')"""

# Active postulantes of the club %(club_id)s with the name of their latest
# active postulación (one row per account).
_POSTULANTES_ACTIVOS = f"""
    SELECT DISTINCT ON (p.usuario_id) p.usuario_id, {_NOMBRE_PERFIL} AS nombre
    FROM postulacion_convocatoria p
    JOIN convocatoria v ON v.id = p.convocatoria_id
    WHERE v.club_id = %(club_id)s AND p.estado <> 'RECHAZADO'
"""
_POSTULANTES_ORDER = "ORDER BY p.usuario_id, p.fecha_creacion DESC, p.id DESC"

# A thread with its club, its last message (ultimo_* columns) and the unread
# messages of each side: those written by the other side after its read mark.
_HILO_SELECT = """
    SELECT h.id, h.club_id, c.nombre AS club_nombre, c.logo AS club_logo, h.deportista_id,
           h.deportista_nombre, h.leido_deportista_id, h.leido_club_id, h.fecha_creacion,
           u.id AS ultimo_id, u.autor_id AS ultimo_autor_id, u.de AS ultimo_de,
           u.texto AS ultimo_texto, u.client_message_id AS ultimo_client_message_id,
           u.enviado_en AS ultimo_enviado_en,
           (SELECT count(*) FROM consulta_mensaje m
            WHERE m.hilo_id = h.id AND m.de = 'club'
              AND m.id > h.leido_deportista_id) AS no_leidos_deportista,
           (SELECT count(*) FROM consulta_mensaje m
            WHERE m.hilo_id = h.id AND m.de = 'deportista'
              AND m.id > h.leido_club_id) AS no_leidos_club
    FROM consulta_hilo h
    JOIN club c ON c.id = h.club_id
    LEFT JOIN LATERAL (
        SELECT m.id, m.autor_id, m.de, m.texto, m.client_message_id, m.enviado_en
        FROM consulta_mensaje m
        WHERE m.hilo_id = h.id
        ORDER BY m.id DESC
        LIMIT 1
    ) u ON true
"""

_HILO_ORDER = "ORDER BY u.id DESC NULLS LAST, h.fecha_creacion DESC, h.id"

# Read mark of each side, by the side that reads.
_LEIDO_COLUMN = {"deportista": "leido_deportista_id", "club": "leido_club_id"}

# Whether usuario_id may read the club's thread hilo_id. The club may be inactive.
# A thread whose athlete is no longer an active postulante is not shown (G3).
_ACCESO_HILO_CLUB = f"""
    SELECT
        EXISTS (SELECT 1 FROM club WHERE id = %(club_id)s) AS acceso_club,
        EXISTS (SELECT 1 FROM club_administrador
                WHERE club_id = %(club_id)s AND usuario_id = %(usuario_id)s) AS acceso_admin,
        EXISTS (SELECT 1 FROM consulta_hilo hh
                WHERE hh.id = %(hilo_id)s AND hh.club_id = %(club_id)s
                  AND {_postulante_activo('hh.club_id', 'hh.deportista_id')}) AS acceso_hilo
"""
_HILO_POSTULANTE = _postulante_activo("h.club_id", "h.deportista_id")

_CONVOCATORIA_COLUMNS = frozenset({
    "deporte_id", "titulo", "descripcion", "categoria_edad", "edad_minima", "edad_maxima",
    "fecha_prueba", "requisitos", "cupos", "direccion", "comuna", "chat_postulantes_habilitado",
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

    def get_club_by_admin(self, usuario_id: UUID) -> StoredClub | None:
        return self._get_one(
            "c.id = (SELECT a.club_id FROM club_administrador a WHERE a.usuario_id = %s)",
            usuario_id,
        )

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
        self,
        usuario_id: UUID,
        rut: str,
        nombre_admin: str,
        values: dict[str, Any],
        deporte_ids: list[int] | None = None,
    ) -> tuple[CreateClubResult, StoredClub | None]:
        # usuario_id stays as the club's creator (historical data).
        columns = {"usuario_id": usuario_id, "rut": rut, **_writable(values)}
        insert = sql.SQL("INSERT INTO club ({}) VALUES ({}) RETURNING id").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
        )
        try:
            with self._pool.connection() as conn, conn.transaction():
                _lock_account(conn, usuario_id)
                if conn.execute(
                    "SELECT 1 FROM club_administrador WHERE usuario_id = %s", (usuario_id,)
                ).fetchone():
                    return "ya_administra", None
                if conn.execute("SELECT 1 FROM club WHERE rut = %s", (rut,)).fetchone():
                    return "rut_duplicado", None
                club_id = conn.execute(insert, _adapt(columns)).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO club_administrador (club_id, usuario_id, rol, nombre)
                    VALUES (%s, %s, 'PRINCIPAL', %s)
                    """,
                    (club_id, usuario_id, nombre_admin),
                )
                if deporte_ids:
                    _replace_sports(conn, club_id, deporte_ids)
        except errors.UniqueViolation as error:
            # Another club took the RUT between the check and the insert.
            if error.diag.constraint_name == "club_rut_uq":
                return "rut_duplicado", None
            if error.diag.constraint_name == "club_administrador_usuario_uq":
                return "ya_administra", None
            raise
        return "creado", self.get_club(club_id, include_inactive=True)

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

    # -- administradores y asignaciones por correo ----------------------------------

    def get_admin(self, club_id: UUID, usuario_id: UUID) -> StoredAdmin | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_ADMIN_COLUMNS} FROM club_administrador "
                "WHERE club_id = %s AND usuario_id = %s",
                (club_id, usuario_id),
            )
            row = cur.fetchone()
        return StoredAdmin(**row) if row is not None else None

    def list_admins(self, club_id: UUID) -> list[StoredAdmin]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_ADMIN_COLUMNS} FROM club_administrador
                WHERE club_id = %s
                ORDER BY rol <> 'PRINCIPAL', fecha_alta, usuario_id
                """,
                (club_id,),
            )
            return [StoredAdmin(**row) for row in cur.fetchall()]

    def remove_admin(self, club_id: UUID, usuario_id: UUID) -> bool:
        with self._pool.connection() as conn:
            deleted = conn.execute(
                """
                DELETE FROM club_administrador
                WHERE club_id = %s AND usuario_id = %s AND rol = 'ADMIN'
                """,
                (club_id, usuario_id),
            )
            return deleted.rowcount > 0

    def create_asignacion(
        self, club_id: UUID, email: str, asignada_por: UUID, max_admins: int
    ) -> tuple[AsignarResult, StoredAsignacion | None]:
        try:
            with self._pool.connection() as conn, conn.transaction():
                # Serializes the count with other assignments and activations of the club.
                _lock_club_admins(conn, club_id)
                estado = conn.execute(
                    f"""
                    SELECT
                        EXISTS (SELECT 1 FROM club_admin_asignacion
                                WHERE club_id = %(club_id)s AND email = %(email)s
                                  AND {_ASIGNACION_PENDIENTE}),
                        EXISTS (SELECT 1 FROM club_admin_asignacion s
                                JOIN club_administrador a
                                  ON a.club_id = s.club_id AND a.usuario_id = s.aceptada_por
                                WHERE s.club_id = %(club_id)s AND s.email = %(email)s),
                        (SELECT count(*) FROM club_administrador WHERE club_id = %(club_id)s)
                        + (SELECT count(*) FROM club_admin_asignacion
                           WHERE club_id = %(club_id)s AND {_ASIGNACION_PENDIENTE})
                    """,
                    {"club_id": club_id, "email": email},
                ).fetchone()
                pendiente, ya_admin, ocupados = estado
                if pendiente:
                    return "duplicada", None
                if ya_admin:
                    return "ya_administra", None
                if ocupados >= max_admins:
                    return "sin_cupo", None
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        f"""
                        INSERT INTO club_admin_asignacion (club_id, email, asignada_por)
                        VALUES (%s, %s, %s)
                        RETURNING {_ASIGNACION_COLUMNS}
                        """,
                        (club_id, email, asignada_por),
                    )
                    return "creada", StoredAsignacion(**cur.fetchone())
        except errors.UniqueViolation as error:
            # Backstop of the check above (club_admin_asignacion_pendiente_uq).
            if error.diag.constraint_name == "club_admin_asignacion_pendiente_uq":
                return "duplicada", None
            raise

    def list_asignaciones(self, club_id: UUID) -> list[StoredAsignacion]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_ASIGNACION_COLUMNS} FROM club_admin_asignacion
                WHERE club_id = %s AND {_ASIGNACION_PENDIENTE}
                ORDER BY fecha_creacion DESC, id DESC
                """,
                (club_id,),
            )
            return [StoredAsignacion(**row) for row in cur.fetchall()]

    def revoke_asignacion(self, club_id: UUID, asignacion_id: UUID) -> bool:
        with self._pool.connection() as conn:
            updated = conn.execute(
                f"""
                UPDATE club_admin_asignacion SET revocada_en = now()
                WHERE id = %s AND club_id = %s AND {_ASIGNACION_PENDIENTE}
                """,
                (asignacion_id, club_id),
            )
            return updated.rowcount > 0

    def has_asignacion_pendiente(self, email: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""
                SELECT EXISTS (
                    SELECT 1 FROM club_admin_asignacion s JOIN club c ON c.id = s.club_id
                    WHERE s.email = %s AND {_ASIGNACION_S_PENDIENTE}
                      AND c.is_active
                )
                """,
                (email,),
            ).fetchone()
        return bool(row[0])

    def claim_asignacion(
        self, email: str, usuario_id: UUID, nombre: str, max_admins: int
    ) -> ClaimResult:
        with self._pool.connection() as conn, conn.transaction():
            # Same lock as creating a club: an account cannot join two clubs at once.
            _lock_account(conn, usuario_id)
            if conn.execute(
                "SELECT 1 FROM club_administrador WHERE usuario_id = %s", (usuario_id,)
            ).fetchone():
                return "ya_administra"
            # FOR UPDATE: a revocation (or another activation) waits for this one.
            candidatas = conn.execute(
                f"""
                SELECT s.id, s.club_id, s.asignada_por
                FROM club_admin_asignacion s
                JOIN club c ON c.id = s.club_id
                WHERE s.email = %s AND {_ASIGNACION_S_PENDIENTE}
                  AND c.is_active
                ORDER BY s.fecha_creacion, s.id
                FOR UPDATE OF s
                """,
                (email,),
            ).fetchall()
            for asignacion_id, club_id, asignada_por in candidatas:
                _lock_club_admins(conn, club_id)
                admins = conn.execute(
                    "SELECT count(*) FROM club_administrador WHERE club_id = %s", (club_id,)
                ).fetchone()[0]
                if admins >= max_admins:
                    # Only when CLUB_MAX_ADMINS was lowered: try the next club.
                    continue
                conn.execute(
                    """
                    UPDATE club_admin_asignacion SET aceptada_por = %s, aceptada_en = now()
                    WHERE id = %s
                    """,
                    (usuario_id, asignacion_id),
                )
                conn.execute(
                    """
                    INSERT INTO club_administrador (club_id, usuario_id, rol, nombre, agregado_por)
                    VALUES (%s, %s, 'ADMIN', %s, %s)
                    """,
                    (club_id, usuario_id, nombre, asignada_por),
                )
                return "aceptada"
        return "sin_asignacion"

    # -- consultas deportista <-> club (HU-51) ----------------------------------------

    def get_hilo(self, hilo_id: UUID) -> StoredHilo | None:
        hilos = self._load_hilos(f"{_HILO_SELECT} WHERE h.id = %s", (hilo_id,))
        return hilos[0] if hilos else None

    def get_hilo_deportista(self, club_id: UUID, deportista_id: UUID) -> StoredHilo | None:
        hilos = self._load_hilos(
            f"{_HILO_SELECT} WHERE h.club_id = %s AND h.deportista_id = %s",
            (club_id, deportista_id),
        )
        return hilos[0] if hilos else None

    def ensure_hilo(
        self, club_id: UUID, deportista_id: UUID, deportista_nombre: str
    ) -> StoredHilo | None:
        params = {"club_id": club_id, "usuario_id": deportista_id, "nombre": deportista_nombre}
        with self._pool.connection() as conn, conn.transaction():
            # Serialized with a rejection of the same person in the club.
            _lock_postulante(conn, club_id, deportista_id)
            activo = conn.execute(
                f"SELECT {_postulante_activo('%(club_id)s', '%(usuario_id)s')}", params
            ).fetchone()[0]
            if activo:
                conn.execute(
                    """
                    INSERT INTO consulta_hilo (club_id, deportista_id, deportista_nombre)
                    VALUES (%(club_id)s, %(usuario_id)s, %(nombre)s)
                    ON CONFLICT (club_id, deportista_id) DO NOTHING
                    """,
                    params,
                )
        if not activo:
            return None
        return self.get_hilo_deportista(club_id, deportista_id)

    def es_postulante_activo(self, club_id: UUID, usuario_id: UUID) -> bool:
        with self._pool.connection() as conn:
            return bool(conn.execute(
                f"SELECT {_postulante_activo('%(club_id)s', '%(usuario_id)s')}",
                {"club_id": club_id, "usuario_id": usuario_id},
            ).fetchone()[0])

    def nombre_postulante(self, club_id: UUID, usuario_id: UUID) -> str | None:
        with self._pool.connection() as conn:
            return _nombre_postulante(conn, club_id, usuario_id)

    def list_hilos_deportista(self, deportista_id: UUID) -> list[StoredHilo]:
        return self._load_hilos(
            f"{_HILO_SELECT} WHERE h.deportista_id = %s AND c.is_active AND {_HILO_POSTULANTE} "
            f"{_HILO_ORDER}",
            (deportista_id,),
        )

    def list_hilos_club(self, club_id: UUID) -> list[StoredHilo]:
        return self._load_hilos(
            f"{_HILO_SELECT} WHERE h.club_id = %s AND {_HILO_POSTULANTE} {_HILO_ORDER}", (club_id,)
        )

    def list_mensajes(
        self, hilo_id: UUID, *, after_id: int | None, before_id: int | None, limit: int
    ) -> list[StoredMensaje]:
        params = {"hilo_id": hilo_id, "after_id": after_id, "before_id": before_id, "limit": limit}
        query = f"SELECT * FROM ({_pagina_mensajes('%(hilo_id)s', after_id)}) m ORDER BY m.id"
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [StoredMensaje(**row) for row in cur.fetchall()]

    def add_mensaje(
        self,
        hilo_id: UUID,
        autor_id: UUID,
        de: str,
        texto: str,
        client_message_id: UUID,
        aviso: OutboxMessage,
    ) -> tuple[StoredMensaje, bool]:
        try:
            return self._add_mensaje(hilo_id, autor_id, de, texto, client_message_id, aviso)
        except errors.ForeignKeyViolation as error:
            # The thread was deleted (rejected postulación) before the insert.
            raise HiloEliminado(hilo_id) from error

    def _add_mensaje(
        self,
        hilo_id: UUID,
        autor_id: UUID,
        de: str,
        texto: str,
        client_message_id: UUID,
        aviso: OutboxMessage,
    ) -> tuple[StoredMensaje, bool]:
        with self._pool.connection() as conn, conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    INSERT INTO consulta_mensaje (hilo_id, autor_id, de, texto, client_message_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (hilo_id, autor_id, client_message_id) DO NOTHING
                    RETURNING {_MENSAJE_COLUMNS}
                    """,
                    (hilo_id, autor_id, de, texto, client_message_id),
                )
                row = cur.fetchone()
                if row is None:
                    # A retry: the message already sent with that id.
                    cur.execute(
                        f"""
                        SELECT {_MENSAJE_COLUMNS} FROM consulta_mensaje
                        WHERE hilo_id = %s AND autor_id = %s AND client_message_id = %s
                        """,
                        (hilo_id, autor_id, client_message_id),
                    )
                    return StoredMensaje(**cur.fetchone()), False
            if de == "club":
                recipients = "SELECT h.deportista_id FROM consulta_hilo h WHERE h.id = %s"
            else:
                recipients = """
                    SELECT a.usuario_id FROM club_administrador a
                    JOIN consulta_hilo h ON h.club_id = a.club_id
                    WHERE h.id = %s
                """
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (user_id, kind, title, body, data) "
                    "SELECT r.user_id, %s, %s, %s, %s FROM ({}) AS r (user_id)"
                ).format(_OUTBOX, sql.SQL(recipients)),
                (aviso.kind, aviso.title, aviso.body, Jsonb(aviso.data), hilo_id),
            )
        return StoredMensaje(**row), True

    def mark_read(self, hilo_id: UUID, de: str, hasta_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                sql.SQL("UPDATE consulta_hilo h SET {set} WHERE h.id = %(hilo_id)s AND {avanza}")
                .format(**_leido_sql(de)),
                {"hilo_id": hilo_id, "hasta_id": hasta_id},
            )

    # Polling paths (sección F): each is a single statement on one connection.

    def mensajes_deportista(
        self,
        club_id: UUID,
        deportista_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[AccesoDeportista, UUID | None, list[StoredMensaje]]:
        # No row: no such active club. One row with m.id NULL: not an active
        # postulante, no thread or no messages.
        query = f"""
            WITH p AS (
                SELECT {_postulante_activo('c.id', '%(deportista_id)s')} AS activo,
                       (SELECT h.id FROM consulta_hilo h
                        WHERE h.club_id = c.id AND h.deportista_id = %(deportista_id)s) AS ref_hilo
                FROM club c
                WHERE c.id = %(club_id)s AND c.is_active
            )
            SELECT p.activo, p.ref_hilo, m.*
            FROM p LEFT JOIN LATERAL (
                SELECT * FROM ({_pagina_mensajes('p.ref_hilo', after_id)}) pagina WHERE p.activo
            ) m ON true
            ORDER BY m.id
        """
        params = {
            "club_id": club_id, "deportista_id": deportista_id,
            "after_id": after_id, "before_id": before_id, "limit": limit,
        }
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        if not rows:
            return "sin_club", None, []
        if not rows[0]["activo"]:
            return "no_postulante", None, []
        return "ok", rows[0]["ref_hilo"], _mensajes(rows)

    def mensajes_club(
        self,
        club_id: UUID,
        usuario_id: UUID,
        hilo_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[AccesoHilo, list[StoredMensaje]]:
        query = f"""
            WITH a AS ({_ACCESO_HILO_CLUB})
            SELECT a.*, m.*
            FROM a LEFT JOIN LATERAL (
                SELECT * FROM ({_pagina_mensajes('%(hilo_id)s', after_id)}) pagina
                WHERE a.acceso_club AND a.acceso_admin AND a.acceso_hilo
            ) m ON true
            ORDER BY m.id
        """
        params = {
            "club_id": club_id, "usuario_id": usuario_id, "hilo_id": hilo_id,
            "after_id": after_id, "before_id": before_id, "limit": limit,
        }
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        acceso = _acceso(rows[0])
        return acceso, _mensajes(rows) if acceso == "ok" else []

    def mark_read_deportista(
        self, club_id: UUID, deportista_id: UUID, hasta_id: int
    ) -> AccesoDeportista:
        # (SELECT activo FROM c) is NULL when there is no such active club.
        query = sql.SQL(
            """
            WITH c AS (
                SELECT cl.id, {activo} AS activo FROM club cl
                WHERE cl.id = %(club_id)s AND cl.is_active
            ),
            u AS (
                UPDATE consulta_hilo h SET {set}
                FROM c
                WHERE c.activo AND h.club_id = c.id AND h.deportista_id = %(deportista_id)s
                  AND {avanza}
            )
            SELECT (SELECT activo FROM c)
            """
        ).format(
            activo=sql.SQL(_postulante_activo("cl.id", "%(deportista_id)s")),
            **_leido_sql("deportista"),
        )
        params = {"club_id": club_id, "deportista_id": deportista_id, "hasta_id": hasta_id}
        with self._pool.connection() as conn:
            activo = conn.execute(query, params).fetchone()[0]
        if activo is None:
            return "sin_club"
        return "ok" if activo else "no_postulante"

    def mark_read_club(
        self, club_id: UUID, usuario_id: UUID, hilo_id: UUID, hasta_id: int
    ) -> AccesoHilo:
        query = sql.SQL(
            """
            WITH a AS ({acceso}),
            u AS (
                UPDATE consulta_hilo h SET {set}
                FROM a
                WHERE h.id = %(hilo_id)s AND h.club_id = %(club_id)s
                  AND a.acceso_admin AND {avanza}
            )
            SELECT * FROM a
            """
        ).format(acceso=sql.SQL(_ACCESO_HILO_CLUB), **_leido_sql("club"))
        params = {
            "club_id": club_id, "usuario_id": usuario_id, "hilo_id": hilo_id, "hasta_id": hasta_id,
        }
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return _acceso(cur.fetchone())

    def _load_hilos(self, query: str, params: Any) -> list[StoredHilo]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [_to_hilo(row) for row in cur.fetchall()]

    # -- canales del club (G4) ----------------------------------------------------------

    def list_canales_club(self, club_id: UUID) -> list[StoredCanal]:
        return self._load_canales(
            f"{_canal_select('club')} WHERE k.club_id = %(club_id)s {_CANAL_ORDER}",
            {"club_id": club_id},
        )

    def get_canal_club(self, club_id: UUID, canal_id: UUID) -> StoredCanal | None:
        canales = self._load_canales(
            f"{_canal_select('club')} WHERE k.id = %(canal_id)s AND k.club_id = %(club_id)s",
            {"club_id": club_id, "canal_id": canal_id},
        )
        return canales[0] if canales else None

    def create_canal(
        self,
        club_id: UUID,
        nombre: str,
        miembros_escriben: bool,
        *,
        max_canales: int,
        max_miembros: int,
    ) -> tuple[CrearCanalResult, StoredCanal | None]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"club-canales:{club_id}",)
            )
            total = conn.execute(
                "SELECT count(*) FROM club_canal WHERE club_id = %s", (club_id,)
            ).fetchone()[0]
            if total >= max_canales:
                return "sin_cupo", None
            canal_id = conn.execute(
                """
                INSERT INTO club_canal (club_id, nombre, miembros_escriben)
                VALUES (%s, %s, %s) RETURNING id
                """,
                (club_id, nombre, miembros_escriben),
            ).fetchone()[0]
            # The athletes with an ACEPTADO postulación in the club.
            conn.execute(
                f"""
                INSERT INTO club_canal_miembro (canal_id, usuario_id, nombre)
                SELECT %(canal_id)s, a.usuario_id, a.nombre
                FROM (
                    {_POSTULANTES_ACTIVOS}
                      AND EXISTS (
                        SELECT 1 FROM postulacion_convocatoria p2
                        JOIN convocatoria v2 ON v2.id = p2.convocatoria_id
                        WHERE v2.club_id = v.club_id AND p2.usuario_id = p.usuario_id
                          AND p2.estado = 'ACEPTADO')
                    {_POSTULANTES_ORDER}
                ) a
                ORDER BY a.usuario_id
                LIMIT %(max_miembros)s
                """,
                {"canal_id": canal_id, "club_id": club_id, "max_miembros": max_miembros},
            )
        return "creado", self.get_canal_club(club_id, canal_id)

    def update_canal(
        self, club_id: UUID, canal_id: UUID, changes: dict[str, Any]
    ) -> StoredCanal | None:
        values = {k: v for k, v in changes.items() if k in ("nombre", "miembros_escriben")}
        if not values:
            return self.get_canal_club(club_id, canal_id)
        query = sql.SQL(
            "UPDATE club_canal SET {} WHERE id = %(canal_id)s AND club_id = %(club_id)s RETURNING id"
        ).format(
            sql.SQL(", ").join(
                sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in values
            )
        )
        with self._pool.connection() as conn:
            updated = conn.execute(
                query, {**values, "canal_id": canal_id, "club_id": club_id}
            ).fetchone()
        return self.get_canal_club(club_id, canal_id) if updated else None

    def delete_canal(self, club_id: UUID, canal_id: UUID) -> bool:
        with self._pool.connection() as conn:
            return conn.execute(
                "DELETE FROM club_canal WHERE id = %s AND club_id = %s RETURNING id",
                (canal_id, club_id),
            ).fetchone() is not None

    def list_miembros_canal(
        self, club_id: UUID, canal_id: UUID
    ) -> list[StoredMiembroCanal] | None:
        query = """
            SELECT k.id AS canal, cm.usuario_id, cm.nombre, cm.agregado_en
            FROM club_canal k
            LEFT JOIN club_canal_miembro cm ON cm.canal_id = k.id
            WHERE k.id = %s AND k.club_id = %s
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (canal_id, club_id))
            rows = cur.fetchall()
        if not rows:
            return None
        return _miembros_canal(rows)

    def list_candidatos_canal(
        self, club_id: UUID, canal_id: UUID
    ) -> list[StoredMiembroCanal] | None:
        query = f"""
            SELECT k.id AS canal, a.usuario_id, a.nombre, NULL::timestamptz AS agregado_en
            FROM club_canal k
            LEFT JOIN LATERAL (
                {_POSTULANTES_ACTIVOS}
                  AND NOT EXISTS (
                    SELECT 1 FROM club_canal_miembro cm
                    WHERE cm.canal_id = k.id AND cm.usuario_id = p.usuario_id)
                {_POSTULANTES_ORDER}
            ) a ON true
            WHERE k.id = %(canal_id)s AND k.club_id = %(club_id)s
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, {"canal_id": canal_id, "club_id": club_id})
            rows = cur.fetchall()
        if not rows:
            return None
        return _miembros_canal(rows)

    def add_miembro_canal(
        self, club_id: UUID, canal_id: UUID, usuario_id: UUID, *, max_miembros: int
    ) -> AgregarMiembroResult:
        with self._pool.connection() as conn, conn.transaction():
            # Serializes the member count of the channel (NO KEY: messages keep flowing).
            canal = conn.execute(
                "SELECT id FROM club_canal WHERE id = %s AND club_id = %s FOR NO KEY UPDATE",
                (canal_id, club_id),
            ).fetchone()
            if canal is None:
                return "sin_canal"
            _lock_postulante(conn, club_id, usuario_id)
            nombre = _nombre_postulante(conn, club_id, usuario_id)
            if nombre is None:
                return "no_postulante"
            if conn.execute(
                "SELECT 1 FROM club_canal_miembro WHERE canal_id = %s AND usuario_id = %s",
                (canal_id, usuario_id),
            ).fetchone():
                return "ya_estaba"
            total = conn.execute(
                "SELECT count(*) FROM club_canal_miembro WHERE canal_id = %s", (canal_id,)
            ).fetchone()[0]
            if total >= max_miembros:
                return "sin_cupo"
            conn.execute(
                "INSERT INTO club_canal_miembro (canal_id, usuario_id, nombre) VALUES (%s, %s, %s)",
                (canal_id, usuario_id, nombre),
            )
        return "agregado"

    def remove_miembro_canal(self, club_id: UUID, canal_id: UUID, usuario_id: UUID) -> bool:
        query = """
            WITH k AS (SELECT id FROM club_canal WHERE id = %(canal_id)s AND club_id = %(club_id)s),
            d AS (
                DELETE FROM club_canal_miembro cm USING k
                WHERE cm.canal_id = k.id AND cm.usuario_id = %(usuario_id)s
            )
            SELECT EXISTS (SELECT 1 FROM k)
        """
        params = {"canal_id": canal_id, "club_id": club_id, "usuario_id": usuario_id}
        with self._pool.connection() as conn:
            return bool(conn.execute(query, params).fetchone()[0])

    def add_mensaje_canal(
        self,
        canal_id: UUID,
        autor_id: UUID,
        de: str,
        autor_nombre: str | None,
        texto: str,
        client_message_id: UUID,
        aviso: OutboxMessage,
    ) -> tuple[StoredMensajeCanal, bool]:
        try:
            return self._add_mensaje_canal(
                canal_id, autor_id, de, autor_nombre, texto, client_message_id, aviso
            )
        except errors.ForeignKeyViolation as error:
            raise CanalEliminado(canal_id) from error

    def _add_mensaje_canal(
        self,
        canal_id: UUID,
        autor_id: UUID,
        de: str,
        autor_nombre: str | None,
        texto: str,
        client_message_id: UUID,
        aviso: OutboxMessage,
    ) -> tuple[StoredMensajeCanal, bool]:
        with self._pool.connection() as conn, conn.transaction():
            nuevo = conn.execute(
                """
                INSERT INTO club_canal_mensaje
                    (canal_id, autor_id, de, autor_nombre, texto, client_message_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (canal_id, autor_id, client_message_id) DO NOTHING
                RETURNING id
                """,
                (canal_id, autor_id, de, None if de == "club" else autor_nombre, texto,
                 client_message_id),
            ).fetchone()
            if nuevo is not None:
                # A message cancels a pending inactivity notice and the kept period.
                conn.execute(
                    """
                    UPDATE club_canal
                    SET ultima_actividad = now(), aviso_inactividad_en = NULL, mantener_hasta = NULL
                    WHERE id = %s
                    """,
                    (canal_id,),
                )
                conn.execute(
                    sql.SQL(
                        "INSERT INTO {} (user_id, kind, title, body, data) "
                        "SELECT cm.usuario_id, %s, %s, %s, %s FROM club_canal_miembro cm "
                        "WHERE cm.canal_id = %s AND NOT cm.silenciado AND cm.usuario_id <> %s "
                        "ORDER BY cm.usuario_id"
                    ).format(_OUTBOX),
                    (aviso.kind, aviso.title, aviso.body, Jsonb(aviso.data), canal_id, autor_id),
                )
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    {_MENSAJE_CANAL_SELECT}
                    WHERE m.canal_id = %(canal_id)s AND m.autor_id = %(autor_id)s
                      AND m.client_message_id = %(client_message_id)s
                    """,
                    {"canal_id": canal_id, "autor_id": autor_id,
                     "client_message_id": client_message_id},
                )
                row = cur.fetchone()
        return _mensaje_canal(row), nuevo is not None

    def decidir_inactividad(
        self, club_id: UUID, canal_id: UUID, decision: str, mantener_hasta: datetime
    ) -> DecisionInactividadResult:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                SELECT aviso_inactividad_en FROM club_canal
                WHERE id = %s AND club_id = %s FOR UPDATE
                """,
                (canal_id, club_id),
            ).fetchone()
            if row is None:
                return "sin_canal"
            if row[0] is None:
                return "sin_aviso"
            if decision == "eliminar":
                conn.execute("DELETE FROM club_canal WHERE id = %s", (canal_id,))
                return "eliminado"
            conn.execute(
                """
                UPDATE club_canal SET aviso_inactividad_en = NULL, mantener_hasta = %s
                WHERE id = %s
                """,
                (mantener_hasta, canal_id),
            )
        return "mantenido"

    # Polling paths of channels (sección F): each is a single statement.

    def mensajes_canal_club(
        self,
        club_id: UUID,
        usuario_id: UUID,
        canal_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[StoredCanal | None, list[StoredMensajeCanal]]:
        where = """
            WHERE k.id = %(canal_id)s AND k.club_id = %(club_id)s
              AND EXISTS (SELECT 1 FROM club_administrador a
                          WHERE a.club_id = k.club_id AND a.usuario_id = %(usuario_id)s)
        """
        params = {
            "club_id": club_id, "usuario_id": usuario_id, "canal_id": canal_id,
            "after_id": after_id, "before_id": before_id, "limit": limit,
        }
        return self._canal_con_mensajes(f"{_canal_select('club')} {where}", after_id, params)

    def mark_read_canal_club(
        self, club_id: UUID, usuario_id: UUID, canal_id: UUID, hasta_id: int
    ) -> bool:
        query = f"""
            WITH a AS (
                SELECT k.id FROM club_canal k
                WHERE k.id = %(canal_id)s AND k.club_id = %(club_id)s
                  AND EXISTS (SELECT 1 FROM club_administrador ad
                              WHERE ad.club_id = k.club_id AND ad.usuario_id = %(usuario_id)s)
            ),
            u AS (
                UPDATE club_canal k SET leido_club_id = {_leido_canal('k.leido_club_id', 'k.id')}
                FROM a
                WHERE k.id = a.id AND k.leido_club_id < %(hasta_id)s
            )
            SELECT EXISTS (SELECT 1 FROM a)
        """
        params = {
            "club_id": club_id, "usuario_id": usuario_id, "canal_id": canal_id, "hasta_id": hasta_id,
        }
        with self._pool.connection() as conn:
            return bool(conn.execute(query, params).fetchone()[0])

    def list_canales_deportista(self, usuario_id: UUID) -> list[StoredCanal]:
        return self._load_canales(
            f"{_canal_select('miembro')} WHERE c.is_active {_CANAL_ORDER}",
            {"usuario_id": usuario_id},
        )

    def get_canal_deportista(self, canal_id: UUID, usuario_id: UUID) -> StoredCanal | None:
        canales = self._load_canales(
            f"{_canal_select('miembro')} WHERE k.id = %(canal_id)s AND c.is_active",
            {"usuario_id": usuario_id, "canal_id": canal_id},
        )
        return canales[0] if canales else None

    def mensajes_canal_deportista(
        self,
        canal_id: UUID,
        usuario_id: UUID,
        *,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> tuple[StoredCanal | None, list[StoredMensajeCanal]]:
        params = {
            "usuario_id": usuario_id, "canal_id": canal_id,
            "after_id": after_id, "before_id": before_id, "limit": limit,
        }
        return self._canal_con_mensajes(
            f"{_canal_select('miembro')} WHERE k.id = %(canal_id)s AND c.is_active", after_id, params
        )

    def mark_read_canal_deportista(self, canal_id: UUID, usuario_id: UUID, hasta_id: int) -> bool:
        query = f"""
            WITH a AS (
                SELECT cm.canal_id FROM club_canal_miembro cm
                JOIN club_canal k ON k.id = cm.canal_id
                JOIN club c ON c.id = k.club_id
                WHERE cm.canal_id = %(canal_id)s AND cm.usuario_id = %(usuario_id)s AND c.is_active
            ),
            u AS (
                UPDATE club_canal_miembro cm
                SET leido_id = {_leido_canal('cm.leido_id', 'cm.canal_id')}
                FROM a
                WHERE cm.canal_id = a.canal_id AND cm.usuario_id = %(usuario_id)s
                  AND cm.leido_id < %(hasta_id)s
            )
            SELECT EXISTS (SELECT 1 FROM a)
        """
        params = {"canal_id": canal_id, "usuario_id": usuario_id, "hasta_id": hasta_id}
        with self._pool.connection() as conn:
            return bool(conn.execute(query, params).fetchone()[0])

    def set_silencio_canal(self, canal_id: UUID, usuario_id: UUID, silenciado: bool) -> bool:
        query = """
            UPDATE club_canal_miembro cm SET silenciado = %(silenciado)s
            FROM club_canal k JOIN club c ON c.id = k.club_id
            WHERE k.id = cm.canal_id AND c.is_active
              AND cm.canal_id = %(canal_id)s AND cm.usuario_id = %(usuario_id)s
            RETURNING 1
        """
        params = {"canal_id": canal_id, "usuario_id": usuario_id, "silenciado": silenciado}
        with self._pool.connection() as conn:
            return conn.execute(query, params).fetchone() is not None

    # Hourly inactivity check (G4).

    @contextmanager
    def inactividad_lock(self) -> Iterator[bool]:
        """Session-level advisory lock on its own connection, released at the end."""
        with self._pool.connection() as conn:
            obtenido = bool(
                conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (_INACTIVIDAD_LOCK,)).fetchone()[0]
            )
            conn.commit()  # the session lock stays; no transaction is left open
            try:
                yield obtenido
            finally:
                if obtenido:
                    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (_INACTIVIDAD_LOCK,))
                    conn.commit()

    def canales_por_revisar(self, inactivo_desde: datetime) -> list[CanalInactividad]:
        query = """
            SELECT id, club_id, nombre, ultima_actividad, aviso_inactividad_en, mantener_hasta
            FROM club_canal
            WHERE aviso_inactividad_en IS NOT NULL OR ultima_actividad <= %s
            ORDER BY ultima_actividad, id
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (inactivo_desde,))
            return [CanalInactividad(**row) for row in cur.fetchall()]

    def avisar_inactividad(
        self, canal: CanalInactividad, ahora: datetime, aviso: OutboxMessage
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                """
                UPDATE club_canal SET aviso_inactividad_en = %(ahora)s
                WHERE id = %(id)s AND aviso_inactividad_en IS NULL
                  AND ultima_actividad = %(ultima_actividad)s
                  AND mantener_hasta IS NOT DISTINCT FROM %(mantener_hasta)s
                RETURNING club_id
                """,
                {
                    "ahora": ahora, "id": canal.id, "ultima_actividad": canal.ultima_actividad,
                    "mantener_hasta": canal.mantener_hasta,
                },
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (user_id, kind, title, body, data) "
                    "SELECT a.usuario_id, %s, %s, %s, %s FROM club_administrador a "
                    "WHERE a.club_id = %s ORDER BY a.usuario_id"
                ).format(_OUTBOX),
                (aviso.kind, aviso.title, aviso.body, Jsonb(aviso.data), row[0]),
            )
        return True

    def eliminar_canal_inactivo(self, canal: CanalInactividad) -> bool:
        if canal.aviso_inactividad_en is None:
            return False
        with self._pool.connection() as conn:
            return conn.execute(
                "DELETE FROM club_canal WHERE id = %s AND aviso_inactividad_en = %s RETURNING id",
                (canal.id, canal.aviso_inactividad_en),
            ).fetchone() is not None

    def _load_canales(self, query: str, params: Any) -> list[StoredCanal]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [_to_canal(row) for row in cur.fetchall()]

    def _canal_con_mensajes(
        self, canal_query: str, after_id: int | None, params: dict[str, Any]
    ) -> tuple[StoredCanal | None, list[StoredMensajeCanal]]:
        """The channel row (canal_query) and a page of its messages, in one statement."""
        query = f"""
            WITH canal AS ({canal_query})
            SELECT canal.*, pm.*
            FROM canal LEFT JOIN LATERAL ({_pagina_canal('canal.id', after_id)}) pm ON true
            ORDER BY pm.pm_id
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        if not rows:
            return None, []
        mensajes = [
            _mensaje_canal({k[3:]: v for k, v in row.items() if k.startswith("pm_")})
            for row in rows if row["pm_id"] is not None
        ]
        canal = _to_canal({k: v for k, v in rows[0].items() if not k.startswith("pm_")})
        return canal, mensajes

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

    def set_chat_postulantes(
        self, club_id: UUID, convocatoria_id: UUID, habilitado: bool
    ) -> StoredConvocatoria | None:
        with self._pool.connection() as conn:
            cur = conn.execute(
                """
                UPDATE convocatoria
                SET chat_postulantes_habilitado = %s, fecha_actualizacion = now()
                WHERE id = %s AND club_id = %s
                """,
                (habilitado, convocatoria_id, club_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get_convocatoria(convocatoria_id, include_inactive_clubs=True)

    def chat_habilitado_para(self, club_id: UUID, usuario_id: UUID) -> bool:
        with self._pool.connection() as conn:
            return bool(conn.execute(
                """
                SELECT NOT EXISTS (
                    SELECT 1
                    FROM postulacion_convocatoria p
                    JOIN convocatoria v ON v.id = p.convocatoria_id
                    WHERE v.club_id = %(club_id)s AND p.usuario_id = %(usuario_id)s
                      AND p.estado <> 'RECHAZADO' AND NOT v.chat_postulantes_habilitado
                )
                """,
                {"club_id": club_id, "usuario_id": usuario_id},
            ).fetchone()[0])


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
            cupos, club_id = conn.execute(
                "SELECT cupos, club_id FROM convocatoria WHERE id = %s FOR UPDATE",
                (convocatoria_id,),
            ).fetchone()
            anterior, usuario_id = conn.execute(
                "SELECT estado, usuario_id FROM postulacion_convocatoria WHERE id = %s FOR UPDATE",
                (postulacion_id,),
            ).fetchone()
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
                if estado == "RECHAZADO":
                    _sin_postulacion_activa(conn, club_id, usuario_id)
            if result == "actualizada" and evento is not None:
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


def _lock_account(conn: Any, usuario_id: UUID) -> None:
    """Serializes creating a club and activating assignments by one account, so
    the one-club-per-account check holds (the unique usuario_id backs it)."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"club-admin:{usuario_id}",)
    )


# -- canales del club (G4) ------------------------------------------------------------

# Only one instance runs the hourly inactivity check at a time.
_INACTIVIDAD_LOCK = "sportmatch-clubs-canales-inactividad"

# The author shown for a channel message: the club's current name when the
# club wrote it, the member's name snapshot otherwise. mc is the club.
_AUTOR_CANAL = (
    "CASE WHEN m.de = 'club' THEN mc.nombre ELSE COALESCE(m.autor_nombre, 'Deportista') END"
)
_MENSAJE_CANAL_COLUMNS = (
    "id", "canal_id", "autor_id", "de", "autor_nombre", "texto", "client_message_id", "enviado_en",
)
_MENSAJE_CANAL_FROM = """
    FROM club_canal_mensaje m
    JOIN club_canal mk ON mk.id = m.canal_id
    JOIN club mc ON mc.id = mk.club_id
"""


def _mensaje_canal_columns(prefix: str) -> str:
    return ", ".join(
        f"{_AUTOR_CANAL if column == 'autor_nombre' else 'm.' + column} AS {prefix}{column}"
        for column in _MENSAJE_CANAL_COLUMNS
    )


_MENSAJE_CANAL_SELECT = f"SELECT {_mensaje_canal_columns('')} {_MENSAJE_CANAL_FROM}"

_CANAL_ORDER = "ORDER BY k.ultima_actividad DESC, k.id DESC"


def _canal_select(vista: str) -> str:
    """A channel k with its club c, member count, last message (ultimo_*
    columns) and unread count, as the club sees it (vista club) or as the
    member %(usuario_id)s sees it (vista miembro; only their channels)."""
    if vista == "club":
        miembro = ""
        no_leidos = "mn.de = 'deportista' AND mn.id > k.leido_club_id"
        silenciado = "false"
    else:
        miembro = (
            "JOIN club_canal_miembro me ON me.canal_id = k.id AND me.usuario_id = %(usuario_id)s"
        )
        no_leidos = "mn.autor_id <> me.usuario_id AND mn.id > me.leido_id"
        silenciado = "me.silenciado"
    ultimo = ", ".join(f"u.{column} AS ultimo_{column}" for column in _MENSAJE_CANAL_COLUMNS)
    return f"""
        SELECT k.id, k.club_id, c.nombre AS club_nombre, c.logo AS club_logo, k.nombre,
               k.miembros_escriben, k.fecha_creacion, k.ultima_actividad,
               k.aviso_inactividad_en, k.mantener_hasta,
               (SELECT count(*) FROM club_canal_miembro cmc WHERE cmc.canal_id = k.id) AS miembros,
               (SELECT count(*) FROM club_canal_mensaje mn
                WHERE mn.canal_id = k.id AND {no_leidos}) AS no_leidos,
               {silenciado} AS silenciado,
               {ultimo}
        FROM club_canal k
        JOIN club c ON c.id = k.club_id
        {miembro}
        LEFT JOIN LATERAL (
            SELECT {_mensaje_canal_columns('')} {_MENSAJE_CANAL_FROM}
            WHERE m.canal_id = k.id
            ORDER BY m.id DESC
            LIMIT 1
        ) u ON true
    """


def _pagina_canal(canal: str, after_id: int | None) -> str:
    """Like _pagina_mensajes for the channel canal (an SQL expression), with
    every column prefixed pm_ so it can sit next to the channel's columns."""
    order = "m.id" if after_id is not None else "m.id DESC"
    return f"""
        SELECT {_mensaje_canal_columns('pm_')} {_MENSAJE_CANAL_FROM}
        WHERE m.canal_id = {canal}
          AND (%(after_id)s::bigint IS NULL OR m.id > %(after_id)s)
          AND (%(before_id)s::bigint IS NULL OR m.id < %(before_id)s)
        ORDER BY {order}
        LIMIT %(limit)s
    """


def _leido_canal(column: str, canal: str) -> str:
    """A read mark moved to %(hasta_id)s: never back, never past the channel's last message."""
    return (
        f"GREATEST({column}, LEAST(%(hasta_id)s, COALESCE("
        f"(SELECT max(mm.id) FROM club_canal_mensaje mm WHERE mm.canal_id = {canal}), 0)))"
    )


def _mensaje_canal(row: dict[str, Any]) -> StoredMensajeCanal:
    return StoredMensajeCanal(**{column: row[column] for column in _MENSAJE_CANAL_COLUMNS})


def _to_canal(row: dict[str, Any]) -> StoredCanal:
    ultimo = {column: row.pop(f"ultimo_{column}") for column in _MENSAJE_CANAL_COLUMNS}
    mensaje = _mensaje_canal(ultimo) if ultimo["id"] is not None else None
    return StoredCanal(**row, ultimo=mensaje)


def _miembros_canal(rows: list[dict[str, Any]]) -> list[StoredMiembroCanal]:
    """Members or candidates of a channel query's rows (usuario_id NULL: none), by name."""
    miembros = [
        StoredMiembroCanal(
            usuario_id=row["usuario_id"], nombre=row["nombre"], agregado_en=row["agregado_en"]
        )
        for row in rows if row["usuario_id"] is not None
    ]
    return sorted(miembros, key=lambda m: (m.nombre.lower(), str(m.usuario_id)))


def _lock_postulante(conn: Any, club_id: UUID, usuario_id: UUID) -> None:
    """Serializes a rejection with creating that person's thread or channel
    membership in the club, so neither survives a rejection (G3)."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"club-postulante:{club_id}:{usuario_id}",),
    )


def _nombre_postulante(conn: Any, club_id: UUID, usuario_id: UUID) -> str | None:
    row = conn.execute(
        f"""
        SELECT {_NOMBRE_PERFIL}
        FROM postulacion_convocatoria p
        JOIN convocatoria v ON v.id = p.convocatoria_id
        WHERE v.club_id = %s AND p.usuario_id = %s AND p.estado <> 'RECHAZADO'
        ORDER BY p.fecha_creacion DESC, p.id DESC
        LIMIT 1
        """,
        (club_id, usuario_id),
    ).fetchone()
    return row[0] if row else None


def _sin_postulacion_activa(conn: Any, club_id: UUID, usuario_id: UUID) -> None:
    """G3, inside the rejection's transaction: without another active
    postulación in the club, delete the person's consulta thread (its
    messages go by ON DELETE CASCADE) and their channel memberships."""
    _lock_postulante(conn, club_id, usuario_id)
    params = {"club_id": club_id, "usuario_id": usuario_id}
    if conn.execute(
        f"SELECT {_postulante_activo('%(club_id)s', '%(usuario_id)s')}", params
    ).fetchone()[0]:
        return
    conn.execute(
        "DELETE FROM consulta_hilo WHERE club_id = %(club_id)s AND deportista_id = %(usuario_id)s",
        params,
    )
    conn.execute(
        """
        DELETE FROM club_canal_miembro cm
        USING club_canal k
        WHERE k.id = cm.canal_id AND k.club_id = %(club_id)s AND cm.usuario_id = %(usuario_id)s
        """,
        params,
    )


def _lock_club_admins(conn: Any, club_id: UUID) -> None:
    """Serializes counting a club's administrators and pending assignments."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"club-admins:{club_id}",)
    )


def _pagina_mensajes(hilo: str, after_id: int | None) -> str:
    """Subquery with up to %(limit)s messages of the thread hilo (an SQL
    expression): the first ones after %(after_id)s, otherwise the last ones
    before %(before_id)s (or the latest ones). The caller orders by id."""
    conditions = f"""
        m.hilo_id = {hilo}
        AND (%(after_id)s::bigint IS NULL OR m.id > %(after_id)s)
        AND (%(before_id)s::bigint IS NULL OR m.id < %(before_id)s)
    """
    columns = ", ".join(f"m.{column.strip()}" for column in _MENSAJE_COLUMNS.split(","))
    order = "m.id" if after_id is not None else "m.id DESC"
    return (
        f"SELECT {columns} FROM consulta_mensaje m WHERE {conditions} "
        f"ORDER BY {order} LIMIT %(limit)s"
    )


def _leido_sql(de: str) -> dict[str, sql.Composable]:
    """SET and WHERE of a read mark update of the thread h by side de: forward
    to %(hasta_id)s, never back, never past the last message. The WHERE skips
    the write when the mark would not move."""
    column = sql.Identifier(_LEIDO_COLUMN[de])
    return {
        "set": sql.SQL(
            "{column} = GREATEST(h.{column}, LEAST(%(hasta_id)s, COALESCE("
            "(SELECT max(m.id) FROM consulta_mensaje m WHERE m.hilo_id = h.id), 0)))"
        ).format(column=column),
        "avanza": sql.SQL("h.{column} < %(hasta_id)s").format(column=column),
    }


def _mensajes(rows: list[dict[str, Any]]) -> list[StoredMensaje]:
    """The messages of a page query's rows (m.id NULL: no message)."""
    names = ("id", "hilo_id", "autor_id", "de", "texto", "client_message_id", "enviado_en")
    return [StoredMensaje(**{n: row[n] for n in names}) for row in rows if row["id"] is not None]


def _acceso(row: dict[str, Any]) -> AccesoHilo:
    if not row["acceso_club"]:
        return "sin_club"
    if not row["acceso_admin"]:
        return "no_admin"
    return "ok" if row["acceso_hilo"] else "sin_hilo"


def _to_hilo(row: dict[str, Any]) -> StoredHilo:
    ultimo = {
        name: row.pop(f"ultimo_{name}")
        for name in ("id", "autor_id", "de", "texto", "client_message_id", "enviado_en")
    }
    mensaje = None
    if ultimo["id"] is not None:
        mensaje = StoredMensaje(hilo_id=row["id"], **ultimo)
    return StoredHilo(**row, ultimo=mensaje)


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
