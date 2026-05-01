import subprocess
import sys

result = subprocess.run([sys.executable, "-m", "pytest", "-q"], check=False)
raise SystemExit(result.returncode)
