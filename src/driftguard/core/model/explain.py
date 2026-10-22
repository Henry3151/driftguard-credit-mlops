"""Explicabilidade por decisão (LGPD art. 20) via contribuições SHAP nativas do XGBoost.

Gera os "motivos da decisão": as 3 features que mais aumentaram o risco de cada
proposta recusada, em linguagem simples, sem expor dados pessoais.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.pipeline import Pipeline

REASON_TEXT = {
    "ext_source_2": "score de bureau externo (fonte 2) baixo",
    "ext_source_3": "score de bureau externo (fonte 3) baixo ou ausente",
    "debt_to_income": "comprometimento de renda com a parcela elevado",
    "credit_to_income": "valor do crédito alto em relação à renda",
    "income": "renda declarada",
    "annuity": "valor da parcela",
    "credit_amount": "valor do crédito solicitado",
    "goods_price": "valor do bem financiado",
    "age_years": "faixa etária",
    "employment_years": "tempo de emprego curto",
    "has_employment": "ausência de vínculo empregatício",
    "children_count": "número de dependentes",
    "region_rating": "classificação de risco da região",
    "contract_type": "tipo de contrato",
    "income_type": "tipo de fonte de renda",
    "education": "escolaridade",
    "own_realty": "não possui imóvel",
}


def shap_by_feature(pipe: Pipeline, X: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    pre, clf = pipe.named_steps["pre"], pipe.named_steps["clf"]
    Xt = pre.transform(X)
    contrib = clf.get_booster().predict(xgb.DMatrix(Xt), pred_contribs=True)[:, :-1]
    names = [n.split("__", 1)[1] for n in pre.get_feature_names_out()]
    src = [n if n in numeric else next(c for c in categorical if n.startswith(c + "_")) for n in names]
    df = pd.DataFrame(contrib, columns=names, index=X.index)
    return df.T.groupby(np.array(src)).sum().T


def decision_reasons(pipe: Pipeline, X: pd.DataFrame, numeric: list[str], categorical: list[str], top_k: int = 3) -> pd.DataFrame:
    shap = shap_by_feature(pipe, X, numeric, categorical)
    rows = []
    for idx, r in shap.iterrows():
        top = r.sort_values(ascending=False).head(top_k)
        rows.append({f"motivo_{i + 1}": REASON_TEXT.get(f, f) for i, f in enumerate(top.index) if top[f] > 0} | {"_idx": idx})
    return pd.DataFrame(rows).set_index("_idx")
