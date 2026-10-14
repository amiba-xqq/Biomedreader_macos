from pathlib import Path
import subprocess
from PIL import Image
root=Path(__file__).resolve().parents[1];folder=root/'build/BiomedReader.iconset';folder.mkdir(parents=True,exist_ok=True)
image=Image.open(root/'app/assets/app_icon.png').convert('RGBA')
for size in (16,32,128,256,512):
    image.resize((size,size),Image.Resampling.LANCZOS).save(folder/f'icon_{size}x{size}.png')
    image.resize((size*2,size*2),Image.Resampling.LANCZOS).save(folder/f'icon_{size}x{size}@2x.png')
subprocess.run(['iconutil','-c','icns',str(folder),'-o',str(root/'build/BiomedReader.icns')],check=True)
