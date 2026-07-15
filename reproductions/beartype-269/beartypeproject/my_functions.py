def foo(bar: float) -> None:
    pass


if __name__ == "__main__":
    foo("baz")  # type: ignore[arg-type]
