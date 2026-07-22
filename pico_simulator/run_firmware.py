import argparse
import importlib
import os
import shutil
import sys

from pico_runtime import install


def clone_source_to_data(source_dir, data_dir, refresh=False):
    if refresh and os.path.isdir(data_dir):
        shutil.rmtree(data_dir)

    if not os.path.isdir(data_dir):
        shutil.copytree(source_dir, data_dir)
        print("[SIM] Created data clone from source.")


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description="Run FPV Pico firmware with a MicroPython simulator layer.")
    parser.add_argument(
        "--entry",
        choices=["main", "boot", "recovery"],
        default="main",
        help="Firmware entry module from source (default: main)",
    )
    parser.add_argument(
        "--source-dir",
        default=os.path.join(project_root, "source"),
        help="Path to source directory that contains firmware files.",
    )
    parser.add_argument(
        "--data-dir",
        default=os.path.join(project_root, "data"),
        help="Path to writable cloned firmware workspace used for simulation.",
    )
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="Delete data directory and clone a fresh copy from source before starting.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Desktop port used when firmware tries to listen on port 80.",
    )
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source_dir)
    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(source_dir):
        raise SystemExit(f"Source directory not found: {source_dir}")

    clone_source_to_data(source_dir, data_dir, refresh=args.refresh_data)

    os.chdir(data_dir)
    if data_dir not in sys.path:
        sys.path.insert(0, data_dir)

    install(sim_port=args.port)

    print(f"[SIM] Source directory: {source_dir}")
    print(f"[SIM] Working directory: {data_dir}")
    print(f"[SIM] Importing firmware entry: {args.entry}.py")
    print(f"[SIM] Open UI at: http://127.0.0.1:{args.port}/")

    importlib.import_module(args.entry)


if __name__ == "__main__":
    main()
