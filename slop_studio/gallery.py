"""Generate lightweight HTML gallery pages for batch output viewing.

Handles both raster images and 3D meshes (the ``.glb`` files the TRELLIS.2 /
Pixal3D image-to-3D templates produce). Meshes can't be shown inline without
pulling a WebGL viewer off a CDN — which fails silently offline — so they get
a labelled card that opens the file itself when clicked.
"""

import os
import time
from pathlib import Path

_GALLERY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Slop Studio Gallery</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #eee; font-family: system-ui, sans-serif; padding: 2rem; }
  h1 { text-align: center; margin-bottom: 1.5rem; font-weight: 300; font-size: 1.4rem; color: #999; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1rem;
  }
  .card {
    background: #252525;
    border-radius: 8px;
    overflow: hidden;
    transition: transform 0.15s;
  }
  .card:hover { transform: scale(1.02); }
  .card img {
    width: 100%;
    display: block;
    cursor: pointer;
  }
  .card .label {
    padding: 0.5rem 0.75rem;
    font-size: 0.8rem;
    color: #888;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card a.mesh {
    display: block;
    text-decoration: none;
    color: inherit;
    padding: 2.5rem 0.75rem;
    text-align: center;
    background: linear-gradient(160deg, #2f2f2f, #202020);
  }
  .card a.mesh .glyph { font-size: 2.5rem; display: block; margin-bottom: 0.5rem; }
  .card a.mesh .hint { font-size: 0.75rem; color: #777; }
  /* Lightbox */
  .lightbox {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.92);
    justify-content: center;
    align-items: center;
    z-index: 10;
    cursor: zoom-out;
  }
  .lightbox.active { display: flex; }
  .lightbox img { max-width: 95vw; max-height: 95vh; border-radius: 4px; }
</style>
</head>
<body>
<h1>Slop Studio &mdash; Session Gallery</h1>
<div class="grid" id="grid"></div>
<div class="lightbox" id="lightbox" onclick="this.classList.remove('active')">
  <img id="lb-img" src="">
</div>
<script>
const images = IMAGE_DATA_PLACEHOLDER;

const grid = document.getElementById("grid");
const lightbox = document.getElementById("lightbox");
const lbImg = document.getElementById("lb-img");

// src is a filesystem-relative path, not a URL: encode each segment so a
// filename containing #, ? or % resolves to the file rather than being read
// as URL syntax. Separators stay literal.
const toUrl = path => path.split("/").map(encodeURIComponent).join("/");

images.forEach(img => {
  const card = document.createElement("div");
  card.className = "card";
  const href = toUrl(img.src);
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = img.name;

  if (img.kind === "mesh") {
    const link = document.createElement("a");
    link.className = "mesh";
    link.href = href;
    link.innerHTML = '<span class="glyph">\u25e7</span><span class="hint">3D model &mdash; click to open</span>';
    card.appendChild(link);
  } else {
    const imgEl = document.createElement("img");
    imgEl.src = href;
    imgEl.loading = "lazy";
    imgEl.addEventListener("click", () => {
      lbImg.src = href;
      lightbox.classList.add("active");
    });
    card.appendChild(imgEl);
  }

  card.appendChild(label);
  grid.appendChild(card);
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape") lightbox.classList.remove("active");
});
</script>
</body>
</html>
"""


MESH_EXTENSIONS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".fbx", ".usdz"}


def generate_gallery(image_paths: list[str], output_dir: str) -> str:
    """Generate an HTML gallery file for the given output paths.

    Args:
        image_paths: Absolute paths to image or 3D-mesh files.
        output_dir: The root output directory (gallery is written here).

    Returns:
        Absolute path to the generated HTML file.
    """
    import json

    output_dir_path = Path(output_dir)
    gallery_path = output_dir_path / f"gallery_{int(time.time())}.html"

    image_data = []
    for img_path in image_paths:
        abs_path = Path(img_path).resolve()
        rel_path = Path(os.path.relpath(abs_path, output_dir_path)).as_posix()
        kind = "mesh" if abs_path.suffix.lower() in MESH_EXTENSIONS else "image"
        image_data.append({"src": rel_path, "name": abs_path.name, "kind": kind})

    html = _GALLERY_HTML.replace("IMAGE_DATA_PLACEHOLDER", json.dumps(image_data))
    gallery_path.write_text(html)

    return str(gallery_path)
