import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'

const EXTENSION_ASYNC_MESSAGE_ERROR =
  'A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received'

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason
  const message =
    typeof reason === 'string'
      ? reason
      : reason instanceof Error
        ? reason.message
        : ''

  // Ignore noisy browser-extension promise rejections without hiding app errors.
  if (message.includes(EXTENSION_ASYNC_MESSAGE_ERROR)) {
    event.preventDefault()
  }
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
