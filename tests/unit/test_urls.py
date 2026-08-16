from __future__ import annotations

import pytest

from vinted_sniper.vinted import urls


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.vinted.fr/catalog?search_text=x", "fr"),
        ("https://vinted.de/catalog?search_text=x", "de"),
        ("https://www.vinted.co.uk/catalog?search_text=x", "co.uk"),
        ("https://www.vinted.com/catalog?search_text=x", "com"),
    ],
)
def test_country_site_comes_from_the_domain(url: str, expected: str) -> None:
    assert urls.extract_tld(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.ebay.fr/catalog?search_text=x",
        "https://www.vinted.zz/catalog?search_text=x",
    ],
)
def test_non_vinted_addresses_are_refused(url: str) -> None:
    with pytest.raises(urls.InvalidSearchURLError):
        urls.extract_tld(url)


def test_array_parameters_become_api_names() -> None:
    params = urls.parse_search_params(
        "https://www.vinted.fr/catalog?catalog[]=1904&brand_ids[]=53&status[]=6&status[]=1"
    )

    assert params["catalog_ids"] == "1904"
    assert params["brand_ids"] == "53"
    assert params["status_ids"] == "6,1", "repeated filters should combine, not overwrite"


def test_category_and_brand_landing_pages_carry_their_filter_in_the_path() -> None:
    assert urls.parse_search_params("https://www.vinted.fr/catalog/1904-femmes")["catalog_ids"] == (
        "1904"
    )
    assert urls.parse_search_params("https://www.vinted.fr/brand/53-nike")["brand_ids"] == "53"


def test_tracking_parameters_are_dropped() -> None:
    params = urls.parse_search_params(
        "https://www.vinted.fr/catalog?search_text=nike&time=1700000000"
        "&search_id=12345&page=3&disabled_personalization=true&utm_source=x"
    )

    assert set(params) == {"search_text", "order"}


def test_results_are_always_requested_newest_first() -> None:
    params = urls.parse_search_params("https://www.vinted.fr/catalog?search_text=x&order=relevance")

    assert params["order"] == "newest_first"


def test_same_search_pasted_twice_normalises_to_one_key() -> None:
    first = urls.normalise_search_url(
        "https://www.vinted.fr/catalog?search_text=nike&price_to=30&time=111&search_id=a"
    )
    second = urls.normalise_search_url(
        "https://www.vinted.fr/catalog?price_to=30&search_text=nike&time=222&search_id=b&page=2"
    )

    assert first == second


def test_multi_word_searches_keep_their_plus_signs() -> None:
    normalised = urls.normalise_search_url("https://www.vinted.fr/catalog?search_text=nike+air+max")

    assert "search_text=nike+air+max" in normalised
    assert "%2B" not in normalised, "a re-encoded plus turns the search into a literal '+'"


def test_url_without_filters_is_refused_with_advice() -> None:
    with pytest.raises(urls.InvalidSearchURLError, match="no search filters"):
        urls.normalise_search_url("https://www.vinted.fr/catalog")


def test_missing_scheme_is_tolerated() -> None:
    assert urls.normalise_search_url("www.vinted.fr/catalog?search_text=x").startswith("https://")


def test_deep_links_stay_on_the_search_country_site() -> None:
    assert urls.item_url("de", 42) == "https://www.vinted.de/items/42"
    assert urls.message_seller_url("de", 42).endswith("/items/42/want_it/new")
    assert "transaction%5Bitem_id%5D=42" in urls.buy_url("de", 42)
