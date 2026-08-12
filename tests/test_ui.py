from tests.seed import seed_ads


def test_index_page(app):
    seed_ads(app)
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "OLX Monitor" in html
    assert "OptiPlex 7050" in html
    assert "R$ 800,00" in html  # filtro brl


def test_index_pagination_links(app):
    seed_ads(app)
    with app.app_context():
        from app.scrapers.base import RawAd
        from app.services import ad_service

        for i in range(30):
            ad_service.upsert_raw(
                RawAd(
                    olx_id=str(1000 + i),
                    title=f"Anúncio paginação {i}",
                    url=f"https://sp.olx.com.br/x/pag-{i}",
                    price_cents=1000 + i,
                )
            )
    # page na query não pode quebrar url_for (bug: **request.args duplicava 'page')
    assert app.test_client().get("/?page=1").status_code == 200
    assert app.test_client().get("/?page=2").status_code == 200
    resp = app.test_client().get("/?page=1&price_max=99999")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "page=2" in html and "price_max=99999" in html  # próximo preserva filtros sem duplicar page


def test_index_filtered(app):
    seed_ads(app)
    resp = app.test_client().get("/?ram_min=16")
    html = resp.get_data(as_text=True)
    assert "OptiPlex 7050" in html
    assert "ThinkCentre" not in html


def test_index_gen_filter(app):
    seed_ads(app)
    # geração exige família (escalas Intel/Ryzen incompatíveis)
    resp = app.test_client().get("/?cpu_family=i5&gen_min=8")
    html = resp.get_data(as_text=True)
    assert "OptiPlex 7050" in html      # i5-8500 (8ª)
    assert "ThinkCentre" not in html    # i3-6100 (6ª)

    # sem família, gen_min é ignorado → volta a mostrar as outras famílias
    resp = app.test_client().get("/?gen_min=8")
    html = resp.get_data(as_text=True)
    assert "ThinkCentre" in html


def test_filters_family_optgroup(app):
    import re

    seed_ads(app)
    norm = lambda html: re.sub(r"\s+", " ", html)

    html = norm(app.test_client().get("/").get_data(as_text=True))
    assert '<optgroup label="Intel">' in html
    assert '<optgroup label="AMD">' in html
    # sem família, o seletor de geração fica desabilitado
    assert 'id="gen_min" name="gen_min" disabled' in html

    # com família (i5), o seletor ganha a faixa e não fica desabilitado
    html = norm(app.test_client().get("/?cpu_family=i5").get_data(as_text=True))
    assert 'id="gen_min" name="gen_min"' in html
    assert 'id="gen_min" name="gen_min" disabled' not in html
    assert ">8ª</option>" in html


def test_filters_vendor_options(app):
    import re

    seed_ads(app)
    norm = lambda html: re.sub(r"\s+", " ", html).replace(" >", ">")
    html = norm(app.test_client().get("/").get_data(as_text=True))
    assert '<option value="intel">Intel (qualquer)</option>' in html
    assert '<option value="amd">AMD (qualquer)</option>' in html


def test_filters_vendor_enables_gen(app):
    import re

    seed_ads(app)
    norm = lambda html: re.sub(r"\s+", " ", html).replace(" >", ">")

    # cpu_family=intel → geração habilitada com faixa 1–14
    html = norm(app.test_client().get("/?cpu_family=intel").get_data(as_text=True))
    assert 'id="gen_min" name="gen_min"' in html
    assert 'id="gen_min" name="gen_min" disabled' not in html
    assert ">14ª</option>" in html
    # option "Intel (qualquer)" marcada como selected
    assert '<option value="intel" selected>Intel (qualquer)</option>' in html

    # cpu_family=amd → faixa 1–9
    html = norm(app.test_client().get("/?cpu_family=amd").get_data(as_text=True))
    assert ">9ª</option>" in html
    assert ">14ª</option>" not in html

    # caixa alta normaliza → selected também em 'Intel'
    html = norm(app.test_client().get("/?cpu_family=Intel").get_data(as_text=True))
    assert '<option value="intel" selected>Intel (qualquer)</option>' in html


def test_detail_page(app):
    seed_ads(app)
    resp = app.test_client().get("/ads/1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "OptiPlex 7050" in html
    assert "i5-8500" in html
    assert "Ver na OLX" in html


def test_ad_detail_has_available_toggle(app):
    seed_ads(app)
    html = app.test_client().get("/ads/1").get_data(as_text=True)
    assert 'id="ad-available-toggle"' in html
    assert "Disponível" in html


def test_index_hides_and_shows_oculto_badge(app):
    from app.extensions import db
    from app.models import Ad

    with app.app_context():
        db.session.add(Ad(olx_id="oc1", title="Sucata oculta",
                          url="https://sp.olx.com.br/x/oc-1", user_disabled=True))
        db.session.commit()

    assert "Sucata oculta" not in app.test_client().get("/").get_data(as_text=True)
    html = app.test_client().get("/?include_inactive=1").get_data(as_text=True)
    assert "Sucata oculta" in html
    assert 'badge oculto' in html


def test_detail_404(app):
    seed_ads(app)
    assert app.test_client().get("/ads/999").status_code == 404


def test_review_page(app):
    seed_ads(app)
    resp = app.test_client().get("/review")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Sem specs extraídas" in html
    assert "OptiPlex 3020" in html       # sem specs
    assert "OptiPlex 7010" in html       # confiança baixa (0.3)


def test_run_page(app):
    resp = app.test_client().get("/run")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="run-terms"' in html
    assert 'name="steps"' in html
    assert 'id="run-region"' in html
    assert "Rodar" in html
    # campo de termos bloqueado por padrão + botões Editar/Salvar/Cancelar
    assert 'readonly' in html
    assert 'id="terms-edit"' in html
    assert 'id="terms-save"' in html
    assert 'id="terms-cancel"' in html


def test_run_page_shows_history(app):
    from app.extensions import db
    from app.models import RunHistory

    with app.app_context():
        db.session.add(
            RunHistory(
                source="autorun",
                steps=["scrape"],
                status="done",
                result={"scrape": {"novos": 2}},
            )
        )
        db.session.commit()

    resp = app.test_client().get("/run")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Histórico de execuções" in html
    assert "autorun" in html
    assert "scrape: novos 2" in html
    assert 'data-status="done"' in html


def test_index_includes_removed_badge(app):
    from app.extensions import db
    from app.models import Ad

    with app.app_context():
        db.session.add(Ad(olx_id="rm1", title="OptiPlex removido", url="https://sp.olx.com.br/x/rm-1",
                          is_active=False, price_cents=10000))
        db.session.commit()

    # oculto por padrão
    assert "OptiPlex removido" not in app.test_client().get("/").get_data(as_text=True)

    html = app.test_client().get("/?include_inactive=1").get_data(as_text=True)
    assert "OptiPlex removido" in html
    assert "removido" in html


def test_detail_shows_removed_badge(app):
    from app.extensions import db
    from app.models import Ad

    with app.app_context():
        a = Ad(olx_id="rm2", title="Dell removido", url="https://sp.olx.com.br/x/rm-2",
               is_active=False, price_cents=10000)
        db.session.add(a)
        db.session.commit()
        ad_id = a.id

    html = app.test_client().get(f"/ads/{ad_id}").get_data(as_text=True)
    assert "removido" in html


def test_run_page_lists_check_step(app):
    html = app.test_client().get("/run").get_data(as_text=True)
    assert 'value="check"' in html
    assert "Check (verificar publicados)" in html


def test_run_page_has_manual_ad_form(app):
    html = app.test_client().get("/run").get_data(as_text=True)
    assert 'id="add-ad-url"' in html
    assert 'id="add-ad-process"' in html
    assert 'id="add-ad-button"' in html
    assert "Cadastrar anúncio manual" in html
