import install_finder  # noqa: F401

from importlib_resources import files


def main() -> None:
    print(files("sample_namespace"))


main()
