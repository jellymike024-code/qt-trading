"""Download pinned rendering dependencies and public geographic assets locally."""
import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent / "dist"
ASSETS = {
    "vendor/three.module.js": "https://unpkg.com/three@0.180.0/build/three.module.js",
    "vendor/three.core.js": "https://unpkg.com/three@0.180.0/build/three.core.js",
    "vendor/OrbitControls.js": "https://unpkg.com/three@0.180.0/examples/jsm/controls/OrbitControls.js",
    "vendor/THREE-LICENSE.txt": "https://unpkg.com/three@0.180.0/LICENSE",
    "assets/earth_day.jpg": "https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg",
    "assets/earth_night.png": "https://threejs.org/examples/textures/planets/earth_lights_2048.png",
    "assets/countries.geojson": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson",
}


def main():
    manifest = {}
    for filename, url in ASSETS.items():
        path = ROOT / filename
        if path.exists() and path.stat().st_size > 100:
            payload = path.read_bytes()
        else:
            response = requests.get(url, timeout=25)
            response.raise_for_status()
            payload = response.content
            if len(payload) < 100:
                raise ValueError(f"unexpected empty asset: {filename}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        manifest[filename] = {"source": url, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        print(filename, len(payload))
    (ROOT / "assets" / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
