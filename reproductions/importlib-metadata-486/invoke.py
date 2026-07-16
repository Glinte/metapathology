from importlib_metadata import files


def main() -> None:
    found = files("plover")
    assert found is not None
    print(found)


main()
