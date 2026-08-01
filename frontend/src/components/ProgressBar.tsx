interface ProgressBarProps {
  value: number   // 0–1
  label?: string
  showPercent?: boolean
}

export default function ProgressBar({ value, label, showPercent = true }: ProgressBarProps) {
  const pct = Math.round(value * 100)
  return (
    <div>
      {(label || showPercent) && (
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 12, color: 'var(--color-text-muted)' }}>
          {label && <span>{label}</span>}
          {showPercent && <span style={{ fontFamily: 'var(--font-mono)' }}>{pct}%</span>}
        </div>
      )}
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
