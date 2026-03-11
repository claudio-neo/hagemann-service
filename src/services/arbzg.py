"""
Servicios ArbZG (Arbeitszeitgesetz) — Ley alemana de tiempo de trabajo (HG-18)

Reglas implementadas:
  - Pausa mínima obligatoria: >6h → 30min, >9h → 45min
  - Jornada máxima: aviso si >10h (600 min)
  - Normalización de UIDs NFC en formato hexadecimal
"""
import re
from typing import Optional


# ── Pausas mínimas (§4 ArbZG) ────────────────────────────────────────────────

def calcular_pausa_minima(minutos_totales: int) -> int:
    """
    Calcula la pausa mínima requerida por ley según los minutos trabajados.

    §4 ArbZG:
      - Jornada > 9 horas (540 min) → pausa mínima 45 min
      - Jornada > 6 horas (360 min) → pausa mínima 30 min
      - Jornada ≤ 6 horas           → sin pausa obligatoria

    Args:
        minutos_totales: Minutos brutos trabajados (sin descontar pausa)

    Returns:
        Minutos de pausa mínima requerida (0, 30 o 45)
    """
    if minutos_totales > 540:
        return 45
    if minutos_totales > 360:
        return 30
    return 0


# ── Jornada máxima (§3 ArbZG) ────────────────────────────────────────────────

def verificar_jornada_maxima(minutos_totales: int) -> Optional[str]:
    """
    Verifica si la jornada supera el máximo legal.

    §3 ArbZG: La jornada ordinaria no puede exceder 10 horas (600 min)
    con carácter general. Si se supera, devuelve un mensaje de advertencia.

    Args:
        minutos_totales: Minutos trabajados

    Returns:
        Cadena de advertencia si se supera el límite, None en caso contrario
    """
    if minutos_totales > 600:
        horas = minutos_totales / 60
        return (
            f"⚠️ ArbZG §3: Jornada de {horas:.1f}h supera el máximo legal de 10h. "
            f"Registrar como horas extra y verificar con el responsable."
        )
    return None


# ── Normalización NFC UID ─────────────────────────────────────────────────────

def normalizar_nfc(raw: str) -> str:
    """
    Normaliza un UID NFC a formato canónico: pares hex en minúsculas separados
    por espacio.

    Formatos de entrada aceptados:
      "8161 07D2"    → "81 61 07 d2"
      "81:61:07:d2"  → "81 61 07 d2"
      "816107d2"     → "81 61 07 d2"
      "81 61 07 d2"  → "81 61 07 d2"

    Args:
        raw: String crudo del UID NFC

    Returns:
        UID normalizado en formato "xx xx xx xx" (lowercase hex, pares separados
        por espacio)

    Raises:
        ValueError: Si el string no contiene bytes hexadecimales válidos
    """
    if not raw or not raw.strip():
        raise ValueError("UID NFC vacío")

    # Eliminar separadores comunes: espacios, comas, guiones, dos puntos
    cleaned = re.sub(r"[\s:\-,]", "", raw.strip())

    # Verificar que solo quedan caracteres hex
    if not re.match(r"^[0-9a-fA-F]+$", cleaned):
        raise ValueError(f"UID NFC inválido: caracteres no hexadecimales en '{raw}'")

    # Debe tener longitud par (bytes completos)
    if len(cleaned) % 2 != 0:
        raise ValueError(f"UID NFC inválido: longitud impar ({len(cleaned)} chars) en '{raw}'")

    # Dividir en pares y convertir a minúsculas
    pairs = [cleaned[i:i+2].lower() for i in range(0, len(cleaned), 2)]
    return " ".join(pairs)
