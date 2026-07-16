import wrapt


@wrapt.when_imported("target")
def _hook(module: object) -> None:
    _ = module


import target


def main() -> None:
    loader_name = type(target.__loader__).__name__
    spec_loader_name = type(target.__spec__.loader).__name__  # type: ignore[union-attr]
    print(loader_name)
    print(spec_loader_name)
    assert loader_name == "SourceFileLoader"
    assert spec_loader_name == "SourceFileLoader"


main()
