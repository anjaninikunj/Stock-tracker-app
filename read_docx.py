import zipfile
import xml.etree.ElementTree as ET
import os

docx_path = r"C:\1 Minutes Scalping_strategi_transcript.docx"
txt_path = r"e:\AI 2026\chartink-telegram-alerts\transcript.txt"

if not os.path.exists(docx_path):
    print(f"Error: {docx_path} does not exist.")
    exit(1)

try:
    with zipfile.ZipFile(docx_path) as z:
        xml_content = z.read('word/document.xml')
        root = ET.fromstring(xml_content)
        
        paragraphs = []
        for elem in root.iter():
            if elem.tag.endswith('p'):
                texts = []
                for child in elem.iter():
                    if child.tag.endswith('t') and child.text:
                        texts.append(child.text)
                paragraphs.append("".join(texts))
                
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(paragraphs))
            
        print(f"Successfully converted {docx_path} to {txt_path}")
except Exception as e:
    print(f"Error reading docx: {e}")
