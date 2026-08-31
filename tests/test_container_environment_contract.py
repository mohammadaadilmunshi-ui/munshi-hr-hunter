from scripts.validate_container_environment_contract import validate


def test_container_environment_contract():
    assert validate() == []
