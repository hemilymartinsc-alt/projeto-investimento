from __future__ import annotations

import json

from src.database import connect
from src.jobs.coletar_b3_isin import (
    SOURCE_CODE,
    audit_funds_database,
    baseline_counts,
    check_variation,
    collect_official_data,
    finish_log,
    progress,
    replace_snapshot,
)

PROCESS_NAME = "coletar_b3_isin_apenas_ativos"
MIN_ACTIVE_MASTER = 1000
MIN_ACTIVE_ISIN_COVERAGE = 0.99


class ActiveMasterCoverageError(RuntimeError):
    pass


def load_active_master(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select ticker, isin, classe
            from investimento.ativos
            where instrumento_canonico = true
              and atividade_confirmada_b3 = true
              and isin is not null
              and btrim(isin) <> ''
            order by ticker
            """
        )
        rows = cur.fetchall()

    result = [
        {
            "ticker": row[0],
            "isin": str(row[1]).strip().upper(),
            "classe": row[2],
        }
        for row in rows
    ]
    if len(result) < MIN_ACTIVE_MASTER:
        raise ActiveMasterCoverageError(
            f"mestre ativo abaixo do mínimo: {len(result)} < {MIN_ACTIVE_MASTER}"
        )
    return result


def filter_official_for_active_master(data, active_master):
    target_isins = {row["isin"] for row in active_master}
    all_isins = data["isins"]
    all_emitters = data["emitters"]

    selected_isins = [
        row for row in all_isins
        if row["isin"] in target_isins
    ]
    matched_unique = {row["isin"] for row in selected_isins}
    coverage = len(matched_unique) / len(target_isins)

    if coverage < MIN_ACTIVE_ISIN_COVERAGE:
        missing = sorted(target_isins - matched_unique)[:20]
        raise ActiveMasterCoverageError(
            "cobertura do cadastro ativo B3 abaixo do mínimo: "
            f"{coverage:.4%}; ausentes={missing}"
        )

    issuer_codes = {
        row["codigo_emissor_b3"] for row in selected_isins
    }
    selected_emitters = [
        row for row in all_emitters
        if row["codigo_emissor_b3"] in issuer_codes
    ]

    fund_targets = {
        row["isin"] for row in active_master
        if row["classe"] == "FUNDO"
    }
    fund_matches = matched_unique & fund_targets

    metrics = {
        "ativos_mestre_b3": len(active_master),
        "fundos_mestre_b3": len(fund_targets),
        "isins_ativos_encontrados": len(matched_unique),
        "cobertura_isin_ativos": coverage,
        "linhas_isin_persistidas": len(selected_isins),
        "codigos_emissor_necessarios": len(issuer_codes),
        "linhas_emissor_persistidas": len(selected_emitters),
        "fundos_com_isin_b3": len(fund_matches),
        "isins_ativos_ausentes": sorted(target_isins - matched_unique)[:50],
    }
    return selected_emitters, selected_isins, metrics


def create_log(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo,
                processo,
                status,
                iniciado_em
            )
            values (%s, %s, 'INICIADO', now())
            returning id
            """,
            (SOURCE_CODE, PROCESS_NAME),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def main():
    conn = connect()
    log_id = create_log(conn)
    full_read_count = 0

    try:
        active_master = load_active_master(conn)
        progress(
            "mestre operacional B3 carregado | "
            f"ativos={len(active_master)} "
            f"fundos={sum(1 for x in active_master if x['classe'] == 'FUNDO')}"
        )

        # A B3 distribui o Banco de Dados Completo ISIN em um único pacote.
        # O pacote completo é lido apenas em memória para validação e filtro.
        # Somente os ISINs que já estão ativos no Cadastro Mestre são persistidos.
        data = collect_official_data()
        full_read_count = len(data["emitters"]) + len(data["isins"])

        selected_emitters, selected_isins, filter_metrics = (
            filter_official_for_active_master(data, active_master)
        )

        previous_emitters, previous_isins = baseline_counts(conn)
        check_variation(
            len(selected_emitters),
            previous_emitters,
            "emissores B3 ativos",
        )
        check_variation(
            len(selected_isins),
            previous_isins,
            "ISINs B3 ativos",
        )

        progress(
            "filtro operacional aprovado | "
            f"isins_persistidos={len(selected_isins)} "
            f"emissores_persistidos={len(selected_emitters)}"
        )

        replace_snapshot(conn, selected_emitters, selected_isins)
        db_audit = audit_funds_database(conn)

        metrics = {
            "data_referencia": str(data["reference_date"]),
            "origem_b3_lida_em_memoria": {
                "emissores": data["emitter_metrics"]["rows"],
                "isins": data["isin_metrics"]["rows"],
            },
            "persistencia_apenas_ativos_b3": filter_metrics,
            "auditoria_fundos_banco": db_audit,
        }

        finish_log(
            conn,
            log_id,
            "SUCESSO",
            read_count=full_read_count,
            written_count=len(selected_emitters) + len(selected_isins),
            message=metrics,
        )
        progress(
            "SUCESSO | "
            + json.dumps(metrics, ensure_ascii=False)
        )
    except Exception as exc:
        finish_log(
            conn,
            log_id,
            "ERRO",
            read_count=full_read_count,
            written_count=0,
            message={"erro": str(exc)},
        )
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
