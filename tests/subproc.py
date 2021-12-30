import argparse
import os
import sys

PARSER = argparse.ArgumentParser(description="Subprocess test helper")
PARSER.add_argument(
    "--stderr",
    dest="stderr",
    action="store_true",
    help="write to stderr instead of stdout",
)


def main(argv):
    args = PARSER.parse_args(argv)
    out = sys.stderr if args.stderr else sys.stdout
    print("READY", file=out, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if line.startswith("EXIT:"):
            return int(line.split(":")[1])
        if line.startswith("PID"):
            print("PID:" + str(os.getpid()), file=out, flush=True)
        else:
            print("ACK:" + line, file=out, flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
