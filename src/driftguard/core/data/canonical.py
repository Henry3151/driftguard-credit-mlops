"""Leitura do dataset bruto e conversão para o schema canônico.

Regra de ouro: canonicalizar NÃO é limpar. Aqui só renomeamos, convertemos unidades e
tratamos valores-sentinela DOCUMENTADOS pelo provedor. Qualquer valor inválido segue
adiante para ser barrado pelo contrato (Gate 1) — nada de fillna silencioso.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from driftguard.settings import get_settings


def raw_columns() -> list[str]:
    return list(get_settings().dataset["columns"].keys())


def read_source(path: Path | None = None) -> pd.DataFrame:
    """Lê o arquivo de origem projetando só as colunas mapeadas (minimização na fonte)."""
    cfg = get_settings()
    path = path or cfg.paths.root / cfg.dataset["source_file"]
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Dataset não encontrado em '{path}'. Baixe application_train.csv em "
            f"{cfg.dataset['source_url']} e salve em data/raw/ (ver README)."
        )
    return pd.read_csv(path, usecols=raw_columns(), low_memory=False)


def canonicalize(df: pd.DataFrame) -> pd.DataFrame:
    """Aceita lotes com nomes brutos ou canônicos e devolve o schema canônico."""
    cfg = get_settings().dataset
    mapping: dict[str, str] = cfg["columns"]
    out = df.rename(columns=mapping).copy()
    converted_from_raw = set(mapping).intersection(df.columns)

    if "DAYS_EMPLOYED" in converted_from_raw:
        sentinel = cfg["transforms"]["employment_sentinel"]
        days = pd.to_numeric(out["employment_years"], errors="coerce")
        out["has_employment"] = np.where(days == sentinel, 0, 1)
        out["employment_years"] = np.where(days == sentinel, 0.0, -days / 365.25)
    if "DAYS_BIRTH" in converted_from_raw:
        out["age_years"] = -pd.to_numeric(out["age_years"], errors="coerce") / 365.25

    if "customer_id" in out.columns:
        out["customer_id"] = out["customer_id"].astype("string")
    return out


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Features derivadas (etapa de transformação validada pelo Gate 2)."""
    out = df.copy()
    income = pd.to_numeric(out["income"], errors="coerce")
    out["debt_to_income"] = pd.to_numeric(out["annuity"], errors="coerce") / income
    out["credit_to_income"] = pd.to_numeric(out["credit_amount"], errors="coerce") / income
    return out
