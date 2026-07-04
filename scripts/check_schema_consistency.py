#!/usr/bin/env python3
"""CI 检查：比对 routes/*.py 中 INSERT 列名 与 app.py CREATE TABLE 列名，不一致则失败。

覆盖场景：
- INSERT 引用了 CREATE TABLE 中没有的列 → 报错（本次 bug）
- CREATE TABLE 有但 INSERT 没写 → 不报错（可选列是正常的）
"""

import re, sys, os

def extract_create_table_columns(content):
    """从 app.py 中提取所有 CREATE TABLE IF NOT EXISTS 的列名。
    返回 {table_name: {col1, col2, ...}}"""
    tables = {}
    # 匹配 CREATE TABLE IF NOT EXISTS table_name (...);
    # 需要跨行匹配，非贪婪
    pattern = re.compile(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\);",
        re.DOTALL | re.IGNORECASE
    )
    for m in pattern.finditer(content):
        table = m.group(1)
        body = m.group(2)
        cols = set()
        # 提取列定义的第一部分（列名）
        for line in body.split('\n'):
            line = line.strip()
            if not line or line.startswith('--') or line.startswith('#'):
                continue
            # 匹配: col_name TYPE ... 或 col_name TYPE ...
            col_match = re.match(r'(\w+)\s', line)
            if col_match:
                col = col_match.group(1)
                # 跳过 SQL 关键字
                if col.upper() in ('PRIMARY', 'FOREIGN', 'UNIQUE', 'CHECK', 'CONSTRAINT', 'INDEX'):
                    continue
                cols.add(col)
        tables[table] = cols
    return tables


def extract_insert_columns(content):
    """从路由文件中提取所有 INSERT INTO table (col1, col2, ...) 的列名。
    返回 [(file, table, {cols}), ...]"""
    results = []
    # 匹配 INSERT INTO table_name (col1, col2, ...)
    pattern = re.compile(
        r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)",
        re.IGNORECASE
    )
    for m in pattern.finditer(content):
        table = m.group(1)
        cols_str = m.group(2)
        cols = {c.strip() for c in cols_str.split(',') if c.strip()}
        results.append((table, cols))
    return results


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir) if script_dir.endswith('scripts') else script_dir
    os.chdir(project_root)

    # 1. 读取 app.py 中的 CREATE TABLE 定义
    with open('app.py', 'r') as f:
        app_content = f.read()
    schema = extract_create_table_columns(app_content)
    print(f"📋 从 app.py 提取到 {len(schema)} 个表的 CREATE TABLE 定义:")
    for t, cols in sorted(schema.items()):
        print(f"   {t}: {sorted(cols)}")

    # 2. 读取所有 routes/*.py 中的 INSERT 语句
    route_dir = os.path.join(project_root, 'routes')
    if not os.path.isdir(route_dir):
        print("⚠️  routes/ 目录不存在，跳过检查")
        return 0

    errors = []
    for fname in sorted(os.listdir(route_dir)):
        if not fname.endswith('.py') or fname.startswith('__'):
            continue
        fpath = os.path.join(route_dir, fname)
        with open(fpath, 'r') as f:
            content = f.read()
        inserts = extract_insert_columns(content)
        for table, cols in inserts:
            if table not in schema:
                print(f"⚠️  {fname}: INSERT INTO {table} — 表没有在 app.py 中定义 CREATE TABLE，跳过")
                continue
            missing = cols - schema[table]
            if missing:
                msg = f"❌ {fname}: INSERT INTO {table} 引用了 CREATE TABLE 中不存在的列: {sorted(missing)}"
                print(msg)
                errors.append(msg)
            else:
                print(f"✅ {fname}: INSERT INTO {table} — 列一致 ({len(cols)} 列)")

    if errors:
        print(f"\n🚨 发现 {len(errors)} 个 schema 不一致：")
        for e in errors:
            print(f"   {e}")
        print("\n请同步更新 app.py 的 CREATE TABLE 定义和 ALTER TABLE 迁移。")
        return 1

    print("\n✅ 所有 INSERT 列名与 CREATE TABLE 定义一致")
    return 0


if __name__ == '__main__':
    sys.exit(main())
