import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
files_to_check = [r'tools\llm_client.py', r'tools\llm_proposals.py']

for fn in files_to_check:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if '科技' in line or '医疗' in line:
            print(f'{fn}:{i+1}: {line.rstrip()[:120]}')
