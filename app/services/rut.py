from __future__ import annotations

import re

# [0-9], not \d: \d also matches other Unicode digits.
_RUT_PATTERN = re.compile(r"^([0-9]{7,8})-([0-9K])$")
# Legal persons (companies, clubs, foundations) have RUTs from 50.000.000 up.
PERSONA_JURIDICA_MIN = 50_000_000


def digito_verificador(cuerpo: int) -> str:
    """Módulo 11 check digit of a RUT body: 0-9 or K."""
    total, factor = 0, 2
    while cuerpo:
        total += (cuerpo % 10) * factor
        cuerpo //= 10
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - total % 11
    return {11: "0", 10: "K"}.get(resto, str(resto))


def normalizar_rut(valor: str) -> str | None:
    """The RUT as stored (76123456-0: no dots, upper-case K), or None when it
    is not a valid RUT of a legal person (format, check digit and body)."""
    rut = valor.strip().replace(".", "").upper()
    match = _RUT_PATTERN.match(rut)
    if match is None:
        return None
    cuerpo = int(match.group(1))
    if cuerpo < PERSONA_JURIDICA_MIN or digito_verificador(cuerpo) != match.group(2):
        return None
    return rut
