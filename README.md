# Projeto de Investimento

Backend de dados do assistente pessoal de investimentos.

## Arquitetura

Fontes oficiais e operacionais → Python/GitHub Actions → PostgreSQL (Supabase) → Power BI.

### Prioridade de fontes

1. CVM, B3, Banco Central, IBGE e outras fontes oficiais.
2. Documentos oficiais de empresas e fundos.
3. APIs de mercado apenas como camada operacional quando a fonte oficial não for prática para atualização intraday.

## Fase atual

Primeiro pipeline funcional:

- coleta o cadastro oficial de companhias abertas da CVM;
- normaliza os campos necessários;
- grava/upserta em `investimento.cvm_cadastro_companhias`;
- registra o resultado em `investimento.coletas_log`.

Fonte oficial:
`https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv`

## Configuração local

1. Crie um ambiente virtual.
2. Instale `requirements.txt`.
3. Copie `.env.example` para `.env`.
4. Em `DATABASE_URL`, use a connection string do **Session pooler** do Supabase.
5. Execute:

```bash
python -m src.jobs.collect_cvm_cadastro
```

## GitHub Actions

O workflow `.github/workflows/cvm-cadastro.yml` roda:

- manualmente (`workflow_dispatch`);
- automaticamente em dias úteis.

Antes de executar no GitHub, crie o secret:

`DATABASE_URL`

em:

**Settings → Secrets and variables → Actions → New repository secret**

Nunca grave a senha do banco no código ou em arquivos versionados.
