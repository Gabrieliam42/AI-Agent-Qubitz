"""Legacy entrypoint using the shared public default; pass --tier for a subset."""
from disposable_followup_matrix import main

if __name__ == "__main__":
    raise SystemExit(main())
