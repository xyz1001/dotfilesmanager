import subprocess
import sys

res = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_windows.py", "tests/test_cli.py"],
    capture_output=True,
    text=True,
)
print(res.stdout)
print(res.stderr)
if res.returncode != 0:
    lines = res.stdout.split("\n")
    in_failure = False
    failure_text = []
    for line in lines:
        if line.startswith("____") or line.startswith("=== FAILURES ==="):
            if failure_text:
                print("::error::" + "%0A".join(failure_text))
            in_failure = True
            failure_text = [line]
        elif line.startswith("===") and in_failure:
            if failure_text:
                print("::error::" + "%0A".join(failure_text))
            in_failure = False
            failure_text = []
        elif in_failure:
            failure_text.append(line)
    if failure_text:
        print("::error::" + "%0A".join(failure_text))
    sys.exit(res.returncode)
