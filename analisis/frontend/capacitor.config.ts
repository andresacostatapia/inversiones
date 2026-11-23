import type { CapacitorConfig } from '@capacitor/cli'

// App Android: el mismo dashboard, compilado en modo móvil (todo se calcula en el teléfono, sin servidor).
//   npm run android:sync   → compila (dist-mobile) y copia al proyecto android/
//   cd android && ./gradlew assembleDebug   → android/app/build/outputs/apk/debug/app-debug.apk
const config: CapacitorConfig = {
  appId: 'cl.analisis.inversiones',
  appName: 'Análisis de acciones',
  webDir: 'dist-mobile',
  plugins: {
    // fetch() pasa por HTTP nativo: Yahoo y mindicador responden sin las restricciones CORS del navegador.
    CapacitorHttp: { enabled: true },
    LocalNotifications: { iconColor: '#0f5b78' },
  },
  android: {
    // Sin contenido http (solo https) y sin depuración remota en la versión compilada.
    allowMixedContent: false,
  },
}

export default config
