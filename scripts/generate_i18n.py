#!/usr/bin/env python3
"""
Generate i18n.tsx (frontend) and i18n_backend.py (backend)
from a single shared/i18n.json source of truth.

Usage:
    python3 scripts/generate_i18n.py          # generate both
    python3 scripts/generate_i18n.py --check  # exit 1 if outputs would change (CI)
"""

import json, os, sys, subprocess, ast

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_JSON = os.path.join(ROOT, 'shared', 'i18n.json')
BACKEND_OUT = os.path.join(ROOT, 'i18n_backend.py')
FRONTEND_OUT = os.path.join(ROOT, '..', 'snail-books-web', 'src', 'i18n.tsx')

# ── 1. Extract current backend translations ──

def extract_backend():
    """Parse TRANSLATIONS dict from i18n_backend.py using ast.literal_eval."""
    be_path = os.path.join(ROOT, 'i18n_backend.py')
    with open(be_path) as f:
        content = f.read()
    start = content.index('TRANSLATIONS = {')
    depth = 0
    end = start
    for i, c in enumerate(content[start:], start):
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: end = i + 1; break
    dict_str = content[start:end].split('=', 1)[1].strip()
    return ast.literal_eval(dict_str)


# ── 2. Extract current frontend translations ──

def extract_frontend():
    """Evaluate the I18N const from i18n.tsx using Node.js."""
    fe_path = os.path.join(ROOT, '..', 'snail-books-web', 'src', 'i18n.tsx')
    script = """
const fs = require('fs');
let c = fs.readFileSync(process.argv[1], 'utf8');
let s = c.indexOf('const I18N');
let b = c.indexOf('{', s);
let d = 0, e = b;
for (let i = b; i < c.length; i++) {
    if (c[i] === '{') d++; else if (c[i] === '}') { d--; if (d === 0) { e = i+1; break; } }
}
console.log(JSON.stringify(eval('(' + c.substring(b, e) + ')')));
"""
    result = subprocess.run(
        ['node', '-e', script, fe_path],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract frontend i18n: {result.stderr}")
    return json.loads(result.stdout)


# ── 3. Build unified JSON ──

def build_unified(be_data, fe_data):
    """Merge backend + frontend into shared/i18n.json format.
    
    Structure:
    {
      "zh-CN": {
        "common": { ... keys present in BOTH ... },
        "frontend": { ... front-end-only keys ... },
        "backend": { ... back-end-only keys ... }
      },
      ...
    }
    """
    langs = ['zh-CN', 'zh-TW', 'en']
    unified = {}
    for lang in langs:
        be_keys = set(be_data.get(lang, {}).keys())
        fe_keys = set(fe_data.get(lang, {}).keys())
        common_keys = be_keys & fe_keys
        fe_only = fe_keys - be_keys
        be_only = be_keys - fe_keys

        unified[lang] = {
            'common': {k: fe_data[lang][k] for k in sorted(common_keys)},
            'frontend': {k: fe_data[lang][k] for k in sorted(fe_only)},
            'backend': {k: be_data[lang][k] for k in sorted(be_only)},
        }
    return unified


# ── 4. Generate backend i18n_backend.py ──

def generate_backend(unified):
    """Generate i18n_backend.py from unified JSON."""
    lines = ['"""后端 i18n 翻译模块 — 由 scripts/generate_i18n.py 自动生成，勿手动编辑"""', '']
    lines.append('TRANSLATIONS = {')

    for lang in ['zh-CN', 'zh-TW', 'en']:
        data = unified[lang]
        merged = {**data['common'], **data['backend']}
        lines.append(f"    '{lang}': {{")
        for k in sorted(merged):
            v = merged[k].replace("'", "\\'")
            lines.append(f"        '{k}': '{v}',")
        lines.append('    },')

    lines.append('}')
    lines.append('')

    # Append get_lang() and t() functions
    lines.append('''
def get_lang(request):
    """从请求头获取语言，优先级：X-Lang > Accept-Language > 默认 zh-CN"""
    x_lang = request.headers.get('X-Lang', '')
    if x_lang in TRANSLATIONS:
        return x_lang
    best = request.accept_languages.best_match(['zh-CN', 'zh-TW', 'en'], default='zh-CN')
    return best


def t(key, lang='zh-CN', **kwargs):
    """翻译单个 key"""
    msg = TRANSLATIONS.get(lang, TRANSLATIONS['zh-CN']).get(key, key)
    if kwargs:
        msg = msg.format(**kwargs)
    return msg
''')
    return '\n'.join(lines) + '\n'


# ── 5. Generate frontend i18n.tsx ──

def generate_frontend(unified):
    """Generate i18n.tsx from unified JSON."""
    lines = [
        "import React, { createContext, useContext, useState, useCallback } from 'react';",
        '',
        'const I18N: Record<string, Record<string, string>> = {',
    ]

    for lang in ['zh-CN', 'zh-TW', 'en']:
        data = unified[lang]
        merged = {**data['common'], **data['frontend']}
        lines.append(f"  '{lang}': {{")
        for k in sorted(merged):
            v = merged[k].replace("'", "\\'")
            lines.append(f"    {k}: '{v}',")
        lines.append('  },')

    lines.append('};')
    lines.append('')

    # Append LangContext + LangProvider + useLang
    lines.append('''type Lang = 'zh-CN' | 'zh-TW' | 'en';

export const langs: [Lang, string][] = [
  ['zh-CN', '简'],
  ['zh-TW', '繁'],
  ['en', 'EN'],
];

export function t(key: string): string {
  const lang = (typeof window !== 'undefined' ? (window as any).curLang : null) || 'zh-CN';
  return I18N[lang]?.[key] || I18N['zh-CN']?.[key] || key;
}

export function getLang(): string {
  if (typeof window !== 'undefined') {
    return (window as any).curLang || 'zh-CN';
  }
  return 'zh-CN';
}

interface LangContextValue {
  lang: string;
  setLang: (lang: string) => void;
}

const LangContext = createContext<LangContextValue>({
  lang: 'zh-CN',
  setLang: () => {},
});

export function LangProvider({ children }: { children: React.ReactNode }): React.ReactNode {
  const [lang, setLangState] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return (window as any).curLang || localStorage.getItem('lang') || 'zh-CN';
    }
    return 'zh-CN';
  });

  const setLang = useCallback((l: string) => {
    setLangState(l);
    if (typeof window !== 'undefined') {
      (window as any).curLang = l;
      try { localStorage.setItem('lang', l); } catch {}
    }
  }, []);

  return (
    <LangContext.Provider value={{ lang, setLang }}>
      {children}
    </LangContext.Provider>
  );
}

export function useLang() {
  return useContext(LangContext);
}
''')
    return '\n'.join(lines) + '\n'


# ── Main ──

def main():
    check_mode = '--check' in sys.argv

    be_data = extract_backend()
    fe_data = extract_frontend()
    unified = build_unified(be_data, fe_data)

    # Write shared JSON
    with open(SHARED_JSON, 'w', encoding='utf-8') as f:
        json.dump(unified, f, ensure_ascii=False, indent=2)
    print(f"✅ Wrote {SHARED_JSON}")

    # Generate backend
    be_output = generate_backend(unified)
    be_output = be_output.strip() + '\n'

    # Generate frontend
    fe_output = generate_frontend(unified)
    fe_output = fe_output.strip() + '\n'

    if check_mode:
        # Read existing files and compare
        with open(BACKEND_OUT) as f:
            existing_be = f.read()
        with open(FRONTEND_OUT) as f:
            existing_fe = f.read()
        ok = True
        if existing_be != be_output:
            print("❌ Backend i18n_backend.py is out of sync with shared/i18n.json")
            ok = False
        if existing_fe != fe_output:
            print("❌ Frontend i18n.tsx is out of sync with shared/i18n.json")
            ok = False
        if ok:
            print("✅ All i18n files are in sync")
    else:
        # Check if outputs would change
        be_changed = True
        fe_changed = True
        if os.path.exists(BACKEND_OUT):
            with open(BACKEND_OUT) as f:
                be_changed = f.read() != be_output
        if os.path.exists(FRONTEND_OUT):
            with open(FRONTEND_OUT) as f:
                fe_changed = f.read() != fe_output

        with open(BACKEND_OUT, 'w', encoding='utf-8') as f:
            f.write(be_output)
        print(f"{'✏️  Updated' if be_changed else '✅ No change'} {BACKEND_OUT}")

        with open(FRONTEND_OUT, 'w', encoding='utf-8') as f:
            f.write(fe_output)
        print(f"{'✏️  Updated' if fe_changed else '✅ No change'} {FRONTEND_OUT}")

    # Print stats
    for lang in ['zh-CN', 'zh-TW', 'en']:
        d = unified[lang]
        n_common = len(d['common'])
        n_fe = len(d['frontend'])
        n_be = len(d['backend'])
        print(f"  {lang}: common={n_common}, frontend={n_fe}, backend={n_be} (total={n_common+n_fe+n_be})")


if __name__ == '__main__':
    main()
