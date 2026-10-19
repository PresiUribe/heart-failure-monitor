# Frontend

SPA en React + Vite, compilada y servida por Nginx dentro del contenedor `frontend` (`docker-compose.yml`).

- `src/App.jsx`: login, lista de pacientes, ficha clínica, administración de usuarios.
- `src/Corte2Panel.jsx`: canal en tiempo real (WebSocket), bandeja de notificaciones y contadores, banda persistente de alertas críticas, IA con aprobación, remisiones, contabilidad, visor PACS, clusters, y panel admin (cuentas bloqueadas y auditoría).
- `src/api.js`: cliente HTTP; todas las llamadas van a `/api`, que Nginx reenvía a FastAPI.
- `nginx.conf`: proxy hacia la API (`/api/`), WebSocket (`/api/events/ws`), SSE (`/api/events/stream`) y HAPI FHIR (`/fhir/`), con cabeceras de seguridad y rate limiting.

Desarrollo local sin Docker (opcional):

```powershell
npm install
npm run dev
```

Vite proxifica `/api` hacia `http://localhost:8000`.