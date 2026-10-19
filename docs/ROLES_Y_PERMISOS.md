# Roles y permisos

## ADMIN
- Crea y administra usuarios.
- Ve todos los pacientes.
- Crea pacientes.
- Soft edit de pacientes y usuarios.
- Soft delete de pacientes y usuarios.
- Modifica/elimina cualquier Encounter u Observation.
- Es el único rol que puede restaurar un registro.
- Consulta auditoría e historial.
- Sincroniza datos con HAPI FHIR.

## MEDICO_RURAL
- Puede crear un paciente y queda asignado automáticamente.
- Ve únicamente pacientes asignados.
- Crea Encounter.
- Crea Observation.
- Puede hacer soft edit o soft delete únicamente sobre Encounter y Observation
  creados por él.
- No puede restaurar registros.
- Puede sincronizar recursos de pacientes asignados con HAPI FHIR.

## ESPECIALISTA
- Ve únicamente pacientes asignados.
- Crea Encounter y Observation para pacientes asignados.
- Puede editar/eliminar únicamente los registros clínicos que él mismo creó.
- No puede restaurar.
- Puede sincronizar recursos de pacientes asignados.

## PACIENTE_CUIDADOR
- Perfil de solo lectura.
- Debe estar relacionado con un paciente mediante `patient_assignments` con relación `PACIENTE`.
- Ve únicamente su código de paciente y tres datos de seguimiento:
  - médico/profesional de la última atención;
  - último peso;
  - última presión arterial.
- No crea Encounter ni Observation.
- No modifica ni elimina registros clínicos.
- No restaura registros.
- No consulta directamente recursos FHIR ni endpoints clínicos genéricos.
