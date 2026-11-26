"""${{ values.name }} application entry point."""


def main() -> None:
    print("Hello from ${{ values.name }}!")


if __name__ == "__main__":
    main()
