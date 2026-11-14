import { createRoot } from 'react-dom/client'
import { Gate } from './Gate'
import './styles.css'

createRoot(document.getElementById('root')!).render(<Gate />)

// Uso sin conexión: solo en la versión compilada, para no interferir con el desarrollo.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}))
}
