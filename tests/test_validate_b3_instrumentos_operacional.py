from datetime import date

from src.jobs import validate_b3_instrumentos_operacional as job

def instrumento(ticker="TAXA100", nome_corporativo="TAXA DE FINANCIAMENTO", categoria_b3="SHARES"):
    return {
        "ticker": ticker,
        "isin": "BRTAXAINDM77",
        "nome_corporativo": nome_corporativo,
        "categoria_b3": categoria_b3,
        "segmento_b3": "CASH",
        "mercado_b3": "EQUITY-CASH",
        "data_inicio_negociacao": date(2020, 1, 1),
        "data_fim_negociacao": None,
        "data_expiracao": None,
    }

def test_taxa_financiamento_nao_e_canonica():
    inst = instrumento()
    assert job.is_financing_rate(inst) is True
    assert job.canonical_decision(inst, date(2026, 8, 30)) == (False, "FINANCING_RATE")

def test_nao_exclui_acao_real_apenas_por_categoria_shares():
    inst = instrumento(ticker="PETR4", nome_corporativo="PETROLEO BRASILEIRO S.A. PETROBRAS")
    inst["isin"] = "BRPETRACNPR6"
    assert job.is_financing_rate(inst) is False
    assert job.canonical_decision(inst, date(2026, 8, 30))[0] is True

def test_exige_nome_oficial_e_prefixo_taxa():
    assert job.is_financing_rate(instrumento(nome_corporativo="OUTRA COMPANHIA")) is False
    assert job.is_financing_rate(instrumento(ticker="ABCD3")) is False
