import zipfile,xml.etree.ElementTree as ET
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'app/assets/mac_word/BiomedReader.dotm'
with zipfile.ZipFile(p) as z:
    assert z.testzip() is None
    assert z.read('word/vbaProject.bin').startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
    ET.fromstring(z.read('customUI/customUI14.xml'))
    assert b'BiomedReaderRibbon' in z.read('_rels/.rels')
print('Native VBA template, embedded project and Ribbon validated.')
