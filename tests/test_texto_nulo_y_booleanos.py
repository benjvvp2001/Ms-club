"""Verificación independiente de HU-61/67/68, defectos 1 y 4:

1. Un carácter NUL (\\u0000) en un texto daba 500 (PostgreSQL no lo admite); ahora es 400 en todos los textos que
   llegan por la API, sin guardar nada.
4. `cupos: true` (y cualquier booleano en un entero de la convocatoria) se leía como 1; ahora es 400.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from test_editar_convocatoria import Edit, en_dias
from test_convocatorias import payload

from app.schemas.administrador import AsignacionCreate
from app.schemas.canal import CanalCreate, CanalUpdate
from app.schemas.club import ClubCreate, ClubUpdate
from app.schemas.comunicado import ComunicadoCreate
from app.schemas.consulta import MensajeCreate
from app.schemas.convocatoria import ConvocatoriaCreate, ConvocatoriaUpdate
from app.schemas.postulacion import PostulacionCreate, PostulacionEstadoUpdate

NUL = "a\u0000b texto"


# -- convocatoria: publicar y editar por la API ----------------------------------------------------------------------

CAMPOS_TEXTO = [
    ("titulo", NUL), ("descripcion", NUL), ("direccion", NUL), ("categoria_edad", NUL), ("requisitos", [NUL]),
    ("comuna", NUL),
]


@pytest.mark.parametrize("campo, valor", CAMPOS_TEXTO)
def test_editing_with_a_nul_character_is_400_and_saves_nothing(campo, valor):
    setup = Edit()
    antes = setup.client.get(f"/api/v1/clubs/announcements/{setup.call['id']}").json()
    response = setup.patch({campo: valor})
    assert response.status_code == 400, response.text
    assert setup.client.get(f"/api/v1/clubs/announcements/{setup.call['id']}").json() == antes
    assert setup.avisos() == []


@pytest.mark.parametrize("campo, valor", CAMPOS_TEXTO)
def test_publishing_with_a_nul_character_is_400_and_saves_nothing(campo, valor):
    setup = Edit()
    path = f"/api/v1/clubs/{setup.club_id}/announcements"
    cuantas = len(setup.client.get("/api/v1/clubs/announcements", params={"club_id": setup.club_id}).json()["items"])
    response = setup.client.post(path, headers=setup.owner_headers, json=payload(**{campo: valor}))
    assert response.status_code == 400, response.text
    despues = len(setup.client.get("/api/v1/clubs/announcements", params={"club_id": setup.club_id}).json()["items"])
    assert despues == cuantas


def test_the_error_names_the_field_and_does_not_echo_the_value():
    setup = Edit()
    body = setup.patch({"titulo": NUL}).json()
    assert body["detail"], body
    assert "a\u0000b" not in str(body)


# -- el mismo control en todos los textos que llegan por la API ----------------------------------------------------------

def _club(**extra):
    return {"nombre": "Club Prueba", "descripcion": "Descripción", "comuna": "Ñuñoa", "deporte_ids": [1],
            "rut": "76000000-0", **extra}


ENTRADAS = [
    ("convocatoria publicar", ConvocatoriaCreate, payload(titulo=NUL)),
    ("convocatoria editar", ConvocatoriaUpdate, {"descripcion": NUL}),
    ("perfil del club (crear): nombre", ClubCreate, _club(nombre=NUL)),
    ("perfil del club (crear): descripción", ClubCreate, _club(descripcion=NUL)),
    ("perfil del club (editar)", ClubUpdate, {"descripcion": NUL}),
    ("comunicado: título", ComunicadoCreate, {"titulo": NUL, "contenido": "contenido válido", "audiencia": "TODOS"}),
    ("comunicado: contenido", ComunicadoCreate, {"titulo": "Título ok", "contenido": NUL, "audiencia": "TODOS"}),
    ("revisión: comentario", PostulacionEstadoUpdate, {"estado": "PRESELECCIONADO", "comentario": NUL}),
    ("postulación: mensaje", PostulacionCreate, {"mensaje": NUL}),
    ("postulación: antecedente", PostulacionCreate,
     {"antecedentes": [{"institucion": NUL, "deporte": "Futbol", "anio_inicio": 2020}]}),
    ("canal: crear", CanalCreate, {"nombre": NUL}),
    ("canal: editar", CanalUpdate, {"nombre": NUL}),
    ("consulta o mensaje de canal", MensajeCreate, {"texto": NUL, "client_message_id": str(uuid4())}),
    ("administrador por correo", AsignacionCreate, {"email": "a\u0000b@example.com"}),
]


@pytest.mark.parametrize("nombre, modelo, cuerpo", ENTRADAS, ids=[e[0] for e in ENTRADAS])
def test_every_body_schema_rejects_the_nul_character(nombre, modelo, cuerpo):
    with pytest.raises(ValidationError) as error:
        modelo(**cuerpo)
    assert "a\u0000b" not in str(error.value)


def test_the_same_text_without_nul_is_still_accepted():
    ConvocatoriaCreate(**payload(titulo="Prueba normal ñandú 日本語 😀", descripcion="con <b>html</b> y 'comillas'"))
    ComunicadoCreate(titulo="Título ok", contenido="contenido válido", audiencia="TODOS")


# -- booleanos en los enteros de la convocatoria ------------------------------------------------------------------------

ENTEROS = ["cupos", "deporte_id", "edad_minima", "edad_maxima"]


@pytest.mark.parametrize("campo", ENTEROS)
@pytest.mark.parametrize("valor", [True, False])
def test_editing_rejects_booleans_in_integer_fields(campo, valor):
    setup = Edit()
    antes = setup.client.get(f"/api/v1/clubs/announcements/{setup.call['id']}").json()
    response = setup.patch({campo: valor})
    assert response.status_code == 400, response.text
    assert setup.client.get(f"/api/v1/clubs/announcements/{setup.call['id']}").json() == antes


@pytest.mark.parametrize("campo", ENTEROS)
def test_publishing_rejects_booleans_in_integer_fields(campo):
    setup = Edit()
    response = setup.client.post(f"/api/v1/clubs/{setup.club_id}/announcements", headers=setup.owner_headers,
                                 json=payload(**{campo: True}))
    assert response.status_code == 400, response.text


def test_the_lax_typing_of_numbers_is_left_as_it_was():
    """No se endurece: 5.0 y "7" siguen leyéndose como enteros (decisión: solo se informa)."""
    setup = Edit()
    assert setup.patch({"cupos": 5.0}).json()["cupos"] == 5
    assert setup.patch({"cupos": "7"}).json()["cupos"] == 7
    assert setup.patch({"cupos": 5.5}).status_code == 400
    assert setup.patch({"cupos": 9}).json()["cupos"] == 9
    assert setup.patch({"fecha_prueba": en_dias(15)}).status_code == 200
