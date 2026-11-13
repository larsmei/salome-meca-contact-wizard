#!/bin/bash
# Run one Salome test against a copy of test.hdf.
#
#   testing/run_salome_test.sh contact_preview_test.py
#   testing/run_salome_test.sh static_analysis_test.py
#   testing/run_salome_test.sh linear_study_test.py
#   CONTACT_RESULT_MED=/path/result.med testing/run_salome_test.sh shot_results.py
#
# SALOME_MECA selects the starter. The PATH command "salome" in this
# environment ignores arguments, so the default is the Flatpak starter.
# The original test.hdf is only copied. The script does not save it.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=${1:?test script name, for example linear_study_test.py}
SCRIPT_PATH="$ROOT/testing/$SCRIPT"
if [ ! -f "$SCRIPT_PATH" ]; then
  echo "Testskript fehlt: $SCRIPT_PATH" >&2
  exit 2
fi
if [ ! -f "$ROOT/test.hdf" ]; then
  echo "Geometrie fehlt: $ROOT/test.hdf" >&2
  exit 2
fi

SALOME_MECA=${SALOME_MECA:-/home/lars/Flatpaks/salome/salome_meca-lgpl-2025.1.0-1-20251026-scibian-12}
if [ ! -x "$SALOME_MECA" ]; then
  echo "Salome-Starter fehlt: $SALOME_MECA" >&2
  exit 2
fi

export DISPLAY=${DISPLAY:-:0}
WORK=$(mktemp -d /tmp/contact-wizard-test.XXXXXX)
cp -a "$ROOT/test.hdf" "$WORK/study.hdf"
echo "Kopie: $WORK/study.hdf"
echo "Skript: $SCRIPT_PATH"

ARGS=()
PLUGINS=${SALOME_PLUGINS:-$HOME/.config/salome/Plugins}
if [ -d "$PLUGINS" ]; then
  ARGS+=(-B "$PLUGINS:$PLUGINS")
fi

exec "$SALOME_MECA" "${ARGS[@]}" -- "$WORK/study.hdf" "$SCRIPT_PATH"
