import sys

from blog.builder import run_make_mode, autobuild_main


def main():
    """Custom main method to format Make mode help text with the "-M" option"""
    args = sys.argv
    if args[1:3] == ['-M', "help"]:
        builder_args = args[2:]
        return run_make_mode(builder_args)
    else:
        return autobuild_main()


if __name__ == '__main__':
    main()
