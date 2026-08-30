from datetime import date

import pytest

from src.jobs.coletar_b3_isin import (
    B3IsinError,
    normalize_cnpj,
    parse_emissores,
    parse_isins,
)


def csv_bytes(rows):
    return ("\n".join(",".join(f'"{cell}"' for cell in row) for row in rows) + "\n").encode()


def test_normalize_cnpj():
    assert normalize_cnpj("12.345.678/0001-90") == "12345678000190"
    assert normalize_cnpj("123") is None


def test_parse_emissores_requires_large_snapshot(monkeypatch):
    import src.jobs.coletar_b3_isin as mod
    monkeypatch.setattr(mod, "MIN_EMITTERS", 1)
    rows = [["0001", "FUNDO TESTE", "12345678000190", "20260101"]]
    parsed, metrics = parse_emissores(csv_bytes(rows), date(2026, 8, 28))
    assert parsed[0]["codigo_emissor_b3"] == "0001"
    assert parsed[0]["cnpj_emissor_normalizado"] == "12345678000190"
    assert metrics["valid_cnpj"] == 1


def test_parse_isins_maps_position_2_and_3(monkeypatch):
    import src.jobs.coletar_b3_isin as mod
    monkeypatch.setattr(mod, "MIN_ISIN_ROWS", 1)
    monkeypatch.setattr(mod, "MIN_ISIN_TO_ISSUER_RATIO", 0.0)

    emitters = {
        "0001": {
            "codigo_emissor_b3": "0001",
            "nome_emissor": "FUNDO TESTE",
            "cnpj_emissor_normalizado": "12345678000190",
        }
    }
    row = [
        "20260828", "A", "BRTESTCTF001", "0001", "CIOGMY",
        "COTAS", "", "", "", "", "", "BRL",
    ] + [""] * 32 + ["B3"]
    parsed, metrics = parse_isins(
        csv_bytes([row]), emitters, date(2026, 8, 28)
    )
    assert parsed[0]["isin"] == "BRTESTCTF001"
    assert parsed[0]["codigo_emissor_b3"] == "0001"
    assert parsed[0]["moeda"] == "BRL"
    assert metrics["issuer_matches"] == 1


def test_parse_isins_fails_closed_on_invalid_isin(monkeypatch):
    import src.jobs.coletar_b3_isin as mod
    monkeypatch.setattr(mod, "MIN_ISIN_ROWS", 1)
    monkeypatch.setattr(mod, "MIN_ISIN_TO_ISSUER_RATIO", 0.0)

    row = [
        "20260828", "A", "INVALIDO", "0001", "CFI", "DESC",
    ] + [""] * 39
    with pytest.raises(B3IsinError):
        parse_isins(csv_bytes([row]), {}, date(2026, 8, 28))


def test_no_ativos_update_sql():
    import inspect
    import src.jobs.coletar_b3_isin as mod
    source = inspect.getsource(mod).lower()
    assert "update investimento.ativos" not in source
    assert "insert into investimento.ativos" not in source
    assert "delete from investimento.ativos" not in source
