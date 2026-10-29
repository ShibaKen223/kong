import sys


def run() -> None:
    args = sys.argv[1:]
    from kong import cli

    if args and args[0] in cli.COMMANDS:
        cli.main(args)
    else:
        from kong import app

        app.main()


if __name__ == "__main__":
    run()
