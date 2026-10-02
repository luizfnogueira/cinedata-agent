"""Camada semântica: views TEMP que tratam as armadilhas da camada Gold.

As views são criadas por conexão (schema `temp`), então o arquivo do banco
nunca é alterado. Elas aplicam as regras de negócio uma única vez, em SQL,
para que o modelo não precise lembrar de cada filtro a cada pergunta.

Problemas tratados (levantados no perfil dos dados):
- lucro_* nunca é nulo: vale 0 ou -orçamento sem receita, e a receita inteira
  quando falta orçamento. Aqui só existe lucro com receita E orçamento.
- orçamento/receita com valores irrisórios (ex.: US$ 4) distorcem a margem.
  A margem só é calculada quando ambos são >= US$ 10 mil.
- nota_tmdb = 0 com qtd_tmdb = 0 significa "sem votos", não nota zero.
- 4 filmes têm a popularidade contaminada com um ano (ex.: 2018.0).
- duracao_minutos = 0 significa duração desconhecida.
- Gêneros estão em inglês; a view expõe também o nome em português.
"""

import sqlite3

MARGEM_VALOR_MINIMO_USD = 10_000

GENEROS_PT = {
    "Action": "Ação",
    "Adventure": "Aventura",
    "Animation": "Animação",
    "Comedy": "Comédia",
    "Crime": "Crime",
    "Documentary": "Documentário",
    "Drama": "Drama",
    "Family": "Família",
    "Fantasy": "Fantasia",
    "History": "História",
    "Horror": "Terror",
    "Music": "Música",
    "Mystery": "Mistério",
    "Romance": "Romance",
    "Science Fiction": "Ficção Científica",
    "Thriller": "Suspense",
    "Tv Movie": "Filme de TV",
    "War": "Guerra",
    "Western": "Faroeste",
}

_GENERO_PT_CASE = "CASE g.nome_genero\n" + "\n".join(
    f"            WHEN '{en}' THEN '{pt}'" for en, pt in GENEROS_PT.items()
) + "\n            ELSE g.nome_genero\n        END"

_TEM_FINANCEIRO = "f.receita_usd IS NOT NULL AND f.orcamento_usd IS NOT NULL"
_TEM_MARGEM = (
    f"f.receita_usd >= {MARGEM_VALOR_MINIMO_USD} AND f.orcamento_usd >= {MARGEM_VALOR_MINIMO_USD}"
)

VIEWS: dict[str, str] = {
    "vw_filmes": f"""
        SELECT
            m.sk_movie_id,
            m.titulo,
            m.ano_lancamento,
            m.data_lancamento,
            m.status_filme,
            CASE WHEN m.status_filme = 'Lançado' THEN 1 ELSE 0 END AS lancado,
            NULLIF(m.duracao_minutos, 0) AS duracao_minutos,
            f.orcamento_usd,
            f.receita_usd,
            CASE WHEN {_TEM_FINANCEIRO} THEN f.lucro_usd END AS lucro_usd,
            f.orcamento_brl,
            f.receita_brl,
            CASE WHEN {_TEM_FINANCEIRO} THEN f.lucro_brl END AS lucro_brl,
            CASE WHEN {_TEM_MARGEM} THEN f.lucro_usd * 1.0 / f.receita_usd END AS margem_lucro,
            CASE
                WHEN f.popularidade BETWEEN 1900 AND 2030
                     AND f.popularidade = CAST(f.popularidade AS INTEGER) THEN NULL
                ELSE f.popularidade
            END AS popularidade,
            CASE WHEN f.qtd_tmdb > 0 THEN f.nota_tmdb END AS nota_tmdb,
            f.qtd_tmdb,
            f.nota_imdb,
            f.qtd_imdb,
            r.nota_media_usuarios,
            COALESCE(r.qtd_avaliacoes_usuarios, 0) AS qtd_avaliacoes_usuarios
        FROM dim_movies m
        JOIN fact_movies_performance f ON f.sk_movie_id = m.sk_movie_id
        LEFT JOIN dim_reviews r ON r.sk_movie_id = m.sk_movie_id
    """,
    "vw_filme_genero": f"""
        SELECT
            b.sk_movie_id,
            g.sk_genre_id,
            g.nome_genero AS genero_en,
            {_GENERO_PT_CASE} AS genero
        FROM bridge_movie_genre b
        JOIN dim_genres g ON g.sk_genre_id = b.sk_genre_id
    """,
    "vw_filme_pessoa": """
        SELECT
            b.sk_movie_id,
            p.sk_person_id,
            p.nome_pessoa,
            p.tipo_pessoa
        FROM bridge_movie_person b
        JOIN dim_people p ON p.sk_person_id = b.sk_person_id
    """,
    "vw_filme_produtora": """
        SELECT
            b.sk_movie_id,
            c.sk_company_id,
            c.nome_produtora
        FROM bridge_movie_company b
        JOIN dim_companies c ON c.sk_company_id = b.sk_company_id
    """,
}


# Objetos materializados como tabela TEMP indexada em vez de view.
# vw_filme_pessoa tem 745 mil linhas com chaves texto de 64 caracteres: como view,
# a pergunta "dupla ator-diretor" leva ~90 s; materializada, ~2 s
# (custo único de ~2 s ao abrir a conexão).
MATERIALIZED_INDEXES: dict[str, list[str]] = {
    "vw_filme_pessoa": [
        "sk_movie_id, tipo_pessoa",
        "sk_person_id",
    ],
}


def create_semantic_views(conn: sqlite3.Connection) -> None:
    """Cria as views/tabelas TEMP na conexão. Funciona mesmo com o banco em modo read-only."""
    for name, select_sql in VIEWS.items():
        if name in MATERIALIZED_INDEXES:
            conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {name} AS {select_sql}")
            for i, columns in enumerate(MATERIALIZED_INDEXES[name]):
                conn.execute(f"CREATE INDEX IF NOT EXISTS temp.ix_{name}_{i} ON {name} ({columns})")
        else:
            conn.execute(f"CREATE TEMP VIEW IF NOT EXISTS {name} AS {select_sql}")


def reference_year(conn: sqlite3.Connection, min_films: int = 100) -> int:
    """Último ano com volume relevante de filmes lançados.

    A base vai até 2026, mas 2025 e 2026 têm só 4 filmes lançados. Perguntas
    sobre "últimos N anos" contam a partir deste ano (2024 na base atual).
    """
    row = conn.execute(
        """
        SELECT MAX(ano_lancamento) FROM (
            SELECT ano_lancamento FROM dim_movies
            WHERE status_filme = 'Lançado'
            GROUP BY ano_lancamento
            HAVING COUNT(*) >= ?
        )
        """,
        (min_films,),
    ).fetchone()
    return int(row[0])
