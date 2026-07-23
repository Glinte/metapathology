"""Minimal application imported after a freezer bootstrap."""


def main() -> None:
    """Import one dependency after monitoring starts."""
    import fractions

    print(f"fixture-app: {fractions.Fraction(1, 2)}")


if __name__ == "__main__":
    main()
