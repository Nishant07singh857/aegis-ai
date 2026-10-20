#!/usr/bin/env bash
# AEGIS: Kaggle pe killfeed dataset upload karo (ek command).
# Usage: bash notebooks/upload_dataset_to_kaggle.sh [dataset_dir]
# Default dataset_dir: data/youtube_dataset
#
# Pehle se chahiye:
#   1. ~/.kaggle/kaggle.json me valid API key (chmod 600)
#   2. data/youtube_dataset/ me dataset.yaml + chunks/*.zip (W2 banata hai)
#
# Ye script: auth check → username nikalo → staging dir → metadata →
#            kaggle datasets create. Dobara chalane pe naya version banata hai.

set -u
cd "$(dirname "$0")/.." || exit 1

DATASET_DIR="${1:-data/youtube_dataset}"
STAGE="/tmp/aegis_kaggle_upload"
DATASET_NAME="aegis-killfeed-dataset"

echo "=== 1. Kaggle auth check ==="
USERNAME=$(python3 -c "
from kaggle.api.kaggle_api_extended import KaggleApi
a = KaggleApi(); a.authenticate()
print(a.get_config_value('username'))
" 2>/dev/null) || {
  echo "❌ Kaggle auth FAIL. ~/.kaggle/kaggle.json me API key dalo (kaggle.com → Account → Create New API Token)."
  exit 1
}
echo "✅ Auth OK, username: $USERNAME"
SLUG="$USERNAME/$DATASET_NAME"

echo "=== 2. Dataset check: $DATASET_DIR ==="
[ -f "$DATASET_DIR/dataset.yaml" ] || { echo "❌ dataset.yaml nahi mila — W2 ka kaam abhi baaki hai."; exit 1; }
[ -d "$DATASET_DIR/chunks" ] || { echo "❌ chunks/ nahi mila."; exit 1; }
echo "   chunks: $(ls "$DATASET_DIR"/chunks/*.zip 2>/dev/null | wc -l) zip files"

echo "=== 3. Staging: $STAGE ==="
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$DATASET_DIR"/dataset.yaml "$STAGE"/
cp "$DATASET_DIR"/chunks/*.zip "$STAGE"/
# test split bhi ho to sath le jao (agar chunks me nahi hai)
[ -d "$DATASET_DIR/images/test" ] && { mkdir -p "$STAGE/images_test_note"; echo "test split chunks me hona chahiye" > "$STAGE/images_test_note/README.txt"; }

echo "=== 4. Metadata ==="
kaggle datasets init -p "$STAGE" >/dev/null 2>&1
python3 - "$STAGE/dataset-metadata.json" "$SLUG" <<'EOF'
import json, sys
meta_path, slug = sys.argv[1], sys.argv[2]
with open(meta_path) as f:
    meta = json.load(f)
meta["id"] = slug
meta["title"] = "AEGIS BGMI Killfeed Detection Dataset"
meta["licenses"] = [{"name": "CC0-1.0"}]
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print("   id:", slug)
EOF

echo "=== 5. Upload ==="
echo "   Pahli baar: create | Dobara: version (naya version)"
if kaggle datasets list --user "$USERNAME" 2>/dev/null | grep -q "$DATASET_NAME"; then
  echo "   → dataset maujood hai, naya VERSION ban raha hai..."
  kaggle datasets version -p "$STAGE" -m "auto update $(date -u +%Y-%m-%dT%H:%M:%SZ)"
else
  echo "   → naya dataset CREATE ho raha hai..."
  kaggle datasets create -p "$STAGE"
fi

echo ""
echo "✅ Ho gaya! Notebooks me DATASET_SLUG = \"$SLUG\" likho."
