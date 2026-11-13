import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from 'react';

type Theme = 'dark' | 'light' | 'system';
const storageKey = 'agenteval-theme';
const ThemeContext = createContext<{ theme: Theme; choose: (theme: Theme) => void }>({ theme: 'dark', choose: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem(storageKey);
    return stored === 'light' || stored === 'system' ? stored : 'dark';
  });
  useLayoutEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: light)');
    function apply() {
      document.documentElement.dataset.theme = theme === 'system' ? media.matches ? 'light' : 'dark' : theme;
    }
    apply();
    if (theme === 'system') media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [theme]);
  function choose(value: Theme) {
    localStorage.setItem(storageKey, value);
    setTheme(value);
  }
  return <ThemeContext.Provider value={{ theme, choose }}>{children}</ThemeContext.Provider>;
}

export function ThemeSelect() {
  const { theme, choose } = useContext(ThemeContext);
  return <label className="theme-select"><span>主题</span><select aria-label="主题" value={theme} onChange={e => choose(e.target.value as Theme)}>
    <option value="dark">深色</option><option value="light">浅色</option><option value="system">跟随系统</option>
  </select></label>;
}
