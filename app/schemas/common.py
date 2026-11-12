from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

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


def contains_nul(value: object) -> bool:
    """True if a text, or any text nested in a dict or list, has a NUL character (U+0000)."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(contains_nul(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_nul(item) for item in value)
    return False


def reject_bool(value: object) -> object:
    """Integer fields: `true`/`false` must not be read as 1/0 (pydantic's lax mode does)."""
    if isinstance(value, bool):
        raise ValueError("Debe ser un número entero, no verdadero o falso.")
    return value


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def no_nul_characters(cls, data: object) -> object:
        # PostgreSQL cannot store U+0000 in a text column: it used to reach the database and answer 500.
        # Checked here, before any field, so it covers every text of every body and query of the service.
        if contains_nul(data):
            raise ValueError("El texto no puede contener el carácter nulo (U+0000).")
        return data


class Deporte(APIModel):
    id: int
    nombre: str
    descripcion: str | None = None
