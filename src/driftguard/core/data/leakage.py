"""Checagens de vazamento entre Referência e Produção.

1. IDs disjuntos.
2. Duplicatas exatas: SHA-256 de cada linha serializada (sem o ID).
3. Near-duplicates: hashing quantizado (LSH simplificado) — arredonda as features
   numéricas em faixas e compara as assinaturas; pega registros "quase iguais".
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


def _row_hashes(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    serial = df[cols].astype(str).agg("|".join, axis=1)
    return serial.map(lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())


def _quantized_signature(df: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.Series:
    q = pd.DataFrame(index=df.index)
    for c in numeric:
        x = pd.to_numeric(df[c], errors="coerce")
        # 3 algarismos significativos: 152_340 -> 152_000 ; 0.5123 -> 0.512
        with np.errstate(divide="ignore", invalid="ignore"):
            mag = np.nan_to_num(np.floor(np.log10(x.abs().to_numpy(dtype=float))), nan=0.0, posinf=0.0, neginf=0.0)
        q[c] = np.round(x / 10 ** (mag - 2)).astype("Int64").astype(str) + "e" + pd.Series(mag, index=df.index).astype(str)
    for c in categorical:
        q[c] = df[c].astype(str)
    return _row_hashes(q, list(q.columns))


def leakage_report(ref: pd.DataFrame, prod: pd.DataFrame, id_col: str, numeric: list[str], categorical: list[str]) -> dict:
    cols = numeric + categorical
    id_overlap = len(set(ref[id_col]).intersection(set(prod[id_col])))
    exact = len(set(_row_hashes(ref, cols)).intersection(set(_row_hashes(prod, cols))))
    near = len(set(_quantized_signature(ref, numeric, categorical)).intersection(set(_quantized_signature(prod, numeric, categorical))))
    return {
        "id_overlap": int(id_overlap),
        "exact_duplicate_rows": int(exact),
        "near_duplicate_signatures": int(near),
        "near_duplicate_rate_prod": round(near / max(len(prod), 1), 6),
        "passed": id_overlap == 0 and exact == 0,
    }
