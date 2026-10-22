"""Envio opcional das métricas para um Prometheus Pushgateway (DRIFTGUARD_PUSHGATEWAY_URL)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry


def push(registry: CollectorRegistry, job: str) -> bool:
    from driftguard.adapters import note_skipped
    from driftguard.settings import get_settings

    url = get_settings().pushgateway_url
    if not url:
        return False
    try:
        from prometheus_client import push_to_gateway

        push_to_gateway(url, job=job, registry=registry, timeout=5)
        return True
    except Exception as exc:  # gateway fora do ar não pode derrubar o pipeline
        note_skipped("pushgateway", f"falha ao enviar para {url}: {type(exc).__name__}")
        return False
