"""Dados SINTÉTICOS no formato bruto do Home Credit.

Usos (Privacy by Design — nenhum dado real em testes):
- fixtures de testes e CI (o dataset real não pode ser redistribuído);
- modo demonstração (`python -m driftguard demo`);
- lote propositalmente defeituoso para provar que o contrato bloqueia a ingestão.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

INCOME_TYPES = ["Working", "Commercial associate", "Pensioner", "State servant"]
EDUCATION = ["Secondary / secondary special", "Higher education", "Incomplete higher", "Lower secondary"]
FAMILY = ["Married", "Single / not married", "Civil marriage", "Separated", "Widow"]


def synthetic_applications(n: int, seed: int = 7, id_start: int = 900_000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = rng.uniform(21, 68, n)
    income = np.exp(rng.normal(11.9, 0.5, n)).round(-2)
    credit = (income * rng.uniform(1.0, 6.0, n)).round(-2)
    annuity = (credit / rng.uniform(10, 30, n)).round(1)
    income_type = rng.choice(INCOME_TYPES, n, p=[0.52, 0.23, 0.18, 0.07])
    days_employed = np.where(income_type == "Pensioner", 365243, -rng.uniform(30, 12000, n).round())
    ext2 = rng.beta(5, 3, n)
    ext3 = np.where(rng.random(n) < 0.2, np.nan, rng.beta(4, 3, n))
    logit = -2.6 + 3.0 * (0.55 - ext2) + 2.0 * (0.5 - np.nan_to_num(ext3, nan=0.45)) + 3.0 * (annuity / income - 0.18) - 0.01 * (age - 40)
    target = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return pd.DataFrame(
        {
            "SK_ID_CURR": np.arange(id_start, id_start + n),
            "TARGET": target,
            "NAME_CONTRACT_TYPE": rng.choice(["Cash loans", "Revolving loans"], n, p=[0.9, 0.1]),
            "CODE_GENDER": rng.choice(["F", "M"], n, p=[0.65, 0.35]),
            "FLAG_OWN_REALTY": rng.choice(["Y", "N"], n, p=[0.7, 0.3]),
            "CNT_CHILDREN": rng.choice([0, 1, 2, 3], n, p=[0.7, 0.2, 0.08, 0.02]),
            "AMT_INCOME_TOTAL": income,
            "AMT_CREDIT": credit,
            "AMT_ANNUITY": annuity,
            "AMT_GOODS_PRICE": (credit * 0.9).round(-2),
            "NAME_INCOME_TYPE": income_type,
            "NAME_EDUCATION_TYPE": rng.choice(EDUCATION, n, p=[0.7, 0.24, 0.04, 0.02]),
            "NAME_FAMILY_STATUS": rng.choice(FAMILY, n, p=[0.63, 0.15, 0.1, 0.07, 0.05]),
            "DAYS_BIRTH": -(age * 365.25).round(),
            "DAYS_EMPLOYED": days_employed,
            "EXT_SOURCE_2": ext2,
            "EXT_SOURCE_3": ext3,
            "REGION_RATING_CLIENT": rng.choice([1, 2, 3], n, p=[0.1, 0.74, 0.16]),
        }
    )


def bad_batch(n: int = 2000, seed: int = 13) -> tuple[pd.DataFrame, dict[str, int]]:
    """Lote com erros injetados de propósito (≈ 12% das linhas) + coluna não contratada."""
    rng = np.random.default_rng(seed)
    df = synthetic_applications(n, seed=seed, id_start=990_000)
    idx = rng.permutation(n)
    injected = {
        "renda_nula": idx[0:60],
        "menor_de_idade": idx[60:120],
        "renda_negativa": idx[120:150],
        "categoria_invalida": idx[150:190],
        "id_duplicado": idx[190:230],
        "score_bureau_fora_de_faixa": idx[230:250],
    }
    df.loc[injected["renda_nula"], "AMT_INCOME_TOTAL"] = np.nan
    df.loc[injected["menor_de_idade"], "DAYS_BIRTH"] = -(16 * 365.25)
    df.loc[injected["renda_negativa"], "AMT_INCOME_TOTAL"] = -5000.0
    df.loc[injected["categoria_invalida"], "NAME_CONTRACT_TYPE"] = "Crypto loans"
    df.loc[injected["id_duplicado"], "SK_ID_CURR"] = df.loc[idx[300:340], "SK_ID_CURR"].to_numpy()
    df.loc[injected["score_bureau_fora_de_faixa"], "EXT_SOURCE_2"] = 1.7
    # Coluna NÃO prevista no contrato (ex.: sistema upstream passou a enviar documento do cliente)
    df["CPF_CLIENTE"] = "000.000.000-00"
    return df, {k: len(v) for k, v in injected.items()}
