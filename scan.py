import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
# Exclude our scripts and .venv
exclude_dirs = {'.venv', '__pycache__', '.data', 'vendor', '.git', 'notebooks', 'doc'}
exclude_files = {'check_remaining.py', 'find_remaining.py', 'fix_remaining.py', 'fix2.py', 'fix3.py', 'replace_prompts.py', 'find.py'}

for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
    for fn in filenames:
        if not fn.endswith('.py'):
            continue
        if fn in exclude_files:
            continue
        path = os.path.join(dirpath, fn)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        sc = content.count('科技')
        yl = content.count('医疗')
        if sc > 0 or yl > 0:
            rel = os.path.relpath(path, root)
            print(f'{rel}: 科技={sc}, 医疗={yl}')
print('Done scanning')
