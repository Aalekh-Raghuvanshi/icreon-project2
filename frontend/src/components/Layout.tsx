import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'

export default function Layout() {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar />
      <main style={{ flex: 1, overflowY: 'auto', padding: '1.75rem 2rem', minWidth: 0 }}>
        <Outlet />
      </main>
    </div>
  )
}
