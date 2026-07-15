import subprocess
import sys

res = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_windows.py",
        "tests/test_cli.py",
    ],
    capture_output=True,
    text=True,
)
print(res.stdout)
print(res.stderr)
if res.returncode != 0:
    lines = res.stdout.split("\n")
    current_test = ""
    for line in lines:
        if line.startswith("____"):
            current_test = line.strip("_ ")
        elif line.startswith("E   "):
            msg = f"{current_test}: {line.strip()}"
            print(f"::error::{msg}")
    sys.exit(res.returncode)
