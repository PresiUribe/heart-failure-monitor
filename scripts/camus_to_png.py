"""Convierte imágenes CAMUS (NIfTI .nii/.nii.gz o MetaImage .mhd) a PNG para importarlas al PACS.

Uso:
    pip install nibabel numpy pillow          (y SimpleITK si tus archivos son .mhd)
    python scripts/camus_to_png.py <carpeta_CAMUS_paciente> <carpeta_salida_png>

Solo toma las imágenes ED/ES de las vistas 2CH/4CH. Excluye las máscaras de
segmentación (_gt) y las secuencias (_sequence). Si una imagen sale rotada,
usar --no-transpose.
"""
import argparse
import re
from pathlib import Path

import numpy as np
from PIL import Image

PATRON = re.compile(r"_(2CH|4CH)_(ED|ES)\.(nii\.gz|nii|mhd)$", re.IGNORECASE)


def leer(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".mhd":
        import SimpleITK as sitk  # pip install SimpleITK
        return np.squeeze(sitk.GetArrayFromImage(sitk.ReadImage(str(path))))
    import nibabel as nib
    return np.squeeze(nib.load(str(path)).get_fdata())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("origen")
    ap.add_argument("destino")
    ap.add_argument("--no-transpose", action="store_true")
    a = ap.parse_args()
    src, dst = Path(a.origen), Path(a.destino)
    dst.mkdir(parents=True, exist_ok=True)

    archivos = [p for p in src.rglob("*") if p.is_file() and PATRON.search(p.name)]
    if not archivos:
        print(f"No se encontraron imágenes *_2CH|4CH_ED|ES.(nii.gz|nii|mhd) en {src}. Contenido:")
        for p in sorted(src.rglob("*"))[:40]:
            print("  ", p.relative_to(src))
        return

    for f in sorted(archivos):
        arr = leer(f).astype(np.float32)
        if arr.ndim == 3:  # por si viene como volumen de un solo frame
            arr = arr[..., arr.shape[-1] // 2] if arr.shape[-1] < arr.shape[0] else arr[arr.shape[0] // 2]
        arr = 255 * (arr - arr.min()) / max(float(arr.max() - arr.min()), 1.0)
        img = arr.astype(np.uint8)
        if not a.no_transpose and f.suffix.lower() != ".mhd":
            img = img.T  # NIfTI guarda (ancho, alto); .mhd ya viene (alto, ancho)
        nombre = PATRON.sub(lambda m: f"_{m.group(1).upper()}_{m.group(2).upper()}.png", f.name)
        Image.fromarray(img).save(dst / nombre)
        print(f"{f.name} -> {nombre} {img.shape}")


if __name__ == "__main__":
    main()
