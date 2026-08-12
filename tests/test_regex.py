from app.extractors.regex import extract_regex


def test_ram_ddr():
    r = extract_regex("OptiPlex 7050 i5 8500 16gb ddr4 256gb ssd")
    assert r.ram_gb == 16
    assert r.storage_gb == 256
    assert r.storage_type == "ssd"


def test_ram_de_memoria():
    r = extract_regex("Dell Optiplex 7020 vem com 4GB de memória DDR3 e HD de 500GB")
    assert r.ram_gb == 4
    assert r.storage_gb == 500
    assert r.storage_type == "hdd"


def test_cpu_geracao_e_tipo():
    r = extract_regex("Dell SFF Optiplex 7010 i5-13500 (13th Gen) 8GB RAM")
    assert r.cpu == "i5-13500 (13th gen)"
    assert r.form_factor == "sff"


def test_form_factor_mini_e_tower():
    assert extract_regex("ThinkCentre M700 mini pc").form_factor == "mini"
    assert extract_regex("Dell tower completo").form_factor == "tower"
    assert extract_regex("notebook dell").form_factor == "notebook"
    assert extract_regex("Dell OptiPlex All In One 23.8").form_factor == "all-in-one"
    assert extract_regex("computador desktop 8GB RAM").form_factor == "desktop"
    assert extract_regex("mini desktop pc").form_factor == "mini"


def test_storage_tb_hdd():
    r = extract_regex("i7 1TB HDD 512gb ssd")
    assert r.storage_gb == 512
    assert r.storage_type == "ssd"


def test_storage_nvme():
    r = extract_regex("i7 nvme 256gb")
    assert r.storage_gb == 256
    assert r.storage_type == "nvme"


def test_brand_model_extraction():
    r = extract_regex("Computador Dell Optiplex 3040 i3 - 6100")
    assert r.brand == "Dell"
    assert r.model == "OptiPlex 3040"
    r = extract_regex("Lenovo ThinkCentre-M700 mini")
    assert r.brand == "Lenovo"
    assert r.model == "ThinkCentre M700"


def test_sem_match():
    r = extract_regex("Computador usado, ótimo estado")
    assert r.ram_gb is None
    assert r.cpu is None
    assert r.form_factor is None
    assert r.brand is None
    assert r.resolved == set()


def test_cpu_simple():
    r = extract_regex("Core I3 10 Geração 8GB Ram")
    assert r.cpu == "core i3"


def test_explicit_generation_pt():
    r = extract_regex("Intel Core i5 de 10ª geração, 8GB RAM")
    assert r.cpu == "core i5"
    assert r.cpu_generation == 10


def test_explicit_generation_variants():
    assert extract_regex("i7 10th gen").cpu_generation == 10
    assert extract_regex("i7 10 gen").cpu_generation == 10
    assert extract_regex("i7 geração 10").cpu_generation == 10
    assert extract_regex("i7 10 geração").cpu_generation == 10


def test_explicit_generation_after():
    assert extract_regex("Processador de 8ª geração").cpu_generation == 8


def test_explicit_generation_from_model_text():
    r = extract_regex("OptiPlex 7050 SFF i5-13500 (13th Gen) 8GB RAM")
    assert r.cpu == "i5-13500 (13th gen)"
    assert r.cpu_generation == 13


def test_explicit_generation_out_of_range():
    assert extract_regex("i5 de 20ª geração").cpu_generation is None
    assert extract_regex("i5 de 15ª geração").cpu_generation is None


def test_explicit_generation_partial_digit():
    assert extract_regex("i5 de 115ª geração").cpu_generation is None