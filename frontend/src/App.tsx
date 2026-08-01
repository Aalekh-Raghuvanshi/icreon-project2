import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Repository from './pages/Repository'
import Task from './pages/Task'
import Workflow from './pages/Workflow'
import CodeDiff from './pages/CodeDiff'
import TestResults from './pages/TestResults'
import PullRequest from './pages/PullRequest'
import Logs from './pages/Logs'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/"           element={<Dashboard />} />
          <Route path="/repository" element={<Repository />} />
          <Route path="/task"       element={<Task />} />
          <Route path="/workflow"   element={<Workflow />} />
          <Route path="/diff"       element={<CodeDiff />} />
          <Route path="/tests"      element={<TestResults />} />
          <Route path="/pr"         element={<PullRequest />} />
          <Route path="/logs"       element={<Logs />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
