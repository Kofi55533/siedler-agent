#!/usr/bin/env python3
"""vtables.py <SettlersHoK.exe> <out.json>: the vtables of the game executable by their RTTI (MSVC complete object
locators in .rdata): {vtable address: {class, offset, slots}}. Feeds bauplan.py (method -> class). Prints counts only.
"""
import struct, json, sys, collections
b=open(sys.argv[1],'rb').read()
pe=struct.unpack_from('<I',b,0x3c)[0]; n=struct.unpack_from('<H',b,pe+6)[0]; opt=struct.unpack_from('<H',b,pe+20)[0]
base=0x400000; secs=[]
for i in range(n):
    name,vs,va,rs,ra=struct.unpack_from('<8sIIII',b,pe+24+opt+40*i); secs.append((name.rstrip(b'\0').decode(),va+base,vs,ra,rs))
def off(a):
    for nm,va,vs,ra,rs in secs:
        if va<=a<va+min(vs,rs): return ra+a-va
def u(a):
    o=off(a); return None if o is None else struct.unpack_from('<I',b,o)[0]
text=[s for s in secs if s[0]=='.text'][0]; is_text=lambda x: x is not None and text[1]<=x<text[1]+text[2]
rd=[s for s in secs if s[0]=='.rdata'][0]
def tdname(td):
    o=off(td+8)
    if o is None: return None
    e=b.find(b'\0',o,o+512); s=b[o:e]
    return s.decode('latin1') if s.startswith(b'.?A') else None
vt={}
for a in range(rd[1],rd[1]+rd[2]-4,4):
    col=u(a)
    if col is None or not (rd[1]<=col<rd[1]+rd[2]): continue
    if u(col)!=0: continue
    td=u(col+12)
    nm=tdname(td) if td else None
    if not nm: continue
    v=a+4; slots=[]
    while True:
        f=u(v+4*len(slots))
        if not is_text(f) or len(slots)>400: break
        slots.append(f)
    if slots: vt[v]={'class':nm,'offset':u(col+4),'slots':[hex(s) for s in slots]}
json.dump({hex(k):v for k,v in vt.items()},open(sys.argv[2],'w'))
print('vtables',len(vt),'slots',sum(len(v['slots']) for v in vt.values()))
