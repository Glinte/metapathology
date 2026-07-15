from beartype.claw import beartype_this_package

beartype_this_package()


def add(a: int, b: int) -> int:
    return int(a) + b


def main() -> None:
    print(add(1, 2))
    print(add("1", 2))  # type: ignore[arg-type]
