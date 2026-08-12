import click
from flask import Flask

from app.models import Ad
from app.scrapers.base import RawAd
from app.scrapers.client import OlxClient, ScrapeBlockedError
from app.scrapers.olx import OlxScraper
from app.services import ad_service
from app.services.runner import (
    run_check,
    run_enrich,
    run_fill_specs,
    run_process,
    run_scrape,
)


def register(app: Flask) -> None:
    app.cli.add_command(scrape)
    app.cli.add_command(enrich)
    app.cli.add_command(check)
    app.cli.add_command(process)
    app.cli.add_command(fill_specs)
    app.cli.add_command(add)


@click.command()
@click.argument("query")
@click.option("--region", default="estado-sp", help="Região da OLX (ex.: estado-sp).")
@click.option("--max-pages", default=None, type=int, help="Limite de páginas (default: SCRAPER_MAX_PAGES).")
@click.option("--no-details", is_flag=True, help="Não buscar a página de detalhe (só listagem).")
def scrape(query: str, region: str, max_pages: int | None, no_details: bool) -> None:
    """Coleta anúncios da OLX e salva no banco (dedup por URL)."""
    from flask import current_app

    try:
        stats = run_scrape(current_app, query, region, max_pages, no_details)
    except ScrapeBlockedError as e:
        raise click.ClickException(str(e))

    click.echo(f"listagem: {stats['listados']} anúncios únicos")
    click.echo(f"novos: {stats['novos']} | total no banco: {stats['total']}")


@click.command()
@click.option("--limit", default=None, type=int, help="Máx. de anúncios a enriquecer.")
def enrich(limit: int | None) -> None:
    """Busca a página de detalhe dos anúncios sem descrição (preenche descrição/imagens)."""
    from flask import current_app

    from app.extensions import db

    try:
        stats = run_enrich(current_app, limit)
    except ScrapeBlockedError as e:
        raise click.ClickException(str(e))

    click.echo(
        f"enriquecidos: {stats['enriquecidos']} de {stats['total']} | "
        f"total no banco: {db.session.query(Ad).count()}"
    )


@click.command()
@click.option("--limit", default=None, type=int, help="Máx. de anúncios ativos a verificar.")
def check(limit: int | None) -> None:
    """Verifica se anúncios ativos ainda estão publicados (404/410 → removido)."""
    from flask import current_app

    try:
        stats = run_check(current_app, limit)
    except ScrapeBlockedError as e:
        raise click.ClickException(str(e))

    click.echo(
        f"checados: {stats['checados']} | removidos: {stats['removidos']} | "
        f"ativos: {stats['ativos']} | sem confirmar: {stats['sem_confirmar']} | "
        f"erros: {stats['erros']}"
    )


@click.command()
@click.option("--limit", default=None, type=int, help="Máx. de anúncios pendentes a processar.")
@click.option("--ad", "ad_id", default=None, type=int, help="Processar/reprocessar um anúncio específico (id).")
@click.option("--force", is_flag=True, help="Reprocessar mesmo já extraído.")
@click.option("--missing-generation", is_flag=True,
              help="Reprocessar ads com specs mas sem cpu_generation.")
def process(limit: int | None, ad_id: int | None, force: bool, missing_generation: bool) -> None:
    """Extrai specs (regex + LLM) dos anúncios ainda sem extração."""
    from flask import current_app

    try:
        stats = run_process(current_app, limit, ad_id, force, missing_generation)
    except ValueError as e:
        raise click.ClickException(str(e))

    click.echo(
        f"ok: {stats['ok']} | falhas: {stats['falhas']} | retries: {stats.get('retries', 0)} "
        f"| tokens in={stats['tokens_in']} (cache {stats['tokens_cache']}) out={stats['tokens_out']} | "
        f"custo estimado: US${stats['custo']:.4f}"
    )


@click.command()
@click.option("--limit", default=None, type=int, help="Máx. de specs a preencher.")
def fill_specs(limit: int | None) -> None:
    """Fill determinístico (só regex, sem LLM): preenche lacunas de geração/CPU/RAM/storage sem sobrescrever valores existentes."""
    from flask import current_app

    stats = run_fill_specs(current_app, limit)
    click.echo(f"preenchidos: {stats['fill']} de {stats['total']} specs sem geração")


@click.command()
@click.argument("url")
@click.option("--no-process", is_flag=True, help="Não extrair specs na hora.")
def add(url: str, no_process: bool) -> None:
    """Cadastra um anúncio da OLX a partir do link direto."""
    from flask import current_app

    from app.scrapers.client import ScrapeBlockedError
    from app.services.runner import import_single_ad

    try:
        result = import_single_ad(current_app, url, process=not no_process)
    except (ScrapeBlockedError, ValueError) as e:
        raise click.ClickException(str(e))

    if result["status"] == "removed":
        raise click.ClickException("anúncio não existe mais na OLX (removido)")
    if result["status"] == "not_an_ad":
        raise click.ClickException("página não contém dados de anúncio (JSON-LD ausente)")

    click.echo(
        f"criado: {result['created']} | id: {result['ad_id']} | specs: {result['processed']} "
        f"| {result['title']}"
    )
