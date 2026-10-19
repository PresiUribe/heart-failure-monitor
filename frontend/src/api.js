// Tanto en desarrollo como en el contenedor usamos /api.
// - Vite (npm run dev) lo proxifica a localhost:8000.
// - Nginx (Docker) lo proxifica al servicio Docker api:8000.
// Se puede sobreescribir con VITE_API_URL si alguna vez se necesita.
const API_URL = import.meta.env.VITE_API_URL || "/api";

export function getToken() {
  return localStorage.getItem("token") || "";
}

export function setToken(token) {
  localStorage.setItem("token", token);
}

export function clearToken() {
  localStorage.removeItem("token");
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const detail =
      typeof body === "object" && body?.detail
        ? body.detail
        : typeof body === "string"
        ? body
        : JSON.stringify(body);

    throw new Error(`${response.status} - ${detail}`);
  }

  return body;
}

export async function api(path, options = {}) {
  const token = getToken();

  const headers = {
    ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {})
  };

  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers
    });
  } catch (error) {
    throw new Error(
      `No fue posible conectar con el backend a través de /api. ` +
      `Verifica que el servicio API esté activo.`
    );
  }

  return parseResponse(response);
}

export async function login(username, password) {
  const form = new URLSearchParams();
  form.append("username", username);
  form.append("password", password);

  let response;
  try {
    response = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded"
      },
      body: form
    });
  } catch (error) {
    throw new Error(
      `No fue posible conectar con el backend a través de /api. ` +
      `Verifica que FastAPI esté activo.`
    );
  }

  const data = await parseResponse(response);
  setToken(data.access_token);
  return data;
}

export { API_URL };

export async function apiBlob(path, options = {}) {
  const token = getToken();
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {})
      }
    });
  } catch {
    throw new Error("No fue posible descargar el recurso desde el backend.");
  }

  if (!response.ok) {
    let detail = await response.text();
    try { detail = JSON.parse(detail)?.detail || detail; } catch {}
    throw new Error(`${response.status} - ${detail}`);
  }
  return response.blob();
}
