from src.jobs.coletar_b3_programas_bdr import (
    parse_page,
    parse_total,
    validate_variation,
)


HTML = """
<html><body>
<table>
<thead><tr><th>Empresa</th><th>Código de Negociação</th><th>Ações</th></tr></thead>
<tbody>
<tr>
<td>Abbott Laboratories</td>
<td>ABTT34</td>
<td><a href="https://www.abbott.com">Ir para o Site</a>
<a href="/arquivo/abbott.pdf">Visualizar arquivo</a></td>
</tr>
<tr>
<td>Empresa Restrita</td>
<td>ZZZZ34*</td>
<td><a href="https://example.com">Ir para o Site</a>
<a href="/arquivo/restrita.pdf">Visualizar arquivo</a></td>
</tr>
</tbody>
</table>
<div>Exibindo 1 - 2 de 2 resultados.</div>
</body></html>
"""


def test_parse_total():
    assert parse_total(HTML) == 2


def test_parse_rows():
    rows = parse_page(
        "NAO_PATROCINADO",
        "https://finservices.b3.com.br/bdr-nao-patrocinado/programas?delta=60",
        HTML,
        1,
    )
    assert len(rows) == 2
    assert rows[0].ticker == "ABTT34"
    assert rows[0].nome_programa == "Abbott Laboratories"
    assert rows[0].url_site_emissor == "https://www.abbott.com"
    assert rows[0].url_documento_b3.endswith("/arquivo/abbott.pdf")
    assert rows[1].ticker == "ZZZZ34"
    assert rows[1].restrito_qualificados is True


def test_variacao_aceita_limite():
    validate_variation(
        {"NAO_PATROCINADO": 790, "ETF": 310},
        {"NAO_PATROCINADO": 780, "ETF": 300},
    )


def test_variacao_rejeita_queda_grande():
    try:
        validate_variation(
            {"NAO_PATROCINADO": 500, "ETF": 310},
            {"NAO_PATROCINADO": 790, "ETF": 310},
        )
    except RuntimeError as exc:
        assert "sanidade de variação falhou" in str(exc)
    else:
        raise AssertionError("deveria rejeitar variação acima do limite")
