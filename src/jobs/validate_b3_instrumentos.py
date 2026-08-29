from __future__ import annotations

import csv
import io
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from src.database import connect

SOURCE_CODE = "B3_INSTRUMENTS"
PROCESS_NAME = "validar_classificar_instrumentos_b3"
REQUEST_URL = "https://arquivos.b3.com.br/api/download/requestname"
DOWNLOAD_URL = "https://arquivos.b3.com.br/api/download/"
TZ_BR = ZoneInfo("America/Sao_Paulo")
ISIN_RE = re.compile(r"^[A-Z0-9]{12}$")


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def upper(v):
    s = clean(v)
    return s.upper() if s else None


def norm(v):
    s = upper(v) or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(v):
    s = clean(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def pick(row, *keys):
    for k in keys:
        if k in row and clean(row[k]):
            return clean(row[k])
    return None


def parse_csv(content: bytes):
    text = content.decode("iso-8859-1", errors="replace")
    lines = text.splitlines()
    status = None
    header = None
    for i, line in enumerate(lines):
        n = norm(line)
        if "STATUS DO ARQUIVO" in n:
            status = "Final" if "FINAL" in n else ("Parcial" if "PARCIAL" in n else None)
        fields = [x.strip() for x in next(csv.reader([line], delimiter=";"))]
        if "TckrSymb" in fields and "ISIN" in fields:
            header = i
            break
    if header is None:
        raise RuntimeError("Cabeçalho TckrSymb/ISIN não encontrado no arquivo B3.")
    reader = csv.DictReader(io.StringIO("\n".join(lines[header:])), delimiter=";")
    return status, [dict(r) for r in reader if upper(r.get("TckrSymb"))]


def download_latest():
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 projeto-investimento/1.0",
        "Referer": "https://arquivos.b3.com.br/",
    })
    today = datetime.now(TZ_BR).date()
    errors = []
    for back in range(11):
        ref = today - timedelta(days=back)
        try:
            r = sess.get(REQUEST_URL, params={"fileName": "InstrumentsConsolidated", "date": ref.isoformat()}, timeout=45)
            if r.status_code in (400, 404):
                continue
            r.raise_for_status()
            token = r.json().get("token")
            if not token:
                continue
            f = sess.get(DOWNLOAD_URL, params={"token": token}, timeout=90)
            f.raise_for_status()
            status, rows = parse_csv(f.content)
            if status == "Parcial":
                continue
            if rows:
                return ref, status, rows
        except Exception as exc:
            errors.append(f"{ref}: {exc}")
    raise RuntimeError("Não foi possível obter arquivo final recente da B3. " + "; ".join(errors[-3:]))


def normalize(row, ref, status):
    return {
        "data_referencia": parse_date(pick(row, "RptDt", "ReportDate")) or ref,
        "ticker": upper(pick(row, "TckrSymb", "TickerSymbol")),
        "isin": upper(pick(row, "ISIN")),
        "ativo_base": clean(pick(row, "Asst", "Asset")),
        "descricao_ativo": clean(pick(row, "AsstDesc", "AssetDescription")),
        "segmento_b3": clean(pick(row, "SgmtNm", "SegmentName")),
        "mercado_b3": clean(pick(row, "MktNm", "MarketName")),
        "categoria_b3": clean(pick(row, "SctyCtgyNm", "SecurityCategoryName")),
        "descricao_b3": clean(pick(row, "Desc", "Description")),
        "cfi_code": clean(pick(row, "CFICd", "CFICode")),
        "moeda": upper(pick(row, "TrdgCcy", "TradingCurrency")),
        "nome_corporativo": clean(pick(row, "CrpnNm", "CorporateName", "CorpName")),
        "nivel_governanca": clean(pick(row, "CorpGovnLvlNm", "CorpGovnLvlNam", "CorporateGovernanceLevelName")),
        "data_inicio_negociacao": parse_date(pick(row, "TrdgStartDt", "TradingStartDate")),
        "data_fim_negociacao": parse_date(pick(row, "TrdgEndDt", "TradingEndDate")),
        "data_expiracao": parse_date(pick(row, "XprtnDt", "ExpirationDate")),
        "status_arquivo": status,
        "raw_json": row,
    }


def text_of(inst):
    fields = ("categoria_b3", "descricao_b3", "descricao_ativo", "segmento_b3", "mercado_b3", "nome_corporativo", "cfi_code")
    return " | ".join(norm(inst.get(k)) for k in fields if inst.get(k))


def derivative(inst):
    t = text_of(inst)
    return any(x in t for x in ("OPTION", "OPCAO", "FUTURE", "FUTURO", "FORWARD", "TERMO", "SWAP", "SECURITY LENDING", "EMPRESTIMO", "EXERCISE", "EXERCICIO"))


def classify(inst):
    t = text_of(inst)
    if "BDR" in t or "BRAZILIAN DEPOSITARY" in t:
        return "BDR", ("ETF_BDR" if "ETF" in t else None)
    if any(x in t for x in ("FUNDO IMOBILI", "REAL ESTATE FUND", " FII ", "FII|", "|FII")):
        return "FII", None
    if "ETF" in t or "EXCHANGE TRADED FUND" in t:
        return "ETF", None
    if "ETP" in t or "EXCHANGE TRADED PRODUCT" in t:
        return "ETP", None
    if any(x in t for x in ("ORDINARY SHARES", "PREFERRED SHARES", "COMMON SHARES", "ACAO ORDINARIA", "ACAO PREFERENCIAL", "ACOES ORDINARIAS", "ACOES PREFERENCIAIS", "STOCK", "SHARES")):
        if "UNIT" in t:
            return "ACAO", "UNIT"
        if "PREFERRED" in t or "PREFERENCIAL" in t:
            return "ACAO", "PN"
        if "ORDINARY" in t or "ORDINARIA" in t:
            return "ACAO", "ON"
        return "ACAO", None
    if any(x in t for x in ("FIXED INCOME", "RENDA FIXA", "DEBENTURE", "BOND")):
        return "RENDA_FIXA", None
    if "FUND" in t or "FUNDO" in t:
        return "FUNDO", None
    return "OUTRO", None


def current(inst, ref):
    end = inst.get("data_fim_negociacao")
    exp = inst.get("data_expiracao")
    return not ((end and end < ref) or (exp and exp < ref))


def candidate(inst, ref):
    isin = inst.get("isin")
    return bool(inst.get("ticker") and isin and ISIN_RE.fullmatch(isin) and current(inst, ref) and not derivative(inst))


def save_snapshot(conn, instruments, ref):
    sql = """
    insert into investimento.b3_instrumentos_snapshot (
      data_referencia,ticker,isin,ativo_base,descricao_ativo,segmento_b3,mercado_b3,
      categoria_b3,descricao_b3,cfi_code,moeda,nome_corporativo,nivel_governanca,
      data_inicio_negociacao,data_fim_negociacao,data_expiracao,status_arquivo,raw_json
    ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
    """
    rows = []
    for i in instruments:
        if not i.get("ticker") or not i.get("isin"):
            continue
        rows.append((i["data_referencia"],i["ticker"],i["isin"],i.get("ativo_base"),i.get("descricao_ativo"),i.get("segmento_b3"),i.get("mercado_b3"),i.get("categoria_b3"),i.get("descricao_b3"),i.get("cfi_code"),i.get("moeda"),i.get("nome_corporativo"),i.get("nivel_governanca"),i.get("data_inicio_negociacao"),i.get("data_fim_negociacao"),i.get("data_expiracao"),i.get("status_arquivo"),json.dumps(i.get("raw_json") or {}, ensure_ascii=False)))
    with conn.cursor() as cur:
        cur.execute("delete from investimento.b3_instrumentos_snapshot where data_referencia=%s", (ref,))
        if rows:
            cur.executemany(sql, rows)
    return len(rows)


def load_existing(conn):
    with conn.cursor() as cur:
        cur.execute("select ticker,isin from investimento.ativos")
        return {r[0].upper(): upper(r[1]) for r in cur.fetchall()}


def best(rows, target_isin=None):
    if target_isin:
        exact = [r for r in rows if upper(r.get("isin")) == target_isin]
        if exact:
            rows = exact
    def score(i):
        t = text_of(i)
        return (10 if not derivative(i) else 0) + (5 if any(x in t for x in ("SPOT", "CASH", "VISTA")) else 0) + (1 if i.get("categoria_b3") else 0)
    return max(rows, key=score) if rows else None


def validate_master(conn, instruments, ref):
    existing = load_existing(conn)
    by_ticker = {}
    for i in instruments:
        by_ticker.setdefault(i["ticker"], []).append(i)

    candidates = {}
    for ticker, rows in by_ticker.items():
        valid = [r for r in rows if candidate(r, ref)]
        if valid:
            candidates[ticker] = best(valid, existing.get(ticker))

    counts = {"candidatos": len(candidates), "novos": 0, "validados": 0, "divergentes": 0, "inativos": 0, "sem_isin": 0, "duvidosos": 0}
    with conn.cursor() as cur:
        for ticker, inst in candidates.items():
            isin = upper(inst["isin"])
            old_isin = existing.get(ticker)
            if old_isin and old_isin != isin:
                cur.execute("""
                    update investimento.ativos set ativo=false,elegivel_analise=false,status_validacao='DIVERGENTE',
                    motivo_exclusao='Ticker encontrado na B3 com ISIN diferente do cadastro mestre.',
                    fonte_validacao=%s,validado_em=now(),atualizado_em=now() where ticker=%s
                """, (SOURCE_CODE, ticker))
                counts["divergentes"] += 1
                continue

            classe, subclasse = classify(inst)
            nome = inst.get("nome_corporativo") or inst.get("descricao_ativo") or inst.get("descricao_b3") or ticker
            if ticker in existing:
                cur.execute("""
                    update investimento.ativos set
                      nome=coalesce(%s,nome), classe=%s, subclasse=%s, tipo_instrumento=%s,
                      categoria_b3=%s, segmento_b3=%s, mercado_b3=%s, moeda=coalesce(%s,moeda),
                      isin=%s, ativo=true, elegivel_analise=true, status_validacao='VALIDADO_B3',
                      motivo_exclusao=null, fonte_validacao=%s, validado_em=now(), atualizado_em=now()
                    where ticker=%s
                """, (nome,classe,subclasse,inst.get("descricao_b3"),inst.get("categoria_b3"),inst.get("segmento_b3"),inst.get("mercado_b3"),inst.get("moeda"),isin,SOURCE_CODE,ticker))
            else:
                cur.execute("""
                    insert into investimento.ativos (
                      ticker,nome,nome_pregao,classe,subclasse,tipo_instrumento,moeda,ativo,isin,
                      fonte_cadastro,url_fonte,categoria_b3,segmento_b3,mercado_b3,status_validacao,
                      elegivel_analise,fonte_validacao,validado_em,atualizado_em
                    ) values (%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,'VALIDADO_B3',true,%s,now(),now())
                """, (ticker,nome,inst.get("ativo_base"),classe,subclasse,inst.get("descricao_b3"),inst.get("moeda") or "BRL",isin,SOURCE_CODE,"https://arquivos.b3.com.br/",inst.get("categoria_b3"),inst.get("segmento_b3"),inst.get("mercado_b3"),SOURCE_CODE))
                counts["novos"] += 1
            counts["validados"] += 1

        for ticker, old_isin in existing.items():
            if not old_isin:
                cur.execute("""update investimento.ativos set ativo=false,elegivel_analise=false,status_validacao='SEM_ISIN',motivo_exclusao='Ativo sem ISIN válido.',fonte_validacao=%s,validado_em=now(),atualizado_em=now() where ticker=%s""", (SOURCE_CODE,ticker))
                counts["sem_isin"] += 1
                continue
            if ticker in candidates:
                continue
            exact = any(upper(r.get("isin")) == old_isin and current(r, ref) for r in by_ticker.get(ticker, []))
            if exact:
                cur.execute("""update investimento.ativos set ativo=false,elegivel_analise=false,status_validacao='DUVIDOSO',motivo_exclusao='Instrumento oficial B3 fora do universo de carteira suportado nesta fase.',fonte_validacao=%s,validado_em=now(),atualizado_em=now() where ticker=%s""", (SOURCE_CODE,ticker))
                counts["duvidosos"] += 1
            else:
                cur.execute("""update investimento.ativos set ativo=false,elegivel_analise=false,status_validacao='INATIVO',motivo_exclusao='Ticker/ISIN não encontrado como instrumento corrente no último cadastro oficial da B3.',fonte_validacao=%s,validado_em=now(),atualizado_em=now() where ticker=%s""", (SOURCE_CODE,ticker))
                counts["inativos"] += 1
    return counts


def log_start(conn):
    with conn.cursor() as cur:
        cur.execute("insert into investimento.coletas_log (fonte_codigo,processo,status) values (%s,%s,'INICIADO') returning id", (SOURCE_CODE,PROCESS_NAME))
        x = cur.fetchone()[0]
    conn.commit()
    return x


def log_end(conn, log_id, status, result, message):
    with conn.cursor() as cur:
        cur.execute("""update investimento.coletas_log set finalizado_em=now(),status=%s,registros_lidos=%s,registros_gravados=%s,mensagem=%s where id=%s""", (status, (result or {}).get("linhas_arquivo"), (result or {}).get("snapshot",0), message[:1500], log_id))
    conn.commit()


def main():
    conn = connect()
    log_id = log_start(conn)
    try:
        ref, status, raw = download_latest()
        instruments = [normalize(r, ref, status) for r in raw]
        with_isin = [i for i in instruments if i.get("isin") and ISIN_RE.fullmatch(i["isin"])]
        snap = save_snapshot(conn, with_isin, ref)
        counts = validate_master(conn, with_isin, ref)
        conn.commit()
        result = {"data_referencia": str(ref), "status_arquivo": status, "linhas_arquivo": len(raw), "linhas_com_isin": len(with_isin), "snapshot": snap, **counts}
        msg = json.dumps(result, ensure_ascii=False, sort_keys=True)
        log_end(conn, log_id, "SUCESSO", result, msg)
        print(msg)
        return 0
    except Exception as exc:
        conn.rollback()
        try:
            log_end(conn, log_id, "ERRO", None, str(exc))
        finally:
            print(f"B3 instrumentos: erro | {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
