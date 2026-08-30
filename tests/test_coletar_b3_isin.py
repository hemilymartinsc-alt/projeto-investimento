from datetime import date
import inspect

import pytest

from src.jobs.coletar_b3_isin import (
    B3IsinError,
    normalize_cnpj,
    parse_emissores,
    parse_isins,
    unique_bridge_for,
)


def csv_bytes(rows):
    lines = []
    for row in rows:
        escaped = [
            '"' + str(cell).replace('"', '""') + '"'
            for cell in row
        ]
        lines.append(",".join(escaped))
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_normalize_cnpj():
    assert (
        normalize_cnpj("12.345.678/0001-90")
        == "12345678000190"
    )
    assert normalize_cnpj("123") is None


def test_duplicate_issuer_code_is_preserved(monkeypatch):
    import src.jobs.coletar_b3_isin as mod

    monkeypatch.setattr(mod, "MIN_EMITTERS", 1)
    monkeypatch.setattr(mod, "MIN_VALID_CNPJ_RATIO", 0.0)

    rows = [
        ["AGMO", "FUNDO A", "12345678000190", "20260101"],
        ["AGMO", "FUNDO A", "12345678000190", "20260102"],
    ]
    parsed, by_code, metrics = parse_emissores(
        csv_bytes(rows), date(2026, 8, 28)
    )

    assert len(parsed) == 2
    assert len(by_code["AGMO"]) == 2
    assert metrics["duplicate_codes"] == 1
    assert metrics["codes_with_cnpj_conflict"] == 0


def test_duplicate_issuer_code_cnpj_conflict_detected(monkeypatch):
    import src.jobs.coletar_b3_isin as mod

    monkeypatch.setattr(mod, "MIN_EMITTERS", 1)
    monkeypatch.setattr(mod, "MIN_VALID_CNPJ_RATIO", 0.0)

    rows = [
        ["AGMO", "FUNDO A", "12345678000190", "20260101"],
        ["AGMO", "FUNDO B", "98765432000199", "20260102"],
    ]
    _, by_code, metrics = parse_emissores(
        csv_bytes(rows), date(2026, 8, 28)
    )

    assert len(by_code["AGMO"]) == 2
    assert metrics["codes_with_cnpj_conflict"] == 1


def make_isin_row(isin="BRTESTCTF001", issuer="0001"):
    return [
        "20260828",
        "A",
        isin,
        issuer,
        "CIOGMY",
        "COTAS",
        "",
        "",
        "",
        "",
        "",
        "BRL",
    ] + [""] * 32 + ["B3"]


def test_parse_isin_position_2_and_3(monkeypatch):
    import src.jobs.coletar_b3_isin as mod

    monkeypatch.setattr(mod, "MIN_ISIN_ROWS", 1)
    monkeypatch.setattr(
        mod, "MIN_ISIN_TO_ISSUER_RATIO", 0.0
    )

    parsed, by_isin, metrics = parse_isins(
        csv_bytes([make_isin_row()]),
        {"0001"},
        date(2026, 8, 28),
    )

    assert parsed[0]["isin"] == "BRTESTCTF001"
    assert parsed[0]["codigo_emissor_b3"] == "0001"
    assert parsed[0]["moeda"] == "BRL"
    assert len(by_isin["BRTESTCTF001"]) == 1
    assert metrics["issuer_matches"] == 1


def test_unique_bridge_accepts_identical_duplicate_emitter():
    by_isin = {
        "BRTESTCTF001": [
            {
                "isin": "BRTESTCTF001",
                "codigo_emissor_b3": "AGMO",
            }
        ]
    }
    by_code = {
        "AGMO": [
            {
                "cnpj_emissor_normalizado": "12345678000190",
                "nome_emissor": "FUNDO A",
            },
            {
                "cnpj_emissor_normalizado": "12345678000190",
                "nome_emissor": "FUNDO A",
            },
        ]
    }

    result = unique_bridge_for(
        "BRTESTCTF001", by_isin, by_code
    )
    assert result["status"] == "PONTE_UNIVOCA"


def test_unique_bridge_blocks_conflicting_cnpj():
    by_isin = {
        "BRTESTCTF001": [
            {
                "isin": "BRTESTCTF001",
                "codigo_emissor_b3": "AGMO",
            }
        ]
    }
    by_code = {
        "AGMO": [
            {
                "cnpj_emissor_normalizado": "12345678000190",
                "nome_emissor": "FUNDO A",
            },
            {
                "cnpj_emissor_normalizado": "98765432000199",
                "nome_emissor": "FUNDO B",
            },
        ]
    }

    result = unique_bridge_for(
        "BRTESTCTF001", by_isin, by_code
    )
    assert result["status"] == "AMBIGUO_EMISSOR"


def test_invalid_isin_fails_closed(monkeypatch):
    import src.jobs.coletar_b3_isin as mod

    monkeypatch.setattr(mod, "MIN_ISIN_ROWS", 1)
    monkeypatch.setattr(
        mod, "MIN_ISIN_TO_ISSUER_RATIO", 0.0
    )

    with pytest.raises(B3IsinError):
        parse_isins(
            csv_bytes([make_isin_row(isin="INVALIDO")]),
            {"0001"},
            date(2026, 8, 28),
        )


def test_collector_does_not_write_master_assets():
    import src.jobs.coletar_b3_isin as mod

    source = inspect.getsource(mod).lower()
    forbidden = (
        "update investimento.ativos",
        "insert into investimento.ativos",
        "delete from investimento.ativos",
        "truncate investimento.ativos",
    )
    assert not any(term in source for term in forbidden)


def test_collection_log_uses_valid_status():
    import src.jobs.coletar_b3_isin as mod

    source = inspect.getsource(mod)
    assert "'INICIADO'" in source
    assert "EM_EXECUCAO" not in source
