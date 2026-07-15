from bt_repro import foo as foo_module


def test_foo_use_template() -> None:
    assert foo_module.use_template().startswith("Foo")
