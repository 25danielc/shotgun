#!/bin/sh
# Fetch the three phone agents' marks for the intro pop-outs into comp/logos (git-ignored):
# Meta (Muse) and OpenAI (Dots) from Wikimedia Commons, Instinct's wordmark from instinct.com.
#   sh video/tools/fetch_logos.sh
set -e
cd "$(dirname "$0")/.."
mkdir -p footage/logos comp/logos
UA="ShotgunDemoVideo/1.0"
curl -sL -A "$UA" "https://upload.wikimedia.org/wikipedia/commons/7/7b/Meta_Platforms_Inc._logo.svg" -o footage/logos/meta.svg
curl -sL -A "$UA" "https://upload.wikimedia.org/wikipedia/commons/6/66/OpenAI_logo_2025_%28symbol%29.svg" -o footage/logos/openai_symbol.svg
curl -sL -A "Mozilla/5.0" "https://instinct.com/redesign/og-image-v4.png" -o footage/logos/instinct_og-image-v4.png
sed 's/fill:#192830/fill:#ffffff/' footage/logos/meta.svg > comp/logos/meta_white.svg
sed -e 's/<svg /<svg fill="#ffffff" /' footage/logos/openai_symbol.svg > comp/logos/openai_symbol_white.svg
uv run --no-project --with pillow --with numpy python - <<'PY'
from PIL import Image
import numpy as np
a = np.array(Image.open("footage/logos/instinct_og-image-v4.png").convert("L")).astype(float)[320:625, 15:1185]
alpha = np.clip((255 - a) * 1.6, 0, 255).astype(np.uint8)
Image.fromarray(np.dstack([np.full_like(alpha, 255)] * 3 + [alpha]), "RGBA").save("comp/logos/instinct_white.png")
PY
ls comp/logos
