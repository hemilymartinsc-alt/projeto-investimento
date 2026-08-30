from __future__ import annotations

import json

from src.jobs.coletar_b3_isin import (
    AUDIT_TICKERS,
    download_official_zip,
    extract_official_payloads,
    parse_emissores,
    parse_isins,
)


def main():
    zip_bytes, reference_date = download_official_zip()
    emissor_bytes, numeraca_bytes = extract_official_payloads(zip_bytes)

    emitters, emitter_metrics = parse_emissores(
        emissor_bytes, reference_date
    )
    emitters_by_code = {
        row["codigo_emissor_b3"]: row for row in emitters
    }
    isins, isin_metrics = parse_isins(
        numeraca_bytes, emitters_by_code, reference_date
    )
    isins_by_value = {row["isin"]: row for row in isins}

    examples = {}
    target_isins = {
        "HGLG11": "BRHGLGCTF004",
        "KNRI11": "BRKNRICTF007",
        "AFHI11": "BRAFHICTF005",
        "AAGR11": "BRAAGRCTF017",
        "AAZQ11": "BRAAZQCTF001",
    }
    for ticker in AUDIT_TICKERS:
        isin = target_isins[ticker]
        item = isins_by_value.get(isin)
        issuer = emitters_by_code.get(
            item["codigo_emissor_b3"] if item else ""
        )
        examples[ticker] = {
            "isin": isin,
            "isin_encontrado": item is not None,
            "codigo_emissor_b3": (
                item["codigo_emissor_b3"] if item else None
            ),
            "emissor_encontrado": issuer is not None,
            "nome_emissor": issuer["nome_emissor"] if issuer else None,
            "possui_cnpj_b3": bool(
                issuer and issuer["cnpj_emissor_normalizado"]
            ),
        }

    result = {
        "data_referencia": str(reference_date),
        "emissores": emitter_metrics,
        "isins": isin_metrics,
        "exemplos": examples,
        "observacao": (
            "Inspeção somente leitura. Nenhuma tabela ou registro é alterado."
        ),
    }
    print("B3 ISIN auditoria: " + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
