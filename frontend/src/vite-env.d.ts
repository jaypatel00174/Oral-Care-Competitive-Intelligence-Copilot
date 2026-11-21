/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API. Default "/api" (the Vite proxy to http://127.0.0.1:8000). */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
