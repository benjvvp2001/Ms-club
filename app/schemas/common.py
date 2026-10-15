from __future__ import annotations

from pydantic import BaseModel, ConfigDict

PAGE_SIZE_MAX = 50

_LETTERS = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
# Same comuna rule as Ms_Users: "Ñuñoa", "O'Higgins", "Pedro Aguirre Cerda", "Til-Til".
PLACE_PATTERN = rf"^[{_LETTERS}.]+(?:[ '-][{_LETTERS}.]+)*$"

# Sport names are compared without case or accents, so "futbol" finds "Fútbol".
_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def fold_name(text: str) -> str:
    """Python twin of the SQL translate(lower(...)) used by the repository."""
    return text.strip().lower().translate(_ACCENTS)


def blank_is_none(value: object) -> object:
    """Optional text fields: an empty or blank string means "no value"."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Deporte(APIModel):
    id: int
    nombre: str
    descripcion: str | None = None
