import { useEffect, useRef } from 'react'
import Phaser from 'phaser'
import { createGameConfig } from './config'
import type { GameBridge } from './GameBridge'
import { BootScene } from './scenes/BootScene'
import { OfficeScene } from './scenes/OfficeScene'

interface Props {
  bridge: GameBridge
}

export function PhaserGame({ bridge }: Props) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const gameRef = useRef<Phaser.Game | null>(null)

  useEffect(() => {
    if (!wrapperRef.current || !containerRef.current || gameRef.current) return

    // Measure the wrapper (which has definite CSS dimensions from the grid layout).
    // The inner container div is initially empty so has 0 dimensions.
    const wrapper = wrapperRef.current
    const container = containerRef.current

    // Force container to fill wrapper so clientWidth/Height are non-zero
    container.style.width  = `${wrapper.clientWidth}px`
    container.style.height = `${wrapper.clientHeight}px`

    // Safety: never create a 0×0 game
    const w = container.clientWidth  || window.innerWidth  - 400
    const h = container.clientHeight || window.innerHeight - 48

    if (w < 50 || h < 50) {
      console.warn('[PhaserGame] Container too small:', w, h, '— using fallback size')
      container.style.width  = `${window.innerWidth - 400}px`
      container.style.height = `${window.innerHeight - 48}px`
    }

    console.log('[PhaserGame] Creating Phaser game', container.clientWidth, '×', container.clientHeight)

    const config = createGameConfig(container)
    config.scene = [BootScene, OfficeScene]
    const game = new Phaser.Game(config)
    game.registry.set('bridge', bridge)
    gameRef.current = game

    // Keep canvas sized to wrapper on window resize
    const onResize = () => {
      if (!wrapper || !game) return
      container.style.width  = `${wrapper.clientWidth}px`
      container.style.height = `${wrapper.clientHeight}px`
      game.scale.resize(wrapper.clientWidth, wrapper.clientHeight)
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      game.destroy(true)
      gameRef.current = null
    }
  }, [bridge]) // bridge is a stable ref, effect runs once

  return (
    // Wrapper fills the CSS grid cell
    <div ref={wrapperRef} style={{ width: '100%', height: '100%' }}>
      {/* Phaser mounts its canvas inside this div */}
      <div ref={containerRef} />
    </div>
  )
}
