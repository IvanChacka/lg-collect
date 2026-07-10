# Replace tech/medical references with law references in llm_client.py
import re

with open(r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect\tools\llm_client.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Count occurrences
print(f"Total '科技' occurrences: {text.count('科技')}")
print(f"Total '医疗' occurrences: {text.count('医疗')}")

# Targeted replacements
replacements = [
    ('严谨的科技/医疗内容编辑', '严谨的法律/法治内容编辑'),
    ('严谨的科技/医疗研究员', '严谨的法律/法治研究员'),
    ('你是一个科技和医疗领域的短视频选题策划', '你是一个法律/法治领域的短视频选题策划'),
    ('账号定位：科技 + 医疗，偏科普、严谨、可验证', '账号定位：法律/法治，偏法规解读、案例分析、严谨、可验证'),
    ('体现科技 + 医疗账号调性', '体现法律/法治账号调性'),
    ('严谨、可验证、科技感', '严谨、可验证、法律感'),
    ('科技类、医疗类为主', '法律/法治类为主'),
    ('科技', '法律/法治'),
    ('医疗', '法律/法治'),
]

# Do them one by one (but be careful with '科技' and '医疗' as they might appear in other contexts)
# Let me do specific context-aware replacements first
text = text.replace('严谨的科技/医疗内容编辑', '严谨的法律/法治内容编辑')
text = text.replace('严谨的科技/医疗研究员', '严谨的法律/法治研究员')
text = text.replace('你是一个科技和医疗领域的短视频选题策划', '你是一个法律/法治领域的短视频选题策划')

# For the general "科技类、医疗类" and similar patterns
text = text.replace('科技类、医疗类为主', '法律/法治类为主')
text = text.replace('科技 + 医疗，偏科普', '法律/法治，偏法规解读')
text = text.replace('科技 + 医疗账号', '法律/法治账号')
text = text.replace('科技', '法律/法治')
text = text.replace('医疗', '法律/法治')

with open(r'C:\Users\Winst\.openclaw\workspace\lg-hotcollect\tools\llm_client.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Done!")
# Count remaining
print(f"Remaining '科技' occurrences: {text.count('科技')}")
print(f"Remaining '医疗' occurrences: {text.count('医疗')}")
