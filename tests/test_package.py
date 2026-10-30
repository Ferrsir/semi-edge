import semiedge


def test_package_exposes_version() -> None:
    assert isinstance(semiedge.__version__, str)
    assert semiedge.__version__
