"""Import real CAMUS images into Orthanc through the project API.

Example:
  python scripts/import_camus_folder.py \
      --patient-id DOCUMENTO_O_UUID --folder ./CAMUS_patient0001 --username medico.demo \
      --password MedicoDemo2026!

The script keeps the original CAMUS subject id and marks the association as
academic. It supports PNG, JPG/JPEG and DICOM. NIfTI/MHD sources should first
be exported to representative ED/ES PNG frames using the source dataset tools.
"""
import argparse
import re
from pathlib import Path
import httpx


def infer_meta(path: Path):
    name = path.stem.upper()
    view = "4CH" if "4CH" in name else ("2CH" if "2CH" in name else "US")
    phase = "ED" if re.search(r"(^|[_-])ED($|[_-])", name) else ("ES" if re.search(r"(^|[_-])ES($|[_-])", name) else "NA")
    match = re.search(r"PATIENT[_-]?(\d+)", name)
    subject = f"patient{int(match.group(1)):04d}" if match else path.parent.name
    return subject, view, phase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--patient-id", required=True, help="Documento del paciente (preferido), PAT-* o UUID interno")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--username", default="medico.demo")
    ap.add_argument("--password", default="MedicoDemo2026!")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    folder = Path(args.folder)
    files = [p for p in folder.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".dcm"}]
    if not files:
        raise SystemExit("No se encontraron PNG/JPG/DICOM. No se generan imágenes sintéticas automáticamente.")

    with httpx.Client(timeout=60) as client:
        lr = client.post(f"{base}/auth/login", data={"username": args.username, "password": args.password})
        lr.raise_for_status()
        headers = {"Authorization": f"Bearer {lr.json()['access_token']}"}
        for path in files:
            subject, view, phase = infer_meta(path)
            with path.open("rb") as fh:
                rr = client.post(
                    f"{base}/patients/{args.patient_id}/images/import",
                    headers=headers,
                    data={"source_subject_id": subject, "dataset": "CAMUS", "view": view, "phase": phase},
                    files={"file": (path.name, fh, "application/dicom" if path.suffix.lower() == ".dcm" else "image/png")},
                )
            rr.raise_for_status()
            print(path.name, "->", rr.json()["id"])

        # Update the documentation count for the demo patient so DATASETS.md
        # reflects the actual real images loaded before evaluation.
        lr = client.get(f"{base}/patients/{args.patient_id}/images", headers=headers)
        lr.raise_for_status()
        count = len(lr.json())
        docs = Path(__file__).resolve().parents[1] / "docs" / "DATASETS.md"
        if docs.exists():
            text = docs.read_text(encoding="utf-8")
            import re as _re
            text = _re.sub(
                r"\*\*Número de imágenes cargadas:\*\* [^\n.]*\.",
                f"**Número de imágenes cargadas:** {count}.",
                text,
                count=1,
            )
            docs.write_text(text, encoding="utf-8")
        print(f"Imágenes CAMUS asociadas al paciente demo: {count}")


if __name__ == "__main__":
    main()
