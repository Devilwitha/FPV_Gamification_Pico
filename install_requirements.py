import os
import subprocess
import sys


def run(cmd):
    print("[RUN]", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(cmd)}")


def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    requirements_path = os.path.join(project_dir, "requirements.txt")

    if not os.path.isfile(requirements_path):
        print("[ERROR] requirements.txt was not found.")
        print(f"Expected path: {requirements_path}")
        sys.exit(1)

    print(f"Using Python: {sys.executable}")
    print(f"Using requirements file: {requirements_path}")

    # Keep pip tooling current to avoid old resolver and TLS issues.
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

    # Install all dependencies listed in requirements.txt.
    run([sys.executable, "-m", "pip", "install", "-r", requirements_path])

    print("[OK] All requirements installed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
