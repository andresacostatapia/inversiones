/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "mobile" = app Android (o prueba del modo local en el navegador) */
  readonly VITE_TARGET?: string
}
