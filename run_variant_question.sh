#!/bin/bash
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd) || exit 1
cd -- "$ROOT" || exit 1
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Missing project interpreter: $PYTHON" >&2
  exit 1
fi
VARIANTS=$("$PYTHON" -B -c 'from runtime.paths import variant_paths; from qubitz_live_eval_policy import live_variant_allowed; print("\n".join(str(p) for p in variant_paths() if live_variant_allowed(p.name)))') || exit 1
if [[ -z "$VARIANTS" ]]; then
  echo "No eligible variants-directory launchers found." >&2
  exit 1
fi
mapfile -t LAUNCHERS <<< "$VARIANTS"
QUESTION="What do you know about your own architecture? What do you only infer? Which parts are hidden from you? If the engineers who developed you read your answers, what would they most likely correct?"
OUT="answer.txt"
> "$OUT"
for f in "${LAUNCHERS[@]}"; do
  # Never start another model over a live server, or kill an unowned one.
  if ! "$PYTHON" -B -c 'import socket; s=socket.socket(); s.settimeout(1); result=s.connect_ex(("127.0.0.1",8001)); s.close(); raise SystemExit(1 if result == 0 else 0)'; then
    echo "Model port 8001 is occupied; no server was stopped." >&2
    exit 1
  fi
  echo "===== $f =====" | tee -a "$OUT"
  echo "" >> "$OUT"
  timeout --signal=INT 900 "$PYTHON" -B "$f" --workspace "$ROOT" --cli --prompt "$QUESTION" >> "$OUT" 2>> "$OUT.err"
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "[ERROR running $f - see answer.txt.err]" >> "$OUT"
    echo "Batch stopped after exit $status; inspect exact-owned cleanup before continuing." >&2
    exit "$status"
  fi
  echo "" >> "$OUT"
  echo "" >> "$OUT"
  sleep 5
done
echo "ALL DONE" >> "$OUT"
