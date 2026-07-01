import type { KanbanBoard } from '../types/kanban'

interface BoardSelectorProps {
  boards: KanbanBoard[]
  activeBoardId: string | null
  onSelect: (id: string) => void
}

export function BoardSelector({ boards, activeBoardId, onSelect }: BoardSelectorProps) {
  return (
    <div className="board-selector">
      <div className="board-tabs">
        {boards.map(b => (
          <button
            key={b.id}
            className={`board-tab${b.id === activeBoardId ? ' active' : ''}`}
            style={{ '--board-color': b.color } as React.CSSProperties}
            onClick={() => onSelect(b.id)}
          >
            <span className="board-tab-dot" style={{ background: b.color }} />
            {b.name}
          </button>
        ))}
      </div>
    </div>
  )
}
