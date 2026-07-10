import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
files = [r'tools\llm_client.py', r'tools\llm_proposals.py']

for fn in files:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Fix: the pattern is 科技与医疗, not 科技和医疗
    text = text.replace('科技与医疗', '法律/法治')
    text = text.replace('科技 + 医疗', '法律/法治')
    text = text.replace('偏科普、严谨、可验证', '偏法规解读、案例分析、严谨、可验证')
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)

# Final check
for fn in files:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    sc = text.count('科技')
    yl = text.count('医疗')
    print(f'{fn}: 科技={sc}, 医疗={yl}')
