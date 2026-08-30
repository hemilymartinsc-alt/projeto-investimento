import pytest

from src.jobs.coletar_b3_isin_ativos import (
    ActiveMasterCoverageError,
    filter_official_for_active_master,
)


def test_filter_persists_only_active_master(monkeypatch):
    import src.jobs.coletar_b3_isin_ativos as mod
    monkeypatch.setattr(mod, "MIN_ACTIVE_ISIN_COVERAGE", 0.99)

    active = [
        {"ticker": "AAA11", "isin": "BRAAAACTF001", "classe": "FUNDO"},
        {"ticker": "BBB3", "isin": "BRBBBBACNOR1", "classe": "ACAO"},
    ]
    data = {
        "isins": [
            {"isin": "BRAAAACTF001", "codigo_emissor_b3": "AAAA"},
            {"isin": "BRBBBBACNOR1", "codigo_emissor_b3": "BBBB"},
            {"isin": "BRCCTTACNOR1", "codigo_emissor_b3": "CCCC"},
        ],
        "emitters": [
            {"codigo_emissor_b3": "AAAA"},
            {"codigo_emissor_b3": "BBBB"},
            {"codigo_emissor_b3": "CCCC"},
        ],
    }

    emitters, isins, metrics = filter_official_for_active_master(data, active)

    assert {x["isin"] for x in isins} == {"BRAAAACTF001", "BRBBBBACNOR1"}
    assert {x["codigo_emissor_b3"] for x in emitters} == {"AAAA", "BBBB"}
    assert metrics["ativos_mestre_b3"] == 2
    assert metrics["fundos_mestre_b3"] == 1
    assert metrics["isins_ativos_encontrados"] == 2


def test_filter_fails_closed_if_active_isin_missing(monkeypatch):
    import src.jobs.coletar_b3_isin_ativos as mod
    monkeypatch.setattr(mod, "MIN_ACTIVE_ISIN_COVERAGE", 1.0)

    active = [
        {"ticker": "AAA11", "isin": "BRAAAACTF001", "classe": "FUNDO"},
        {"ticker": "BBB3", "isin": "BRBBBBACNOR1", "classe": "ACAO"},
    ]
    data = {
        "isins": [
            {"isin": "BRAAAACTF001", "codigo_emissor_b3": "AAAA"},
        ],
        "emitters": [
            {"codigo_emissor_b3": "AAAA"},
        ],
    }

    with pytest.raises(ActiveMasterCoverageError):
        filter_official_for_active_master(data, active)
