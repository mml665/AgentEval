import { useState } from 'react';
import type { API } from './api';
import type { BenchmarkVersion } from './types';
import { Code, ErrorBox } from './ui';

export function parseCases(text: string, allowEmpty = false): Record<string, unknown>[] {
  if (allowEmpty && !text.trim()) return [];
  let value: unknown;
  try { value = JSON.parse(text); } catch (e) {
    const firstLine = text.split(/\r?\n/).find(line => line.trim())?.trim() || '';
    if (text.trimStart().startsWith('[') || firstLine.startsWith('{') && !firstLine.endsWith('}')) {
      const position = /\bposition (\d+)/i.exec((e as Error).message)?.[1];
      const before = position === undefined ? text : text.slice(0, Number(position));
      const lines = before.split(/\r?\n/);
      throw new Error(`JSON 在第 ${lines.length} 行、第 ${lines.at(-1)!.length + 1} 列附近无法解析。`);
    }
    value = text.split(/\r?\n/).flatMap((line, index) => {
      if (!line.trim()) return [];
      try { return [JSON.parse(line)]; } catch { throw new Error(`第 ${index + 1} 行不是合法 JSON。`); }
    });
  }
  if (value && !Array.isArray(value) && typeof value === 'object') value = 'cases' in value ? value.cases : 'case_key' in value ? [value] : null;
  if (!Array.isArray(value) || !allowEmpty && !value.length) throw new Error('需要非空用例数组、cases 对象或 JSONL。');
  const invalid = value.findIndex(item => !item || typeof item !== 'object' || Array.isArray(item));
  if (invalid !== -1) throw new Error(`第 ${invalid + 1} 条用例必须是对象。`);
  return value as Record<string, unknown>[];
}

export function CaseEditor({ text, changed }: { text: string; changed: (s: string) => void }) {
  return <details className="case-editor"><summary>表格预览与基础编辑</summary><CaseRows key={text} text={text} changed={changed}/></details>;
}

function CaseRows({ text, changed }: { text: string; changed: (s: string) => void }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  let rows: Record<string, unknown>[] = [];
  let parseError = '';
  try { rows = parseCases(text, true); } catch (e) { parseError = (e as Error).message; }
  function publish(items: Record<string, unknown>[]) {
    if (parseError) return;
    let source;
    try { source = JSON.parse(text); } catch { /* Blank and JSONL input use an array. */ }
    const document = source && Array.isArray(source.cases) ? { ...source, cases: items } : items;
    changed(JSON.stringify(document, null, 2));
  }
  function add() {
    const keys = new Set(rows.map(row => row?.case_key));
    let index = rows.length + 1;
    while (keys.has(`case-${index}`)) index++;
    publish([...rows, { case_key: `case-${index}`, input: {}, assertions: {}, tags: [] }]);
  }
  function apply() {
    try {
      const value = JSON.parse(draft);
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('单条用例必须是对象。');
      publish(rows.map((row, index) => index === selected ? value : row));
    } catch (e) { setError((e as Error).message); }
  }
  return <>
    <ErrorBox message={parseError ? `${parseError} 请先修复原文，再继续表格编辑。` : error}/>
    {!parseError && <p>{rows.length ? `已解析 ${rows.length} 条用例。` : '空文档，可以添加第一条用例。'}</p>}
    {rows.length > 0 && <div className="table-scroll"><table>
      <thead><tr><th>行 / case_key</th><th>输入摘要</th><th>断言</th><th>标签 / 状态</th><th>操作</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}>
        <td>{index + 1} · {String(row?.case_key || '缺少 case_key')}</td><td>{JSON.stringify(row?.input)?.slice(0, 100)}</td>
        <td>{Object.keys((row?.assertions || {}) as object).join(', ') || '缺少断言'}</td>
        <td>{JSON.stringify(row?.tags || [])} · {row?.case_key && row?.input && row?.assertions ? '待服务端校验' : '字段不完整'}</td>
        <td><button type="button" className="text-button" onClick={() => { setSelected(index); setDraft(JSON.stringify(row, null, 2)); setError(''); }}>编辑用例 {index + 1}</button>
          <button type="button" className="text-button" onClick={() => publish(rows.filter((_, n) => n !== index))}>删除用例 {index + 1}</button></td>
      </tr>)}</tbody>
    </table></div>}
    <button type="button" className="button secondary" disabled={Boolean(parseError)} onClick={add}>添加用例</button>
    {selected !== null && <div>
      <label className="field"><span>单条用例完整 JSON（保留嵌套结构）</span><textarea rows={9} value={draft} onChange={e => setDraft(e.target.value)}/></label>
      <button type="button" className="button secondary" onClick={apply}>应用单条修改</button>
    </div>}
  </>;
}

interface VersionDiff {
  added: string[];
  removed: string[];
  modified: { case_key: string; before: unknown; after: unknown }[];
  unchanged: number;
}

export function BenchmarkDiff({ api, versions }: { api: API; versions: BenchmarkVersion[] }) {
  const [left, setLeft] = useState('');
  const [right, setRight] = useState('');
  const [value, setValue] = useState<VersionDiff | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  function versionChoice(label: string, id: string, select: (id: string) => void) {
    return <label className="field"><span>{label}</span><select value={id} disabled={busy}
      onChange={e => { select(e.target.value); setValue(null); setError(''); }}>
      <option value="">请选择</option>
      {versions.map(v => <option key={v.id} value={v.id}>{v.version} · {v.id.slice(0, 8)}</option>)}
    </select></label>;
  }
  async function compare() {
    setBusy(true); setError(''); setValue(null);
    try { setValue(await api.get<VersionDiff>(`benchmark-versions/${left}/diff?other_version_id=${right}`)); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <details><summary>查看固定版本新增 / 删除 / 修改差异</summary>
    <div className="form-grid">{versionChoice('数据集基线版本', left, setLeft)}{versionChoice('数据集候选版本', right, setRight)}</div>
    <button type="button" className="button secondary" disabled={busy || !left || !right || left === right} onClick={() => void compare()}>比较数据集版本</button>
    <ErrorBox message={error}/>
    {value && <div aria-label="数据集版本差异">
      <p>新增 {value.added.length} 条 · 删除 {value.removed.length} 条 · 修改 {value.modified.length} 条 · 未变更 {value.unchanged} 条</p>
      {value.added.length > 0 && <details><summary>新增用例</summary><ul>{value.added.map(key => <li key={key}>{key}</li>)}</ul></details>}
      {value.removed.length > 0 && <details><summary>删除用例</summary><ul>{value.removed.map(key => <li key={key}>{key}</li>)}</ul></details>}
      {value.modified.map(item => <details key={item.case_key}><summary>修改用例 {item.case_key}</summary>
        <div className="form-grid"><div><strong>基线</strong><Code value={item.before}/></div><div><strong>候选</strong><Code value={item.after}/></div></div>
      </details>)}
    </div>}
  </details>;
}
