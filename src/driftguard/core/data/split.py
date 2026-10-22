"""Separação Referência × Produção (boa prática de MLOps).

- Referência: dataset limpo usado no treino/validação/teste do modelo base.
- Pool de produção: clientes disjuntos da referência, de onde o simulador sorteia
  as janelas mensais (onde o drift acontece).
"""

from __future__ import annotations

import pandas as pd
from sklearn.model_selection import train_test_split


def split_reference_production(df: pd.DataFrame, target: str, production_share: float, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    ref, prod = train_test_split(df, test_size=production_share, stratify=df[target], random_state=seed)
    return ref.reset_index(drop=True), prod.reset_index(drop=True)


def split_train_valid_test(ref: pd.DataFrame, target: str, seed: int) -> dict[str, pd.DataFrame]:
    train, rest = train_test_split(ref, test_size=0.30, stratify=ref[target], random_state=seed)
    valid, test = train_test_split(rest, test_size=0.50, stratify=rest[target], random_state=seed)
    return {"train": train.reset_index(drop=True), "valid": valid.reset_index(drop=True), "test": test.reset_index(drop=True)}
