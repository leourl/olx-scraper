import pytest

from app.extractors.regex import generation_from, normalize_cpu


@pytest.mark.parametrize(
    "cpu,expected",
    [
        ("i5-8500", ("i5", 8500)),
        ("i5 8500", ("i5", 8500)),
        ("core i3", ("i3", None)),
        ("I7 6700", ("i7", 6700)),
        ("i9-13900", ("i9", 13900)),
        ("i5-13500 (13th gen)", ("i5", 13500)),
        ("i5 500", ("i5", 500)),
        ("ryzen 5 2600", ("ryzen5", 2600)),
        ("ryzen 7 5800x", ("ryzen7", 5800)),
        ("ryzen 5 PRO 4650GE", ("ryzen5", 4650)),
        ("ryzen 5 Pro 3500U", ("ryzen5", 3500)),
        ("ryzen7 5700g", ("ryzen7", 5700)),
        ("Core 2 Duo E7500", ("core2", 7500)),
        ("Dual-core e2200", ("core2", 2200)),
        ("Intel Duou Core", ("core2", None)),
        ("Intel Pentium G3250", ("pentium", 3250)),
        ("AMD Athlon 64 Dual Core 3800+", ("athlon", 3800)),
        ("Celeron D", ("celeron", None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_normalize_cpu(cpu, expected):
    assert normalize_cpu(cpu) == expected


@pytest.mark.parametrize(
    "family,model,expected",
    [
        ("i3", 6100, 6),
        ("i5", 8500, 8),
        ("i5", 10100, 10),
        ("i5", 13500, 13),
        ("i7", 6700, 6),
        ("i5", 500, None),          # modelo < 1000
        ("i5", 18500, None),        # fora de faixa (erro de vendedor)
        ("ryzen5", 5600, 5),
        ("ryzen7", 7800, 7),
        ("core2", 7500, None),      # não é Intel/Ryzen
        ("pentium", 3250, None),
        (None, 8500, None),
        ("i5", None, None),
    ],
)
def test_generation_from(family, model, expected):
    assert generation_from(family, model) == expected
