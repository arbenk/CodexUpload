import sys

from font_manager import run_font_helper


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    raise SystemExit(run_font_helper(sys.argv[1]))

