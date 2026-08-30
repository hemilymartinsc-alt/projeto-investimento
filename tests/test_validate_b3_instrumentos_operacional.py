from datetime import date

from src.jobs import validate_b3_instrumentos_operacional as job


def instrumento(ticker="TAXA100", nome_corporativo="TAXA DE FINANCIAMENTO", categoria_b3="SHARES", isin="BRTAXAINDM77", especificacao="ON"):
    return {
        "ticker": ticker,
        "isin": isin,
        "nome_corporativo": nome_corporativo,
        "categoria_b3": categoria_b3,
        "segmento_b3": "CASH",
        "mercado_b3": "EQUITY-CASH",
        "data_inicio_negociacao": date(2020, 1, 1),
        "data_fim_negociacao": None,
        "data_expiracao": None,
        "raw_json": {"SpcfctnCd": especificacao},
    }


def test_taxa_financiamento_nao_e_canonica():
    inst = instrumento()
    assert job.is_financing_rate(inst) is True
    assert job.canonical_decision(inst, date(2026, 8, 30)) == (False, "FINANCING_RATE")


def test_nao_exclui_acao_real_apenas_por_categoria_shares():
    inst = instrumento(ticker="PETR4", nome_corporativo="PETROLEO BRASILEIRO S.A. PETROBRAS", isin="BRPETRACNPR6", especificacao="PN EDJ N2")
    assert job.preliminary_classification(inst) == ("ACAO", None)


def test_cepac_nao_e_acao():
    inst = instrumento(ticker="PMSP11B", nome_corporativo="PREFEITURA MUNICIPAL DE SAO PAULO", isin="BRPMSPCPA000", especificacao="CPA AEP MB")
    assert job.is_cepac(inst) is True
    assert job.preliminary_classification(inst) == ("OUTRO", "CEPAC")




def test_cepac_nao_depende_do_sufixo_11b():
    inst = instrumento(ticker="PMSP12B", nome_corporativo="PREFEITURA MUNICIPAL DE SAO PAULO", isin="BRPMSPCPA018", especificacao="CPA FLI MB")
    assert job.is_cepac(inst) is True
    assert job.preliminary_classification(inst) == ("OUTRO", "CEPAC")


def test_cepac_pendente_futuro_tambem_classifica():
    inst = instrumento(ticker="PMSP14B", nome_corporativo="PREFEITURA MUNICIPAL DE SAO PAULO", isin="BRPMSPCPA034", especificacao="CPA ATZ MB")
    assert job.is_cepac(inst) is True
    assert job.preliminary_classification(inst) == ("OUTRO", "CEPAC")

def test_psvm11_nao_e_acao():
    inst = instrumento(ticker="PSVM11", nome_corporativo="PORTO SUDESTE V.M. S.A.", isin="BRPSVMTRV004", especificacao="TPR")
    assert job.is_royalty_security(inst) is True
    assert job.preliminary_classification(inst) == ("OUTRO", "ROYALTY_SECURITY")


def test_ppla11_e_unit_de_bdrs():
    inst = instrumento(ticker="PPLA11", nome_corporativo="PPLA PARTICIPATIONS LTD.", categoria_b3="UNIT", isin="BRPPLAUNT007", especificacao="UNT")
    assert job.is_bdr_unit(inst) is True
    assert job.preliminary_classification(inst) == ("BDR", "UNIT_BDR")


def test_unit_brasileira_continua_como_acao():
    inst = instrumento(ticker="SANB11", nome_corporativo="BANCO SANTANDER (BRASIL) S.A.", categoria_b3="UNIT", isin="BRSANBCDAM13", especificacao="UNT")
    assert job.preliminary_classification(inst) == ("ACAO", "UNIT")
