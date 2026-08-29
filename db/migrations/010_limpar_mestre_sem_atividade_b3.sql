-- Mantém no cadastro mestre somente instrumentos que já tiveram atividade B3.
-- A execução é idempotente: no ambiente já saneado, o DELETE afeta zero linhas.
create temporary table tmp_ativos_sem_atividade_b3_candidatos
on commit drop
as
select id
from investimento.ativos
where instrumento_canonico = true
  and atividade_confirmada_b3 = false
  and ultima_confirmacao_b3 is null
  and elegivel_analise = false;

create unique index tmp_ativos_sem_atividade_b3_candidatos_id_idx
    on tmp_ativos_sem_atividade_b3_candidatos (id);

do $migration$
declare
    fk record;
    coluna_logica record;
    candidatos_iniciais bigint;
    candidatos_removiveis bigint;
    protegidos_por_dependencia bigint;
    removidos bigint;
begin
    -- Bloqueia somente as linhas candidatas durante a verificação curta. Isso
    -- impede que uma nova FK seja criada entre a auditoria e a exclusão.
    perform 1
    from investimento.ativos master
    join tmp_ativos_sem_atividade_b3_candidatos candidato
      on candidato.id = master.id
    for update of master;

    select count(*)
      into candidatos_iniciais
      from tmp_ativos_sem_atividade_b3_candidatos;

    -- Remove da lista todo ativo referenciado por qualquer foreign key
    -- registrada contra investimento.ativos, inclusive chaves compostas ou
    -- referências a uma chave oficial diferente de id.
    for fk in
        select
            child_namespace.nspname as schema_name,
            child_table.relname as table_name,
            (
                select string_agg(
                    format(
                        'dependencia.%I = master.%I',
                        child_attribute.attname,
                        parent_attribute.attname
                    ),
                    ' and '
                    order by key_columns.position
                )
                from unnest(
                    constraint_row.conkey,
                    constraint_row.confkey
                ) with ordinality as key_columns(
                    child_attnum,
                    parent_attnum,
                    position
                )
                join pg_attribute child_attribute
                  on child_attribute.attrelid = constraint_row.conrelid
                 and child_attribute.attnum = key_columns.child_attnum
                join pg_attribute parent_attribute
                  on parent_attribute.attrelid = constraint_row.confrelid
                 and parent_attribute.attnum = key_columns.parent_attnum
            ) as join_condition
        from pg_constraint constraint_row
        join pg_class child_table
          on child_table.oid = constraint_row.conrelid
        join pg_namespace child_namespace
          on child_namespace.oid = child_table.relnamespace
        where constraint_row.contype = 'f'
          and constraint_row.confrelid = 'investimento.ativos'::regclass
    loop
        execute format(
            $delete_candidates$
            delete from tmp_ativos_sem_atividade_b3_candidatos candidato
            using investimento.ativos master
            where master.id = candidato.id
              and exists (
                  select 1
                  from %I.%I dependencia
                  where %s
              )
            $delete_candidates$,
            fk.schema_name,
            fk.table_name,
            fk.join_condition
        );
    end loop;

    -- Proteção adicional para dependências legadas sem FK declarada, quando
    -- usam as convenções ativo_id ou id_ativo e o mesmo tipo da chave mestre.
    for coluna_logica in
        select
            table_namespace.nspname as schema_name,
            table_row.relname as table_name,
            column_row.attname as column_name
        from pg_attribute column_row
        join pg_class table_row
          on table_row.oid = column_row.attrelid
        join pg_namespace table_namespace
          on table_namespace.oid = table_row.relnamespace
        join pg_attribute master_id
          on master_id.attrelid = 'investimento.ativos'::regclass
         and master_id.attname = 'id'
         and master_id.attnum > 0
         and not master_id.attisdropped
        where table_row.relkind in ('r', 'p')
          and table_row.oid <> 'investimento.ativos'::regclass
          and column_row.attnum > 0
          and not column_row.attisdropped
          and column_row.attname in ('ativo_id', 'id_ativo')
          and column_row.atttypid = master_id.atttypid
    loop
        execute format(
            $delete_legacy_candidates$
            delete from tmp_ativos_sem_atividade_b3_candidatos candidato
            using investimento.ativos master
            where master.id = candidato.id
              and exists (
                  select 1
                  from %I.%I dependencia
                  where dependencia.%I = master.id
              )
            $delete_legacy_candidates$,
            coluna_logica.schema_name,
            coluna_logica.table_name,
            coluna_logica.column_name
        );
    end loop;

    select count(*)
      into candidatos_removiveis
      from tmp_ativos_sem_atividade_b3_candidatos;

    protegidos_por_dependencia :=
        candidatos_iniciais - candidatos_removiveis;

    delete from investimento.ativos master
    using tmp_ativos_sem_atividade_b3_candidatos candidato
    where master.id = candidato.id
      and master.instrumento_canonico = true
      and master.atividade_confirmada_b3 = false
      and master.ultima_confirmacao_b3 is null
      and master.elegivel_analise = false;

    get diagnostics removidos = row_count;

    raise notice
        'Limpeza B3: candidatos=%, protegidos_por_dependencia=%, removidos=%',
        candidatos_iniciais,
        protegidos_por_dependencia,
        removidos;
end
$migration$;
