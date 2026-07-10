import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
for dirpath, dirnames, filenames in os.walk(root):
    for fn in filenames:
        if not fn.endswith('.py'):
            continue
        path = os.path.join(dirpath, fn)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        sc = content.count('科技')
        yl = content.count('医疗')
        if sc > 0 or yl > 0:
            rel = os.path.relpath(path, root)
            print(f'{rel}: 科技={sc}, 医疗={yl}')
