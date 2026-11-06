
"""Dashboard de monitoramento em HTML autocontido (abre no navegador, sem servidor).

Hierarquia em 3 níveis: visão geral (semáforo) -> componentes -> detalhe.
Fontes: logs JSON, relatórios de validação/drift/monitoramento, alertas e causalidade.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from driftguard.core.observability.alerts import read_alerts
from driftguard.core.observability.logging import read_events


C = {
    "ok": "#1a7f37",
    "warn": "#b7791f",
    "crit": "#c0392b",
    "muted": "#6b7280",
    "a": "#2563eb",
    "b": "#7c3aed",
    "c": "#0891b2",
}

SEV_COLOR = {
    "critical": C["crit"],
    "warning": C["warn"],
    "info": C["a"],
}

PRIOR = {
    "critical": "P1",
    "warning": "P2",
    "info": "P3",
}

LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=40, r=20, t=50, b=40),
    height=340,
    font=dict(size=12),
    legend=dict(orientation="h", y=-0.2),
)


def _fig(fig: go.Figure) -> str:
    fig.update_layout(**LAYOUT)
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config={"displaylogo": False, "responsive": True},
    )


def _read_json(p: Path) -> Any:
    return (
        json.loads(p.read_text(encoding="utf-8"))
        if p.exists()
        else None
    )


def _status(
    ok: bool | None,
    warn: bool = False,
) -> tuple[str, str]:
    if ok is None:
        return "sem dados", C["muted"]

    if ok and not warn:
        return "saudável", C["ok"]

    return (
        ("atenção", C["warn"])
        if ok
        else ("crítico", C["crit"])
    )


def _stage_display_status(
    stage: str,
    status: str,
    run_id: str,
    skipped_runs: set[str],
) -> str:
    """Diferencia retreinamento ignorado de retreinamento executado."""
    if (
        stage == "retrain"
        and status == "success"
        and run_id in skipped_runs
    ):
        return "skipped (esperado)"

    return status


def _card(
    title: str,
    value: str,
    status: tuple[str, str],
    sub: str = "",
) -> str:
    label, color = status

    return (
        f'<div class="card" style="border-top:5px solid {color}">'
        f'<div class="ct">{html.escape(title)}</div>'
        f'<div class="cv">{html.escape(value)}</div>'
        f'<div class="cs" style="color:{color}">● {label}</div>'
        f'<div class="sub">{html.escape(sub)}</div>'
        "</div>"
    )


def _table(
    df: pd.DataFrame,
    max_rows: int = 30,
) -> str:
    return df.head(max_rows).to_html(
        index=False,
        classes="tbl",
        border=0,
        na_rep="—",
        escape=True,
    )


def build(
    root: Path,
    slo: dict,
    out: Path,
) -> Path:
    rep = root / "reports"

    events = read_events(root / "logs" / "pipeline.jsonl")
    ev = pd.DataFrame(events)

    wm_path = rep / "monitoring" / "window_metrics.csv"
    wm = (
        pd.read_csv(wm_path)
        if wm_path.exists()
        else pd.DataFrame()
    )

    drift_path = rep / "drift" / "drift_summary.csv"
    drift = (
        pd.read_csv(drift_path)
        if drift_path.exists()
        else pd.DataFrame()
    )

    fair_path = rep / "monitoring" / "fairness_by_window.csv"
    fair = (
        pd.read_csv(fair_path)
        if fair_path.exists()
        else pd.DataFrame()
    )

    alerts = pd.DataFrame(
        read_alerts(rep / "alerts.jsonl")
    )

    meta = (
        _read_json(
            root / "models" / "champion" / "metadata.json"
        )
        or {}
    )

    base = meta.get("baseline_test", {})

    adwin = (
        _read_json(
            rep / "drift" / "adwin_score_stream.json"
        )
        or {}
    )

    bad = _read_json(
        rep / "validation" / "gate1_contract_bad_batch.json"
    )

    src = _read_json(
        rep / "validation" / "gate1_contract_source.json"
    )

    chal = _read_json(
        rep / "model" / "challenger_evaluation.json"
    )

    causal = _read_json(
        rep / "causal" / "attribution.json"
    )

    # ==========================================================
    # NÍVEL 1: VISÃO GERAL / SEMÁFORO
    # ==========================================================

    stage_ev = (
        ev[
            ev["event"].isin(
                ["STAGE_SUCCEEDED", "STAGE_FAILED"]
            )
        ].copy()
        if len(ev)
        else pd.DataFrame()
    )

    last_run = (
        ev["run_id"].iloc[-1]
        if len(ev)
        else None
    )

    # Última execução registrada de cada estágio.
    run_ev = (
        stage_ev.groupby("stage", sort=False).tail(1)
        if len(stage_ev)
        else stage_ev
    )

    if len(run_ev):
        exit_code = (
            run_ev["exit_code"]
            if "exit_code" in run_ev
            else pd.Series(
                index=run_ev.index,
                dtype=float,
            )
        )

        # O bloqueio do lote defeituoso é esperado.
        expected_fail = (
            run_ev["stage"].eq("validate_batch")
            & exit_code.eq(2)
        )

        run_ev = run_ev.assign(
            status=run_ev["status"].where(
                ~expected_fail,
                "blocked (esperado)",
            )
        )

    # ==========================================================
    # AJUSTE: RETREINAMENTO IGNORADO
    # ==========================================================

    skipped_runs = (
        set(
            ev.loc[
                ev["event"].eq("RETRAIN_SKIPPED"),
                "run_id",
            ]
            .dropna()
            .astype(str)
        )
        if (
            len(ev)
            and {"event", "run_id"}.issubset(ev.columns)
        )
        else set()
    )

    if len(run_ev):
        run_ev = run_ev.copy()

        if "run_id" in run_ev.columns:
            run_ev["display_status"] = [
                _stage_display_status(
                    stage=str(row.stage),
                    status=str(row.status),
                    run_id=str(row.run_id),
                    skipped_runs=skipped_runs,
                )
                for row in run_ev.itertuples()
            ]
        else:
            run_ev["display_status"] = run_ev["status"]

    # Preserva o cálculo original de sucesso operacional.
    ok_rate = (
        float(
            run_ev["status"]
            .isin(["success", "blocked (esperado)"])
            .mean()
        )
        if len(run_ev)
        else None
    )

    cards = [
        _card(
            "Pipeline",
            (
                f"{ok_rate:.0%} estágios OK"
                if ok_rate is not None
                else "—"
            ),
            _status(
                None
                if ok_rate is None
                else (
                    ok_rate
                    >= slo["pipeline"]["stage_success_rate_slo"]
                )
            ),
            (
                f"{len(run_ev)} estágios monitorados"
                + (
                    " · lote defeituoso bloqueado ✔"
                    if bad and bad["decision"] == "blocked"
                    else ""
                )
            ),
        )
    ]

    if len(wm):
        last = wm.iloc[-1]
        dq_min = wm["dq_score"].min()

        cards.append(
            _card(
                "Qualidade de dados",
                f"DQ mín. {dq_min:.1f}",
                _status(
                    dq_min >= slo["quality"]["dq_score_slo"]
                ),
                (
                    f"{int(wm['rows_quarantined'].sum())}"
                    " linhas em quarentena nas janelas"
                ),
            )
        )

        auc_ok = (
            last["auc"]
            >= base.get("auc", 0)
            - slo["model"]["auc_max_drop"]
        )

        cards.append(
            _card(
                "Modelo (AUC)",
                (
                    f"{last['auc']:.3f}"
                    f" vs {base.get('auc', 0):.3f}"
                ),
                _status(
                    auc_ok,
                    warn=last["drifted_features"] > 0,
                ),
                (
                    f"{last['month']} · "
                    "política de resposta: "
                    f"{last['policy_level']}"
                ),
            )
        )

        cards.append(
            _card(
                "Drift estatístico (última janela)",
                (
                    f"{int(last['drifted_features'])}"
                    " features"
                ),
                _status(
                    last["drifted_features"] == 0
                ),
                (
                    f"MMD p={last['mmd_p_value']:.3f}"
                    f" · PSI score {last['psi_score']:.3f}"
                ),
            )
        )

        cards.append(
            _card(
                "Negócio",
                (
                    f"{last['loss_gap']:+.0%}"
                    " perda real vs esperada"
                ),
                _status(
                    last["loss_gap"] <= 0.10
                ),
                (
                    f"perda esperada R$ "
                    f"{last['expected_loss']:,.0f}"
                ).replace(",", "."),
            )
        )

    n_crit = (
        int((alerts["severity"] == "critical").sum())
        if len(alerts)
        else 0
    )

    n_warn = (
        int((alerts["severity"] == "warning").sum())
        if len(alerts)
        else 0
    )

    cards.append(
        _card(
            "Alertas",
            f"{n_crit} P1 · {n_warn} P2",
            _status(
                n_crit == 0,
                warn=n_warn > 0,
            ),
            f"{len(alerts)} no total",
        )
    )

    sections: list[str] = []

    # ==========================================================
    # AJUSTE: EXPLICAÇÃO DAS SEVERIDADES
    # ==========================================================

    sections.append(
        "<p class='note'>"
        "<b>Como interpretar as severidades:</b> "
        "o drift estatístico identifica mudanças "
        "nas distribuições. A política operacional "
        "combina sinais de drift e desempenho para "
        "decidir a resposta ao modelo. "
        "Um alerta P1 de impacto financeiro exige "
        "análise, mas não implica retreinamento "
        "automático."
        "</p>"
    )

    # ==========================================================
    # NÍVEL 2: SAÚDE DO PIPELINE
    # ==========================================================

    if len(run_ev):
        st = run_ev[
            [
                "stage",
                "display_status",
                "duration_ms",
                "rows",
                "ts",
            ]
        ].rename(
            columns={
                "display_status": "status",
                "ts": "ultima_execucao",
            }
        ).copy()

        st["duracao_s"] = (
            st.pop("duration_ms") / 1000
        ).round(2)

        st["ultima_execucao"] = (
            st["ultima_execucao"]
            .str[:19]
            .str.replace("T", " ")
        )

        st["rows"] = st["rows"].map(
            lambda v: (
                "—"
                if pd.isna(v)
                else f"{int(v):,}".replace(",", ".")
            )
        )

        st["duracao_total_s"] = st["duracao_s"]

        fig = go.Figure(
            go.Bar(
                x=st["stage"],
                y=st["duracao_total_s"],
                marker_color=[
                    (
                        C["ok"]
                        if s == "success"
                        else (
                            C["warn"]
                            if "esperado" in str(s)
                            else C["crit"]
                        )
                    )
                    for s in st["status"]
                ],
                text=st["duracao_total_s"],
                textposition="outside",
            )
        )

        fig.update_layout(
            title=(
                "Duração por estágio "
                "(última execução, s) — vermelho = falha"
            ),
            yaxis_title="segundos",
        )

        sections.append(
            "<h2>Saúde do pipeline</h2>"
            "<div class='grid2'>"
            f"<div>{_fig(fig)}</div>"
            "<div class='scroll'>"
            f"{_table(st.drop(columns=['duracao_total_s']))}"
            "</div></div>"
        )

    # ==========================================================
    # INFRAESTRUTURA
    # ==========================================================

    if len(wm):
        fig = go.Figure()

        fig.add_bar(
            x=wm["month"],
            y=wm["rows_accepted"],
            name="aceitas",
            marker_color=C["ok"],
        )

        fig.add_bar(
            x=wm["month"],
            y=wm["rows_quarantined"],
            name="quarentena",
            marker_color=C["crit"],
        )

        fig.update_layout(
            barmode="stack",
            title="Volume processado por janela",
        )

        fig2 = go.Figure()

        for col, nm, color in (
            ("latency_p50_ms_1k", "p50", C["a"]),
            ("latency_p95_ms_1k", "p95", C["b"]),
            ("latency_p99_ms_1k", "p99", C["crit"]),
        ):
            fig2.add_scatter(
                x=wm["month"],
                y=wm[col],
                name=nm,
                mode="lines+markers",
                line=dict(color=color),
            )

        fig2.add_hline(
            y=slo["infra"]["prediction_latency_p95_ms_per_1k"],
            line_dash="dot",
            annotation_text="SLO p95",
        )

        fig2.update_layout(
            title="Latência de predição (ms por 1.000 linhas)"
        )

        res_html = ""

        if len(run_ev) and "memory_mb" in run_ev:
            res = run_ev.dropna(
                subset=["memory_mb"]
            )

            fig3 = go.Figure(
                go.Bar(
                    x=res["stage"],
                    y=res["memory_mb"],
                    marker_color=C["c"],
                    text=res["memory_mb"].round(0),
                    textposition="outside",
                )
            )

            fig3.add_hline(
                y=slo["infra"]["max_memory_mb"],
                line_dash="dot",
                annotation_text="limite",
            )

            fig3.update_layout(
                title=(
                    "Memória de pico do processo até "
                    "o fim de cada estágio (MB)"
                )
            )

            res_html = _fig(fig3)

        sections.append(
            "<h2>Infraestrutura</h2>"
            "<div class='grid3'>"
            f"<div>{_fig(fig)}</div>"
            f"<div>{_fig(fig2)}</div>"
            f"<div>{res_html}</div>"
            "</div>"
        )

    # ==========================================================
    # QUALIDADE DE DADOS
    # ==========================================================

    dq_parts = []

    if bad:
        top = sorted(
            bad["rule_failures"].items(),
            key=lambda kv: -kv[1],
        )[:10]

        fig = go.Figure(
            go.Bar(
                y=[k for k, _ in top][::-1],
                x=[v for _, v in top][::-1],
                orientation="h",
                marker_color=C["crit"],
            )
        )

        fig.update_layout(
            title=(
                "Lote defeituoso: "
                f"{bad['decision'].upper()}"
                " — violações por regra"
            ),
            height=380,
        )

        dq_parts.append(
            f"<div>{_fig(fig)}"
            "<p class='note'>"
            f"<b>Motivo:</b> "
            f"{html.escape(str(bad['blocked_reason']))}"
            "<br>"
            "Quarentena: <code>"
            f"{html.escape(str(bad['quarantine_path']).split('/data/')[-1])}"
            "</code></p></div>"
        )

    if len(wm):
        fig = go.Figure(
            go.Scatter(
                x=wm["month"],
                y=wm["dq_score"],
                mode="lines+markers+text",
                text=wm["dq_score"],
                textposition="top center",
                line=dict(color=C["a"]),
            )
        )

        fig.add_hline(
            y=slo["quality"]["dq_score_slo"],
            line_dash="dot",
            annotation_text="SLO",
        )

        fig.update_layout(
            title="Data Quality Score por janela (6 dimensões)",
            yaxis_range=[80, 101],
        )

        source_note = ""

        if src:
            source_note = (
                "<p class='note'>"
                f"Fonte original: {src['n_rows']:,} linhas"
                f" · {src['n_invalid_rows']} em quarentena"
                f" · DQ {src['dq_score']}"
                f" · decisão <b>{src['decision']}</b>"
                "</p>"
            ).replace(",", ".")

        dq_parts.append(
            f"<div>{_fig(fig)}{source_note}</div>"
        )

    if dq_parts:
        sections.append(
            "<h2>Qualidade de dados & contratos</h2>"
            "<div class='grid2'>"
            + "".join(dq_parts)
            + "</div>"
        )

    # ==========================================================
    # DATA DRIFT
    # ==========================================================

    if len(drift):
        piv = drift.pivot_table(
            index="feature",
            columns="window",
            values="psi",
        ).reindex(
            drift[
                drift["window"] == drift["window"].max()
            ]
            .sort_values(
                "psi",
                ascending=False,
            )["feature"]
        )

        months = (
            dict(zip(wm["window"], wm["month"]))
            if len(wm)
            else {}
        )

        fig = go.Figure(
            go.Heatmap(
                z=piv.values,
                x=[
                    months.get(c, c)
                    for c in piv.columns
                ],
                y=piv.index,
                colorscale="YlOrRd",
                zmin=0,
                zmax=max(
                    0.3,
                    float(piv.values.max()),
                ),
                text=piv.round(3).values,
                texttemplate="%{text}",
                colorbar=dict(title="PSI"),
            )
        )

        fig.update_layout(
            title=(
                "PSI por feature × janela "
                "(≥0,10 moderado · ≥0,25 severo)"
            ),
            height=520,
        )

        f2 = make_subplots(
            specs=[[{"secondary_y": True}]]
        )

        f2.add_bar(
            x=wm["month"],
            y=wm["drifted_features"],
            name="features com drift (PSI/KS+BH)",
            marker_color=C["warn"],
        )

        f2.add_scatter(
            x=wm["month"],
            y=wm["mmd2"],
            name="MMD² (multivariado)",
            mode="lines+markers",
            line=dict(
                color=C["b"],
                width=3,
            ),
            secondary_y=True,
            text=[
                f"p={v:.3f}"
                for v in wm["mmd_p_value"]
            ],
            hovertemplate=(
                "MMD²=%{y:.4f} %{text}"
                "<extra></extra>"
            ),
        )

        f2.update_layout(
            title=(
                "Drift univariado "
                "(nº de features) × multivariado (MMD²)"
            )
        )

        f2.update_yaxes(
            title_text="features com drift",
            secondary_y=False,
        )

        f2.update_yaxes(
            title_text="MMD²",
            secondary_y=True,
        )

        links = (
            " · ".join(
                (
                    "<a href='../drift/"
                    f"evidently_{w}.html'>"
                    f"{months.get(w, w)}</a>"
                )
                for w in wm["window"]
            )
            if len(wm)
            else ""
        )

        adw = (
            (
                "ADWIN no fluxo de scores: "
                "primeira mudança após a referência em "
                "<b>"
                f"{months.get(adwin.get('first_change_after_reference'), adwin.get('first_change_after_reference'))}"
                "</b>"
            )
            if adwin
            else ""
        )

        sections.append(
            "<h2>Drift</h2>"
            "<div class='grid2'>"
            f"<div>{_fig(fig)}</div>"
            "<div>"
            f"{_fig(f2)}"
            f"<p class='note'>{adw}<br>"
            f"Relatórios Evidently: {links}"
            " · <a href='../drift/"
            "evidently_consolidated_reference_vs_last_window.html'>"
            "consolidado</a>"
            "</p></div></div>"
        )

    # ==========================================================
    # MODELO E IMPACTO DE NEGÓCIO
    # ==========================================================

    if len(wm):
        fig = go.Figure()

        fig.add_scatter(
            x=wm["month"],
            y=wm["auc"],
            name="AUC",
            mode="lines+markers",
            line=dict(
                color=C["a"],
                width=3,
            ),
        )

        fig.add_scatter(
            x=wm["month"],
            y=wm["ks"],
            name="KS",
            mode="lines+markers",
            line=dict(color=C["c"]),
        )

        if base:
            fig.add_hline(
                y=(
                    base["auc"]
                    - slo["model"]["auc_max_drop"]
                ),
                line_dash="dot",
                line_color=C["crit"],
                annotation_text="SLO AUC",
            )

        fig.update_layout(
            title="Desempenho por janela (rótulo atrasado)"
        )

        f2 = go.Figure()

        f2.add_scatter(
            x=wm["month"],
            y=wm["default_rate"],
            name="inadimplência real",
            mode="lines+markers",
            line=dict(
                color=C["crit"],
                width=3,
            ),
        )

        f2.add_scatter(
            x=wm["month"],
            y=wm["mean_pd"],
            name="PD média prevista",
            mode="lines+markers",
            line=dict(color=C["a"]),
        )

        f2.update_layout(
            title="Risco real × risco previsto",
            yaxis_tickformat=".1%",
        )

        f3 = go.Figure()

        f3.add_bar(
            x=wm["month"],
            y=wm["expected_loss"],
            name="perda esperada",
            marker_color=C["a"],
        )

        f3.add_bar(
            x=wm["month"],
            y=wm["realized_loss"],
            name="perda realizada",
            marker_color=C["crit"],
        )

        f3.update_layout(
            title=(
                "Impacto de negócio "
                "(carteira aprovada, R$)"
            ),
            barmode="group",
        )

        chal_html = ""

        if chal and not chal.get("skipped"):
            g = "".join(
                (
                    "<li>"
                    f"{'✔' if v else '✘'} "
                    f"{html.escape(k)}"
                    "</li>"
                )
                for k, v in chal["guardrails"].items()
            )

            opts = pd.DataFrame(
                chal.get("response_options", [])
            )

            opt_html = ""

            if len(opts):
                show = opts[
                    [
                        "opcao",
                        "auc",
                        "approval_rate",
                        "recall_default",
                        "bad_rate_approved",
                        "expected_cost",
                        "realized_loss",
                        "min_disparate_impact",
                        "di_faixa_etaria_monitorada",
                    ]
                ].copy()

                for c in (
                    "expected_cost",
                    "realized_loss",
                ):
                    show[c] = show[c].map(
                        lambda v: (
                            f"R$ {v / 1e6:,.1f} mi"
                            .replace(",", "X")
                            .replace(".", ",")
                            .replace("X", ".")
                        )
                    )

                for c in (
                    "approval_rate",
                    "recall_default",
                    "bad_rate_approved",
                ):
                    show[c] = show[c].map(
                        lambda v: f"{v:.1%}"
                    )

                show.columns = [
                    (
                        "Resposta avaliada ("
                        + chal["holdout_window"]
                        + ")"
                    ),
                    "AUC",
                    "Aprovação",
                    "Recall default",
                    "Bad rate aprovados",
                    "Custo esperado",
                    "Perda realizada",
                    "DI gênero (≥0,80)",
                    "DI faixa etária (monitorado)",
                ]

                opt_html = _table(show)

            chal_html = (
                "<h2>Correções: política de resposta</h2>"
                "<div class='grid2'>"
                f"<div class='scroll'>{opt_html}</div>"
                "<div>"
                "<p class='note'>"
                "<b>Recomendação:</b> "
                f"{html.escape(str(chal.get('recommended_response')))}"
                "<br>"
                "Challenger treinado com "
                f"{', '.join(chal['trained_on'])}"
                " (rótulos já disponíveis) e avaliado em "
                f"{chal['holdout_window']}: "
                f"AUC {chal['champion']['auc']}"
                f" → {chal['challenger']['auc']}"
                " · segmento crítico "
                f"{chal['segment_auc_champion']}"
                f" → {chal['segment_auc_challenger']}"
                " · <b>"
                + (
                    "PROMOVIDO a champion"
                    if chal["promoted"]
                    else "NÃO promovido"
                )
                + "</b></p>"
                f"<ul class='g'>{g}</ul>"
                "</div></div>"
            )

        sections.append(
            "<h2>Modelo & negócio</h2>"
            "<div class='grid3'>"
            f"<div>{_fig(fig)}</div>"
            f"<div>{_fig(f2)}</div>"
            f"<div>{_fig(f3)}</div>"
            "</div>"
            f"{chal_html}"
        )

    # ==========================================================
    # ANÁLISE CAUSAL
    # ==========================================================

    if causal:
        pw = pd.DataFrame(
            causal["per_window"]
        )

        fig = go.Figure()

        fig.add_bar(
            x=pw["month"],
            y=pw["efeito_covariate_shift_auc"],
            name="efeito mudança de população",
            marker_color=C["c"],
        )

        fig.add_bar(
            x=pw["month"],
            y=pw["efeito_concept_drift_auc"],
            name="efeito concept drift",
            marker_color=C["crit"],
        )

        fig.update_layout(
            title=(
                "Causalidade: decomposição "
                "da variação de AUC"
            ),
            barmode="relative",
        )

        abl = pd.DataFrame(
            causal["ablation_last_window"]
        )

        f2 = go.Figure(
            go.Bar(
                x=abl["feature"],
                y=abl["delta_pd_media_pp"],
                marker_color=C["b"],
                text=abl["delta_pd_media_pp"],
                textposition="outside",
            )
        )

        f2.update_layout(
            title=(
                "Ablação contrafactual: contribuição "
                "para a PD média (p.p.)"
            )
        )

        sections.append(
            "<h2>Análise causal</h2>"
            "<div class='grid2'>"
            f"<div>{_fig(fig)}</div>"
            f"<div>{_fig(f2)}</div>"
            "</div>"
            "<p class='note'>"
            "Detalhes em "
            "<a href='../causal/attribution.md'>"
            "reports/causal/attribution.md"
            "</a></p>"
        )

    # ==========================================================
    # FAIRNESS
    # ==========================================================

    if len(fair):
        fig = go.Figure()

        for attr, color in zip(
            fair["attribute"].unique(),
            [C["a"], C["b"], C["c"]],
        ):
            d = fair[
                fair["attribute"] == attr
            ]

            fig.add_scatter(
                x=[
                    dict(
                        zip(
                            wm["window"],
                            wm["month"],
                        )
                    ).get(w, w)
                    for w in d["window"]
                ],
                y=d["disparate_impact"],
                name=attr,
                mode="lines+markers",
                line=dict(color=color),
            )

        fig.add_hline(
            y=slo["model"]["fairness_min_disparate_impact"],
            line_dash="dot",
            line_color=C["crit"],
            annotation_text="regra 4/5",
        )

        fig.update_layout(
            title=(
                "Disparate impact por janela "
                "(auditoria — atributos fora do modelo)"
            ),
            yaxis_range=[0, 1.05],
        )

        sections.append(
            "<h2>Fairness</h2>"
            "<div class='grid2'>"
            f"<div>{_fig(fig)}</div>"
            f"<div>{_table(fair.round(4))}</div>"
            "</div>"
        )

    # ==========================================================
    # ALERTAS
    # ==========================================================

    if len(alerts):
        mo = (
            dict(zip(wm["window"], wm["month"]))
            if len(wm)
            else {}
        )

        a = alerts.assign(
            mes=alerts["window"].map(
                lambda w: mo.get(w, w)
            )
        )

        fig = go.Figure()

        for sev in (
            "critical",
            "warning",
            "info",
        ):
            d = a[
                a["severity"] == sev
            ]

            if len(d):
                fig.add_scatter(
                    x=d["mes"],
                    y=d["type"],
                    mode="markers",
                    name=f"{sev} ({PRIOR.get(sev)})",
                    marker=dict(
                        size=14,
                        color=SEV_COLOR[sev],
                        opacity=0.8,
                    ),
                    text=d["title"],
                    hovertemplate=(
                        "%{text}<extra></extra>"
                    ),
                )

        fig.update_layout(
            title="Linha do tempo de alertas",
            height=380,
        )

        tbl = a[
            [
                "mes",
                "priority",
                "severity",
                "type",
                "title",
                "route",
                "runbook",
            ]
        ].iloc[::-1]

        sections.append(
            "<h2>Alertas</h2>"
            "<div class='grid2'>"
            f"<div>{_fig(fig)}</div>"
            "<div class='scroll'>"
            f"{_table(tbl, 60)}"
            "</div></div>"
        )

    # ==========================================================
    # NÍVEL 3: DETALHAMENTO DO DRIFT
    # ==========================================================

    if len(drift):
        lastw = drift["window"].max()

        d = drift[
            drift["window"] == lastw
        ][
            [
                "feature",
                "type",
                "test",
                "psi",
                "effect_size",
                "p_value",
                "significant_bh",
                "drift_detected",
                "severity",
                "importance",
                "impact_score",
            ]
        ]

        sections.append(
            "<h2>Detalhe — drift na última janela</h2>"
            f"{_table(d.round(5), 40)}"
        )

    # ==========================================================
    # EVENTOS RECENTES
    # ==========================================================

    if len(ev):
        recent = ev[
            ev["level"].isin(
                ["WARNING", "ERROR", "CRITICAL"]
            )
        ].tail(25)

        cols = [
            c
            for c in [
                "ts",
                "level",
                "stage",
                "event",
                "window",
                "title",
                "reason",
                "rule",
            ]
            if c in recent.columns
        ]

        sections.append(
            "<h2>Detalhe — eventos recentes (WARNING+)</h2>"
            "<div class='scroll'>"
            f"{_table(recent[cols].iloc[::-1], 25)}"
            "</div>"
        )

    # ==========================================================
    # GERAÇÃO DO HTML
    # ==========================================================

    generated = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    page = f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Credit Scoring — Monitoramento</title>
<script>{get_plotlyjs()}</script>

<style>
body{{
    font-family:Inter,Segoe UI,Arial,sans-serif;
    background:#f6f7fb;
    color:#111827;
    margin:0;
    padding:24px 32px
}}
h1{{margin:0 0 4px}}
h2{{
    margin:28px 0 8px;
    padding-bottom:6px;
    border-bottom:2px solid #e5e7eb
}}
.meta{{
    color:#6b7280;
    margin-bottom:16px
}}
.cards{{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
    gap:14px
}}
.card{{
    background:#fff;
    border-radius:10px;
    padding:14px 16px;
    box-shadow:0 1px 3px rgba(0,0,0,.08)
}}
.ct{{
    font-size:12px;
    text-transform:uppercase;
    color:#6b7280;
    letter-spacing:.04em
}}
.cv{{
    font-size:22px;
    font-weight:700;
    margin:6px 0
}}
.cs{{font-weight:600}}
.sub{{
    font-size:12px;
    color:#6b7280;
    margin-top:4px
}}
.grid2{{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:16px
}}
.grid3{{
    display:grid;
    grid-template-columns:1fr 1fr 1fr;
    gap:16px
}}
.grid2>div,.grid3>div{{
    background:#fff;
    border-radius:10px;
    padding:8px;
    box-shadow:0 1px 3px rgba(0,0,0,.06);
    min-width:0
}}
.tbl{{
    border-collapse:collapse;
    width:100%;
    font-size:12px;
    background:#fff
}}
.tbl th{{
    background:#f3f4f6;
    text-align:left;
    padding:6px
}}
.tbl td{{
    padding:5px 6px;
    border-top:1px solid #eef0f3
}}
.scroll{{
    max-height:420px;
    overflow:auto
}}
.note{{
    font-size:13px;
    color:#374151;
    padding:4px 8px
}}
ul.g{{
    columns:2;
    font-size:13px
}}
code{{
    background:#eef2ff;
    padding:1px 4px;
    border-radius:4px
}}
@media(max-width:1100px){{
    .grid2,.grid3{{
        grid-template-columns:1fr
    }}
}}
</style>
</head>

<body>
<h1>Credit Scoring — Sustentação & Confiabilidade</h1>

<div class="meta">
Modelo
<b>{html.escape(str(meta.get("registered_model", "—")))}</b>
v{meta.get("model_version", "—")}
· limiar PD &lt; {meta.get("threshold", "—")}
· AUC referência {base.get("auc", "—")}
· run <code>{html.escape(str(last_run))}</code>
· gerado em {generated}
</div>

<div class="cards">
{"".join(cards)}
</div>

{"".join(sections)}

<p class="meta" style="margin-top:32px">
Métricas e SLOs: docs/METRICS.md
· Respostas a alertas: docs/RUNBOOK.md
· Governança: README.md
</p>

</body>
</html>
"""

    out.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    out.write_text(
        page,
        encoding="utf-8",
    )

    return out
