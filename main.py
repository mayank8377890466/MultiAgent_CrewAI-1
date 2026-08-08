import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def main():
    subprocess.run(
        ["streamlit", "run", str(PROJECT_DIR / "src" / "testfixer" / "ui.py")]
        + sys.argv[1:],
        cwd=str(PROJECT_DIR),
    )


if __name__ == "__main__":
    main()
