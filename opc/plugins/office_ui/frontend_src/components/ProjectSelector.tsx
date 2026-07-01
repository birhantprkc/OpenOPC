import { useState, useCallback } from 'react'
import type { Project } from '../types/kanban'

interface ProjectSelectorProps {
  projects: Project[]
  activeId: string
  onSelect: (id: string) => void
  onCreate: (id: string) => void
  onDelete?: (id: string) => void
}

export function ProjectSelector({ projects, activeId, onSelect, onCreate, onDelete }: ProjectSelectorProps) {
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const handleCreate = useCallback(() => {
    const id = newName.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-|-$/g, '')
    if (!id) return
    onCreate(id)
    setNewName('')
    setCreating(false)
  }, [newName, onCreate])

  const handleDelete = useCallback(() => {
    if (!confirmDelete || !onDelete) return
    onDelete(confirmDelete)
    setConfirmDelete(null)
  }, [confirmDelete, onDelete])

  return (
    <div className="project-selector" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <select
        className="theme-select"
        value={activeId}
        onChange={e => onSelect(e.target.value)}
        title="Switch project"
      >
        {projects.map(p => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
      {creating ? (
        <form
          onSubmit={e => { e.preventDefault(); handleCreate() }}
          style={{ display: 'flex', gap: 4 }}
        >
          <input
            autoFocus
            className="theme-select"
            value={newName}
            placeholder="project-name"
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Escape') setCreating(false) }}
            style={{ width: 120 }}
          />
          <button type="submit" className="pill-btn" style={{ fontSize: 11, padding: '2px 8px' }}>+</button>
        </form>
      ) : (
        <button
          className="pill-btn"
          onClick={() => setCreating(true)}
          title="New project"
          style={{ fontSize: 11, padding: '2px 8px' }}
        >
          +
        </button>
      )}
      {onDelete && activeId !== 'default' && !confirmDelete && (
        <button
          className="pill-btn"
          onClick={() => setConfirmDelete(activeId)}
          title="Delete project"
          style={{ fontSize: 11, padding: '2px 8px', color: '#ef4444' }}
        >
          Del
        </button>
      )}

      {confirmDelete && (
        <div className="project-delete-confirm" style={{
          position: 'fixed', inset: 0, zIndex: 9999,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(0,0,0,0.5)',
        }}>
          <div style={{
            background: 'var(--bg-surface, #1e1e2e)', borderRadius: 12, padding: '24px 32px',
            maxWidth: 400, boxShadow: '0 8px 32px rgba(0,0,0,0.4)', textAlign: 'center',
          }}>
            <p style={{ margin: '0 0 8px', fontWeight: 600, fontSize: 15 }}>
              Delete project "{confirmDelete}"?
            </p>
            <p style={{ margin: '0 0 20px', fontSize: 13, opacity: 0.7 }}>
              All sessions, messages, tasks, and agent data in this project will be permanently deleted. This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
              <button
                className="pill-btn"
                onClick={() => setConfirmDelete(null)}
                style={{ padding: '6px 18px', fontSize: 13 }}
              >
                Cancel
              </button>
              <button
                className="pill-btn"
                onClick={handleDelete}
                style={{ padding: '6px 18px', fontSize: 13, background: '#ef4444', color: '#fff' }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
