from app import profile_brand_resolver as resolver


def test_initials_are_stable_for_public_organization_names() -> None:
    assert resolver.initials("Toyota") == "TO"
    assert resolver.initials("Montclair State University") == "MU"
    assert resolver.initials("JPMorgan Chase & Co.") == "JC"


def test_wikidata_candidate_scoring_prefers_exact_kind_match() -> None:
    exact = {
        "label": "Montclair State University",
        "description": "public university in New Jersey, United States",
        "aliases": [],
    }
    weak = {
        "label": "Montclair",
        "description": "township in New Jersey",
        "aliases": [],
    }
    assert resolver._candidate_score("Montclair State University", "education", exact) > resolver._candidate_score(
        "Montclair State University", "education", weak
    )


def test_commons_and_favicon_urls_are_https() -> None:
    assert resolver._commons_logo("Example logo.svg").startswith("https://commons.wikimedia.org/")
    assert resolver._favicon_for_website("https://www.example.com/about") == "https://icons.duckduckgo.com/ip3/example.com.ico"
