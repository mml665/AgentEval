import { useState } from 'react';
import type { API } from './api';
import type { BenchmarkVersion } from './types';
import { Code, ErrorBox } from './ui';

export function parseCases(text: string): Record<string, unknown>[] {
  let value: unknown;
  try { value = JSON.parse(text); } catch {
    value = text.split(/\r?\n/).map((line,i) => { if (!line.trim()) return null; try { return JSON.parse(line); } catch { throw new Error(`第 ${i+1} 行不是合法 JSON。`); } }).filter(v => v !== null);
  }
  if (value && !Array.isArray(value) && typeof value === 'object') value = 'cases' in value ? value.cases : 'case_key' in value ? [value] : null;
  if (!Array.isArray(value) || !value.length) throw new Error('需要非空用例数组、cases 对象或 JSONL。');
  return value as Record<string, unknown>[];
}

export function CaseEditor({ text, changed }: { text: string; changed: (s: string) => void }) {
  const [selected,setSelected] = useState<number | null>(null), [draft,setDraft] = useState(''), [error,setError] = useState('');
  let rows: Record<string,unknown>[] = []; let parseError = '';
  try { rows = parseCases(text); } catch (e) { parseError = !text.trim() || text.trim() === '[]' ? '' : (e as Error).message; }
  const publish = (items: Record<string,unknown>[]) => { changed(JSON.stringify(items,null,2)); setSelected(null); setError(''); };
  return <details className="case-editor"><summary>表格预览与基础编辑（{rows.length} 条）</summary><ErrorBox message={error || parseError}/>{rows.length > 0 && <><div className="table-scroll"><table><thead><tr><th>行 / case_key</th><th>输入摘要</th><th>断言</th><th>标签 / 状态</th><th/></tr></thead><tbody>{rows.map((r,i) => <tr key={i}><td>{i+1} · {String(r?.case_key || '缺少 case_key')}</td><td>{JSON.stringify(r?.input)?.slice(0,100)}</td><td>{Object.keys((r?.assertions || {}) as object).join(', ') || '缺少断言'}</td><td>{JSON.stringify(r?.tags || [])} · {r?.case_key && r?.input && r?.assertions ? '待服务端校验' : '字段不完整'}</td><td><button type="button" className="text-button" onClick={() => { setSelected(i); setDraft(JSON.stringify(r,null,2)); }}>编辑用例 {i+1}</button><button type="button" className="text-button" onClick={() => publish(rows.filter((_,n) => n !== i))}>删除用例 {i+1}</button></td></tr>)}</tbody></table></div></> }<button type="button" className="button secondary" onClick={() => publish([...rows,{ case_key: `case-${rows.length+1}`, input: {}, assertions: {}, tags: [] }])}>添加用例</button>{selected !== null && <div><label className="field"><span>单条用例完整 JSON（保留嵌套结构）</span><textarea rows={9} value={draft} onChange={e => setDraft(e.target.value)}/></label><button type="button" className="button secondary" onClick={() => { try { const value = JSON.parse(draft); if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('单条用例必须是对象。'); publish(rows.map((r,i) => i === selected ? value : r)); } catch (e) { setError((e as Error).message); } }}>应用单条修改</button></div>}</details>;
}

export function BenchmarkDiff({ api, versions }: { api: API; versions: BenchmarkVersion[] }) {
  const [left,setLeft] = useState(''), [right,setRight] = useState(''), [value,setValue] = useState<unknown>(null), [error,setError] = useState('');
  return <details><summary>查看固定版本新增 / 删除 / 修改差异</summary><div className="form-grid"><label className="field"><span>数据集基线版本</span><select value={left} onChange={e => { setLeft(e.target.value); setValue(null); }}><option value="">选择基线</option>{versions.map(v => <option key={v.id} value={v.id}>{v.version} · {v.id.slice(0,8)}</option>)}</select></label><label className="field"><span>数据集候选版本</span><select value={right} onChange={e => { setRight(e.target.value); setValue(null); }}><option value="">选择候选</option>{versions.map(v => <option key={v.id} value={v.id}>{v.version} · {v.id.slice(0,8)}</option>)}</select></label></div><button type="button" className="button secondary" disabled={!left || !right || left === right} onClick={() => { setError(''); void api.get(`benchmark-versions/${left}/diff?other_version_id=${right}`).then(setValue).catch(e => setError(e.message)); }}>比较数据集版本</button><ErrorBox message={error}/>{value !== null && <Code value={value}/>}</details>;
}
