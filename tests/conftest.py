import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def isolated_root(tmp_path_factory):
    """Executa os testes num diretório isolado (configs/contratos copiados) — nunca toca nos dados reais."""
    tmp = tmp_path_factory.mktemp("driftguard_root")
    for d in ("configs", "contracts"):
        shutil.copytree(ROOT / d, tmp / d)
    os.environ["DRIFTGUARD_ROOT"] = str(tmp)
    os.environ["DRIFTGUARD_CONSOLE_LEVEL"] = "CRITICAL"
    os.environ["DRIFTGUARD_PSEUDONYM_SALT"] = "test-salt-not-secret"
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{(tmp / 'mlflow.db').as_posix()}"
    yield tmp


@pytest.fixture()
def slo():
    from driftguard.settings import get_settings

    return get_settings().slo
