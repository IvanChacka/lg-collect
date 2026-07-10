import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
files = [r'tools\llm_client.py', r'tools\llm_proposals.py']

for fn in files:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Direct byte-level replacement for known patterns
    # These are the Chinese characters that still exist
    text = text.replace('科技和医疗', '法律/法治')
    text = text.replace('科技/医疗', '法律/法治')
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

# Verify
for fn in files:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    remaining = []
    for i, line in enumerate(text.split('\n')):
        if '科技' in line or '医疗' in line:
            remaining.append((i+1, line[:80]))
    print(f'{fn}: {len(remaining)} remaining')
    for ln, txt in remaining:
        print(f'  Line {ln}: {txt}')
