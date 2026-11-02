"""Derive catalogue tokens and contrast from the sole stylesheet input."""
from pathlib import Path
import re,json
C=Path(__file__).resolve().parents[1]
s=(C/'core/styles/theme.css').read_text()
tokens=[{'name':a,'value':b.strip()} for a,b in re.findall(r'--([\w-]+):([^;{}]+);',s.split('@layer base')[0]) if a!='color-']
colors={a['name'][6:]:a['value'] for a in tokens if a['name'].startswith('color-')}
def lum(x):
 vals=[int(x[i:i+2],16)/255 for i in (1,3,5)];return sum((v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4)*w for v,w in zip(vals,[.2126,.7152,.0722]))
def ratio(a,b):
 l,h=sorted([lum(colors[a]),lum(colors[b])]);return round((h+.05)/(l+.05),2)
pairs=[]
for bg in ['page','surface']:
 for fg in ['text','muted','primary','accent']:pairs.append((fg,bg,'Text'))
for fg,bg in [('primary','primary-soft'),('accent','accent-soft'),('success','success-bg'),('warning','warning-bg'),('danger','danger-bg'),('pending','pending-bg')]:pairs.append((fg,bg,'Text and status symbol'))
for bg in ['primary','success','danger']:pairs.append(('surface',bg,'Button label'))
for bg in ['surface','page','primary-soft','pending-bg','accent-soft','success-bg','warning-bg','danger-bg']:pairs.extend([('control',bg,'Control boundary'),('focus',bg,'Focus outline')])
for fg in ['violet','luminous','moon']:pairs.append((fg,'night','Reserved; not active in F0'))
pairs.append(('line','surface','Decorative only, never a control'))
refs={'tokens':tokens,'contrast':[{'pair':a+' / '+b,'ratio':ratio(a,b),'use':u} for a,b,u in pairs]}
def reference_bytes():
    return (json.dumps(refs,indent=2)+'\n').encode('utf-8')
