import os

root = r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect'
files = [r'tools\llm_client.py', r'tools\llm_proposals.py']

for fn in files:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Replace remaining 科技 and 医疗 references in prompt contexts
    text = text.replace('你是一个科技和医疗领域的短视频选题策划', '你是一个法律/法治领域的短视频选题策划')
    text = text.replace('你是一个科技/医疗领域的短视频选题策划', '你是一个法律/法治领域的短视频选题策划')
    text = text.replace('账号定位：科技 + 医疗，偏科普、严谨、可验证', '账号定位：法律/法治，偏法规解读、案例分析、严谨、可验证')
    text = text.replace('与账号定位的匹配度：科技 + 医疗，偏科普、严谨、可验证', '与账号定位的匹配度：法律/法治，偏法规解读、案例分析、严谨、可验证')
    text = text.replace('体现科技 + 医疗账号调性', '体现法律/法治账号调性')
    
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    
    print(f'{fn}: done')

# Verify
for fn in files:
    path = os.path.join(root, fn)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    print(f'{fn}: 科技={text.count("科技")}, 医疗={text.count("医疗")}')
