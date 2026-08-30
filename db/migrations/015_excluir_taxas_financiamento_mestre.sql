-- Correção de classificação B3: a família técnica TAXA (TAXA DE FINANCIAMENTO)
-- não é ação elegível para o Cadastro Mestre, ainda que a B3 a publique como SHARES.
-- O snapshot bruto oficial permanece preservado.

delete from investimento.ativos a
where a.classe = 'ACAO'
  and a.ticker like 'TAXA%'
  and a.isin = 'BRTAXAINDM77'
  and upper(trim(coalesce(a.nome, ''))) = 'TAXA DE FINANCIAMENTO'
  and not exists (select 1 from investimento.analises_diarias x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.cotacoes_diarias x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.cotacoes_intraday x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.demonstracoes_financeiras x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.fundamentos_snapshot x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.movimentacoes_carteira x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.projecoes x where x.ativo_id = a.id)
  and not exists (select 1 from investimento.proventos x where x.ativo_id = a.id);
