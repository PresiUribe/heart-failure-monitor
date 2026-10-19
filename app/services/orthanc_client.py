import io
from datetime import datetime
import httpx
import numpy as np
from PIL import Image
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid
from app.config import settings


def image_to_dicom(image_bytes: bytes, filename: str, patient_ref: str, source_subject: str, view: str | None, phase: str | None) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    arr = np.asarray(img, dtype=np.uint8)

    meta = Dataset()
    meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(filename or "camus.dcm", {}, file_meta=meta, preamble=b"\x00" * 128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID = patient_ref
    ds.PatientName = "ACADEMIC^" + patient_ref
    ds.Modality = "US"
    ds.StudyDescription = "CAMUS academic echocardiography"
    ds.SeriesDescription = " ".join(x for x in [view, phase] if x) or "CAMUS"
    ds.ImageComments = f"Academic link; source CAMUS subject {source_subject}"
    now = datetime.utcnow()
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.Rows, ds.Columns = arr.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = arr.tobytes()
    buff = io.BytesIO()
    pydicom.dcmwrite(buff, ds, write_like_original=False)
    return buff.getvalue()


async def upload_instance(dicom_bytes: bytes) -> str:
    async with httpx.AsyncClient(timeout=settings.orthanc_timeout_seconds) as client:
        r = await client.post(f"{settings.orthanc_base_url.rstrip('/')}/instances", content=dicom_bytes, headers={"Content-Type":"application/dicom"})
        r.raise_for_status()
        data = r.json()
        return data.get("ID") or data.get("ID", "")


async def preview_png(instance_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=settings.orthanc_timeout_seconds) as client:
        r = await client.get(f"{settings.orthanc_base_url.rstrip('/')}/instances/{instance_id}/preview")
        r.raise_for_status()
        return r.content
