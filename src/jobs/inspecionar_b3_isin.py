from __future__ import annotations

import json

from src.jobs.coletar_b3_isin import collect_official_data


def main():
    data = collect_official_data()
    result = {
        "data_referencia": str(data["reference_date"]),
        "emissores": data["emitter_metrics"],
        "isins": data["isin_metrics"],
        "auditoria": data["audit"],
        "observacao": (
            "Somente leitura. Nenhuma tabela do banco é acessada "
            "ou alterada por esta inspeção."
        ),
    }
    print(
        "B3 ISIN auditoria: "
        + json.dumps(result, ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
