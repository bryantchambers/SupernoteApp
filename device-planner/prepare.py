"""Overlay our source into an orchestrator-provisioned official RN plugin scaffold.

Usage: python device-planner/prepare.py /path/to/SupernotePlanner
Does not install dependencies or modify system tools.
"""
import json
from pathlib import Path
import shutil
import sys
import struct
import zlib

source = Path(__file__).resolve().parent
target = Path(sys.argv[1]).resolve()
if not (target / 'android').is_dir() or not (target / 'buildPlugin.sh').is_file():
    raise SystemExit('Create the official React Native 0.79.2 plugin scaffold first; see README.md.')
for name in ['App.jsx', 'index.js', 'engine.js', 'core.mjs', 'PluginConfig.json']:
    shutil.copy2(source / name, target / name)
for obsolete in ['App.tsx', 'App.js']:
    (target / obsolete).unlink(missing_ok=True)
(target / 'app.json').write_text(json.dumps({'name': 'SupernotePlanner', 'displayName': 'Supernote Planner'}, indent=2))
# Generate the tiny monochrome toolbar asset without another imaging dependency.
def chunk(name, data):
    return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff)

pixels = bytearray()
for y in range(64):
    pixels.append(0)
    for x in range(64):
        border = (8 <= x <= 55 and (y in range(12, 15) or y in range(53, 56))) or (12 <= y <= 55 and (x in range(8, 11) or x in range(53, 56)))
        header = 8 <= x <= 55 and 23 <= y <= 25
        binders = (18 <= x <= 21 or 42 <= x <= 45) and 7 <= y <= 18
        dots = any(left <= x <= left + 4 and top <= y <= top + 4 for left in (18, 30, 42) for top in (32, 43))
        pixels.extend((0, 0, 0, 255 if border or header or binders or dots else 0))
assets = target / 'assets'
assets.mkdir(exist_ok=True)
(assets / 'planner.png').write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,6,0,0,0)) + chunk(b'IDAT',zlib.compress(bytes(pixels))) + chunk(b'IEND',b''))
native = target / 'android/app/src/main/java/com/supernoteplanner'
native.mkdir(parents=True, exist_ok=True)
for path in (source / 'native').glob('*.kt'):
    shutil.copy2(path, native / path.name)
metro = target / 'metro.config.js'
metro.write_text("const {getDefaultConfig, mergeConfig} = require('@react-native/metro-config');\nmodule.exports = mergeConfig(getDefaultConfig(__dirname), {resolver:{sourceExts:['js','jsx','json','ts','tsx','mjs']}});\n")
# Register the native module in the official scaffold's package list.
mains = list((target / 'android/app/src/main/java').rglob('MainApplication.kt'))
if len(mains) != 1:
    raise SystemExit('Expected one MainApplication.kt. Native package registration requires inspection.')
main = mains[0]
text = main.read_text()
if 'add(com.supernoteplanner.PlannerStoragePackage())' not in text:
    needle = 'PackageList(this).packages.apply {'
    if needle not in text:
        raise SystemExit('Official scaffold package-list layout changed; inspect before building.')
    text = text.replace(needle, needle + '\n              add(com.supernoteplanner.PlannerStoragePackage())', 1)
    main.write_text(text)
print('Source prepared. Pin sn-plugin-lib 0.2.3 in the build workspace, then build the full native plugin package.')
