-- Migration 014 — documenta que a camada B3 ISIN persistida é operacional
-- e contém somente instrumentos já confirmados como ativos pela B3 no Cadastro Mestre.
-- O arquivo integral da B3 é usado transitoriamente em memória para localizar e validar
-- os registros; não é persistido nesta camada operacional.

comment on table investimento.b3_isin_cadastro is
    'Snapshot operacional B3 ISIN restrito aos instrumentos canônicos com atividade confirmada pela B3 presentes em investimento.ativos. O catálogo integral B3 é apenas lido em memória durante a coleta.';

comment on table investimento.b3_isin_emissores is
    'Emissores B3 necessários para resolver os ISINs dos instrumentos ativos do Cadastro Mestre. Preserva duplicidades oficiais quando existentes. CNPJ é técnico/interno e não deve ser exibido no Power BI.';

comment on view investimento.vw_b3_isin_ponte_unica is
    'Ponte fail-closed ISIN -> código emissor -> CNPJ para instrumentos ativos do Cadastro Mestre. Exclui qualquer ambiguidade de emissor/CNPJ.';
