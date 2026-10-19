# CAMUS: importación académica

Este repositorio **no redistribuye** las imágenes de CAMUS. Descargue el dataset real desde su fuente/distribución autorizada y conserve sus términos de licencia.

Para R06 se debe cargar al menos una ecocardiografía real antes de la evaluación. Se recomienda mantener juntas las vistas/fases de un mismo sujeto CAMUS y asociarlas al mismo paciente académico del sistema.

Flujo:

1. Descargue CAMUS de una fuente autorizada.
2. Si la distribución está en MHD/NIfTI, exporte frames representativos ED/ES a PNG o use DICOM si dispone de él.
3. Cree los usuarios/paciente demo con `python scripts/seed_demo_api.py`.
4. Ejecute `python scripts/import_camus_folder.py --patient-id <UUID> --folder <carpeta>`.
5. Abra la ficha del paciente y confirme que el visor obtiene la imagen desde Orthanc.

La asociación se guarda con `academic_link=true`, `dataset=CAMUS` y `source_subject_id`; no afirma que los datos administrativos creados en la app correspondan a la persona real del dataset.
