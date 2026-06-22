#!/usr/bin/env python3
"""Validate the DeltaCam tile-delta codec on real ESP32-CAM frames, locally.
Compares bytes/frame of MJPEG (full JPEG each frame) vs DeltaCam (only changed
tiles), on whatever the camera is currently pointed at (real sensor noise)."""
import socket, time, sys, io
import numpy as np, cv2

HOST="192.168.1.32"; BOUND=b'123456789000000000000987654321'
# args: [n_frames] [secs] [thresh] [label]
N      = int(sys.argv[1]) if len(sys.argv)>1 else 100
SECS   = int(sys.argv[2]) if len(sys.argv)>2 else 12
THRESH = int(sys.argv[3]) if len(sys.argv)>3 else 10
LABEL  = sys.argv[4] if len(sys.argv)>4 else "result"
ROBUST = int(sys.argv[5]) if len(sys.argv)>5 else 0   # 1 = compensate global luma drift (AGC)
TILE=16; KEYFRAME_EVERY=30; QUALITY=12; WIFI_MBPS=1.8

def grab(n=100, secs=12):
    s=socket.create_connection((HOST,81),timeout=6); s.settimeout(6)
    s.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    buf=b''; frames=[]; t0=time.time()
    while len(frames)<n and time.time()-t0<secs:
        c=s.recv(65536)
        if not c: break
        buf+=c
        while True:
            a=buf.find(b'\xff\xd8'); b=buf.find(b'\xff\xd9',a+2) if a>=0 else -1
            if a<0 or b<0: break
            frames.append(buf[a:b+2]); buf=buf[b+2:]
    s.close(); return frames

def jpeg_bytes(img, q=QUALITY):
    ok,enc=cv2.imencode('.jpg',img,[cv2.IMWRITE_JPEG_QUALITY, max(1,min(100,int(100-q*1.5)))])
    return enc.nbytes if ok else 0

def simulate(frames, robust=0):
    imgs=[cv2.imdecode(np.frombuffer(f,np.uint8),cv2.IMREAD_COLOR) for f in frames]
    imgs=[i for i in imgs if i is not None]
    H,W=imgs[0].shape[:2]; ty,tx=H//TILE, W//TILE
    gray=[cv2.cvtColor(i,cv2.COLOR_BGR2GRAY).astype(np.int16) for i in imgs]
    ref=gray[0].copy(); refimg=imgs[0].copy()
    mjpeg=[]; delta=[]; dirty_counts=[]
    for n,(g,im,raw) in enumerate(zip(gray,imgs,frames)):
        mjpeg.append(len(raw))                       # actual MJPEG payload
        if n==0 or n%KEYFRAME_EVERY==0:
            delta.append(len(raw)); ref=g.copy(); refimg=im.copy(); dirty_counts.append(ty*tx); continue
        # per-tile mean abs diff (luma) vs last-sent reference
        diff=g-ref
        if robust: diff=diff-np.median(diff)   # cancel global brightness drift (AGC)
        d=np.abs(diff)
        # block-reduce to tile means
        tmean=d[:ty*TILE,:tx*TILE].reshape(ty,TILE,tx,TILE).mean(axis=(1,3))
        dirty=tmean>THRESH
        nd=int(dirty.sum()); dirty_counts.append(nd)
        if nd==0:
            delta.append(40); continue              # tiny "no change" packet
        # pack dirty tiles (color) into a strip and JPEG-encode -> realistic cost
        ys,xs=np.where(dirty)
        strip=np.zeros((nd*TILE,TILE,3),np.uint8)
        for k,(yy,xx) in enumerate(zip(ys,xs)):
            strip[k*TILE:(k+1)*TILE]=im[yy*TILE:(yy+1)*TILE, xx*TILE:(xx+1)*TILE]
            refimg[yy*TILE:(yy+1)*TILE, xx*TILE:(xx+1)*TILE]=im[yy*TILE:(yy+1)*TILE, xx*TILE:(xx+1)*TILE]
            ref[yy*TILE:(yy+1)*TILE, xx*TILE:(xx+1)*TILE]=g[yy*TILE:(yy+1)*TILE, xx*TILE:(xx+1)*TILE]
        cost=jpeg_bytes(strip)+nd*2+8               # tiles + index list + header
        delta.append(cost)
    return dict(W=W,H=H,tiles=ty*tx,n=len(imgs),
                mjpeg=mjpeg,delta=delta,dirty=dirty_counts)

def main():
    print(f"[{LABEL}] grabbing up to {N} frames ({SECS}s) from {HOST} ...")
    fr=grab(N, SECS)
    if len(fr)<5: print("not enough frames"); sys.exit(1)
    base=simulate(fr,robust=0); rob=simulate(fr,robust=1)
    n=base['n']; mj=sum(base['mjpeg'])
    print(f"\nscene: {base['W']}x{base['H']}, {base['tiles']} tiles of {TILE}x{TILE}, {n} frames, thresh={THRESH}")
    for tag,r in (("plain delta",base),("+AGC-compensated",rob)):
        dl=sum(r['delta']); dpct=100*np.mean(r['dirty'])/r['tiles']
        fps=WIFI_MBPS*1e6/8/(dl/n)
        print(f"  {tag:18} dirty={dpct:4.1f}%  {dl/n/1024:6.1f} KB/fr  reduction={mj/dl:5.1f}x  -> {min(fps,24):.1f} FPS (cap 24)")
    print(f"  {'MJPEG baseline':18} {' ':14}{mj/n/1024:6.1f} KB/fr  reduction=  1.0x  -> {min(WIFI_MBPS*1e6/8/(mj/n),24):.1f} FPS")
    r=rob if sum(rob['delta'])<sum(base['delta']) else base  # chart the better one
    # chart cumulative bytes
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        plt.figure(figsize=(9,4.5))
        plt.plot(np.cumsum(r['mjpeg'])/1024,label=f"MJPEG (full frames)",lw=2,color="#ef4444")
        plt.plot(np.cumsum(r['delta'])/1024,label=f"DeltaCam (tile-delta)",lw=2,color="#22c55e")
        plt.xlabel("frame"); plt.ylabel("cumulative KB sent")
        plt.title(f"DeltaCam vs MJPEG [{LABEL}] — {r['W']}x{r['H']}, {mj/sum(r['delta']):.1f}x less data")
        plt.legend(); plt.grid(alpha=.3); plt.tight_layout()
        out=f"sim/deltacam_{LABEL}.png"; plt.savefig(out,dpi=130); print(f"\nchart -> {out}")
    except Exception as e: print("chart skip:",e)

if __name__=="__main__": main()
