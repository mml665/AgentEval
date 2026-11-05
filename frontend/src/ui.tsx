import { useEffect, useRef, type ReactNode } from 'react';

const paths: Record<string, ReactNode> = {
  grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
  run: <><path d="m9 5 11 7-11 7z"/><path d="M4 5v14"/></>,
  agent: <><rect x="4" y="6" width="16" height="14" rx="4"/><path d="M12 2v4M8 12h.01M16 12h.01M8 16h8M1 11v4M23 11v4"/></>,
  data: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
  sliders: <><path d="M5 3v7m0 4v7M12 3v12m0 4v2M19 3v2m0 4v12M2 10h6m1 5h6m1-10h6"/></>,
  compare: <><path d="M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5"/>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  close: <path d="m6 6 12 12M6 18 18 6"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6"/></>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></>,
  folder: <path d="M3 7V5h7l2 3h9v12H3z"/>,
  lock: <><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></>,
  exit: <><path d="M10 4H4v16h6m5-12 4 4-4 4M8 12h11"/></>,
  code: <><path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/></>,
  activity: <path d="M2 12h5l3-8 4 16 3-8h5"/>,
};

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.grid}</svg>;
}
export function Logo() { return <div className="logo-mark"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="m7 24 9-17 9 17M11 17h10" fill="none" stroke="currentColor" strokeWidth="2.5"/><circle cx="25" cy="8" r="2" fill="currentColor"/></svg></div>; }

const labels: Record<string, string> = { published: '已投递', blocked: '待确认占用', done: '已归档', completed: '已完成', failed: '失败', passed: '通过', running: '执行中', evaluating: '评分中', pending: '等待中', queued: '排队中', cancelling: '取消中', cancelled: '已取消', unknown: '待判定', excluded: '已排除', inconclusive: '证据不足', scored: '已评分', missing_evidence: '缺少证据', evaluator_error: '评分异常', timed_out: '超时', agent_failed: 'Agent 异常', outcome_unknown: '结果未知', infrastructure_failed: '基础设施异常' };
export function Badge({ status }: { status: string }) { return <span className={`badge status-${status}`}><i />{labels[status] || status}</span>; }
export function Empty({ title, description, action }: { title: string; description?: string; action?: ReactNode }) { return <div className="empty-state"><span className="empty-icon"><Icon name="folder" size={27}/></span><h3>{title}</h3>{description && <p>{description}</p>}{action}</div>; }
export function ErrorBox({ message }: { message?: string | null }) { return message ? <div className="error-box" role="alert">{message}</div> : null; }
export function Code({ value }: { value: unknown }) { return <pre className="json-code">{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>; }
export function Metric({ label, value, hint, icon }: { label: string; value: ReactNode; hint: string; icon: string }) { return <div className="metric-card"><div className="metric-top">{label}<Icon name={icon}/></div><strong>{value}</strong><small>{hint}</small></div>; }
export function pct(value: number | null | undefined) { return value == null ? '—' : `${Math.round(value * 1000) / 10}%`; }
export function date(value: string) { return new Date(value.endsWith('Z') || /[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }); }
export function short(value: string) { return value.slice(0, 8); }
export function Heading({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: ReactNode }) { return <div className="page-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1><p>{description}</p></div>{action}</div>; }

export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
  return <dialog ref={ref} className={`modal ${wide ? 'modal-wide' : ''}`} onCancel={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}><div className="modal-header"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭弹窗"><Icon name="close"/></button></div>{children}</dialog>;
}

export function Pager({ page, total, size = 10, loading = false, onChange }: { page: number; total: number; size?: number; loading?: boolean; onChange: (page: number) => void }) {
  return <div className="pager" aria-label="分页" aria-busy={loading}><span>共 {total} 条 · 第 {page + 1} / {Math.max(1, Math.ceil(total / size))} 页</span><button className="button secondary" disabled={loading || page === 0} onClick={() => onChange(page - 1)}>上一页</button><button className="button secondary" disabled={loading || (page + 1) * size >= total} onClick={() => onChange(page + 1)}>下一页</button></div>;
}
