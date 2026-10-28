"""Start the native gateway with the existing dotenv configuration, without
putting credentials or internal tokens in command arguments or files."""
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env", override=False)
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
gateway = root / "native/bin/rms-gateway.exe"
collector = root / "native/bin/mt5-collector.exe"
if not gateway.is_file() or not collector.is_file():
    raise SystemExit("Native binaries are missing. Run native/build-native.ps1 first.")
args = [str(gateway), "--root", str(root), "--python", str(root / ".venv/Scripts/python.exe"),
        "--collector", str(collector), "--listen", "127.0.0.1:" + os.getenv("PORT", "8000"),
        "--backend-port", os.getenv("RMS_ANALYTICS_PORT", "8001"),
        "--sdk-libs", os.getenv("MT5_SDK_LIB_DIR", r"D:\MetaTrader5SDK\Libs")]
try:
    raise SystemExit(subprocess.call(args, cwd=root, env=os.environ))
except KeyboardInterrupt:
    raise SystemExit(0)
