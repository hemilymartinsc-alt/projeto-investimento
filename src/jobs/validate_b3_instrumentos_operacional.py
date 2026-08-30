from __future__ import annotations

from src.jobs import validate_b3_instrumentos as base

FINANCING_RATE_NAME = "TAXA DE FINANCIAMENTO"
FINANCING_RATE_VARIANT = "FINANCING_RATE"

_original_canonical_decision = base.canonical_decision
_original_load_latest_valid_snapshot_profile = base.load_latest_valid_snapshot_profile

def is_financing_rate(inst) -> bool:
    """Reconhece a família técnica TAXA da B3 sem usar ticker isoladamente."""
    ticker = base.upper(inst.get("ticker")) or ""
    return bool(
        ticker.startswith("TAXA")
        and base.norm(inst.get("categoria_b3")) == "SHARES"
        and base.norm(inst.get("nome_corporativo")) == FINANCING_RATE_NAME
    )

def canonical_decision(inst, ref, fixed_income_etf_keys=frozenset()):
    if is_financing_rate(inst):
        return False, FINANCING_RATE_VARIANT
    return _original_canonical_decision(inst, ref, fixed_income_etf_keys)

def _previous_financing_rate_counts(conn):
    """Conta TAXA que ainda conste como canônica no snapshot anterior legado."""
    with conn.cursor() as cur:
        cur.execute(
            """
            with ultima_referencia as (
                select max(data_referencia) as data_referencia
                from investimento.b3_instrumentos_snapshot
                where status_arquivo = 'Final'
            )
            select
                count(*) filter (where s.instrumento_canonico = true) as canonicos,
                count(*) filter (
                    where s.instrumento_canonico = true
                      and nullif(s.data_inicio_negociacao, date '9999-12-31') is not null
                      and nullif(s.data_inicio_negociacao, date '9999-12-31') <= s.data_referencia
                      and (
                          nullif(s.data_fim_negociacao, date '9999-12-31') is null
                          or nullif(s.data_fim_negociacao, date '9999-12-31') >= s.data_referencia
                      )
                      and (
                          nullif(s.data_expiracao, date '9999-12-31') is null
                          or nullif(s.data_expiracao, date '9999-12-31') >= s.data_referencia
                      )
                ) as confirmados
            from investimento.b3_instrumentos_snapshot s
            join ultima_referencia u
              on u.data_referencia = s.data_referencia
            where s.status_arquivo = 'Final'
              and upper(trim(s.ticker)) like 'TAXA%'
              and upper(trim(coalesce(s.categoria_b3, ''))) = 'SHARES'
              and upper(trim(coalesce(s.nome_corporativo, ''))) = 'TAXA DE FINANCIAMENTO'
            """
        )
        row = cur.fetchone() or (0, 0)
    return int(row[0] or 0), int(row[1] or 0)

def load_latest_valid_snapshot_profile(conn):
    profile = _original_load_latest_valid_snapshot_profile(conn)
    if profile is None:
        return None
    canonical_taxa, confirmed_taxa = _previous_financing_rate_counts(conn)
    if not canonical_taxa and not confirmed_taxa:
        return profile
    adjusted = dict(profile)
    adjusted["total_canonicos"] = max(0, adjusted["total_canonicos"] - canonical_taxa)
    adjusted["total_canonicos_confirmados"] = max(0, adjusted["total_canonicos_confirmados"] - confirmed_taxa)
    classes = dict(adjusted["canonicos_confirmados_por_classe"])
    classes["ACAO"] = max(0, classes.get("ACAO", 0) - confirmed_taxa)
    adjusted["canonicos_confirmados_por_classe"] = classes
    adjusted["ajuste_baseline_taxa_financiamento"] = {
        "canonicos_excluidos": canonical_taxa,
        "confirmados_excluidos": confirmed_taxa,
    }
    return adjusted

def main():
    base.canonical_decision = canonical_decision
    base.load_latest_valid_snapshot_profile = load_latest_valid_snapshot_profile
    return base.main()

if __name__ == "__main__":
    raise SystemExit(main())
