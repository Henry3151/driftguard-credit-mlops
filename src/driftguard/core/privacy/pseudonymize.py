"""Privacy by Design: pseudonimização, minimização e segregação de atributos sensíveis.

- Identificador direto -> HMAC-SHA256 com salt secreto (fora do Git). Sem o salt não há
  reidentificação; com ele (cofre), a área autorizada consegue atender direitos do titular.
- Atributos sensíveis (gênero, estado civil) saem da tabela do modelo e vão para uma
  tabela de auditoria separada, usada exclusivamente para medir vieses (ADR-0008).
"""

from __future__ import annotations

import hashlib
import hmac

import pandas as pd


def pseudonymize_id(value: str, salt: str) -> str:
    return hmac.new(salt.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def apply_privacy(df: pd.DataFrame, id_col: str, sensitive: list[str], salt: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = df.copy()
    out[id_col] = out[id_col].astype(str).map(lambda v: pseudonymize_id(v, salt)).astype("string")
    present = [c for c in sensitive if c in out.columns]
    audit = out[[id_col, *present]].copy()
    model_table = out.drop(columns=present)
    return model_table, audit


def age_band(age: pd.Series) -> pd.Series:
    """Generalização para relatórios (nunca publicar idade exata)."""
    bins = [0, 25, 35, 45, 55, 65, 200]
    labels = ["18-25", "26-35", "36-45", "46-55", "56-65", "65+"]
    return pd.cut(age, bins=bins, labels=labels, right=True).astype(str)
