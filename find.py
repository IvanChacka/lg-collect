import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
for fn in [r'tools\llm_client.py', r'tools\llm_proposals.py']:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    print(f'=== {fn} ===')
    for i, line in enumerate(lines):
        if '科技' in line or '医疗' in line:
            print(f'  Line {i+1}: {line.rstrip()[:100]}')
