"""Explicit developer-only launch surface for the optional Qubitz browser UI."""
from runtime.launcher import main

if __name__ == "__main__":
    raise SystemExit(main(developer_web=True))
