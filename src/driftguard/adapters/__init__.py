"""Adaptadores de componentes OPCIONAIS (ADR-0001).

Regra: o núcleo nunca importa bibliotecas opcionais diretamente. Cada adaptador
verifica a flag e a disponibilidade da biblioteca; se faltar algo, vira no-op e
registra OPTIONAL_COMPONENT_SKIPPED uma única vez.
"""

from __future__ import annotations

import importlib.util

_SKIPPED: set[str] = set()


def available(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def note_skipped(component: str, reason: str) -> None:
    if component in _SKIPPED:
        return
    _SKIPPED.add(component)
    from driftguard.core.observability.logging import log_event  # import tardio evita ciclo

    log_event("OPTIONAL_COMPONENT_SKIPPED", level="INFO", component=component, reason=reason)
