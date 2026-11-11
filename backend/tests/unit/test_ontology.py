import pytest

from app.ontology import norm_label, resolve_metrics, resolve_sectors, sector_for_label


@pytest.mark.parametrize(
    "label,expected",
    [
        ("information and communications", "ict"),
        ("Information & Communications", "ict"),  # SingStat spelling
        ("Information And Communications", "ict"),  # MOM wage table spelling
        ("financial and insurance services", "finance"),
        ("Finance & Insurance", "finance"),
        ("Transport And Storage", "transport"),
        ("Transportation & Storage", "transport"),
        ("Education", "public_admin_edu"),
        ("telecommunications, broadcasting and publishing", "ict_telecom"),
        ("something unrelated", None),
    ],
)
def test_source_labels_map_to_canonical_sector(label, expected):
    assert sector_for_label(label) == expected


def test_norm_label_collapses_punctuation_and_ampersand():
    assert norm_label("Food  Beverage Services") == "food beverage services"
    assert norm_label("Arts, Entertainment & Recreation") == "arts entertainment and recreation"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Analyse employment in the technology sector", ["ict"]),
        ("compare finance and tech wages", ["finance", "ict"]),
        ("how is manufacturing doing", ["manufacturing"]),
        ("what is the weather", []),
        ("Please analyse it carefully", []),  # the pronoun 'it' must not match the IT sector
    ],
)
def test_resolve_sectors(text, expected):
    assert resolve_sectors(text) == expected


def test_resolve_metrics():
    assert resolve_metrics("wage growth and job vacancies") == ["job_vacancies", "total_wage_change"] or set(
        resolve_metrics("wage growth and job vacancies")
    ) >= {"total_wage_change", "job_vacancies"}
    assert "retrenchments" in resolve_metrics("layoffs in 2023")
