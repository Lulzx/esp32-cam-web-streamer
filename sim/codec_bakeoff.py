#!/usr/bin/env python3
"""Rate-distortion bake-off on a controlled stress sequence:
MJPEG vs DeltaCam vs BG-Delta vs MoCo-residual vs ReCAST (novel).
Measures bytes/frame, reconstruction SSIM, and (ReCAST) cache-hit rate."""
import sys, numpy as np, cv2
TILE=16; KEY=30; THRESH=8; TOL=6      # TOL = Hamming tolerance for noise-robust hashing
NFR=75

def synth(n=NFR):
    """A realistic (structured, JPEG-compressible) scene with mild sensor noise."""
    H,W=480,640
    rng=np.random.default_rng(7)
    yy,xx=np.meshgrid(np.linspace(0,1,H),np.linspace(0,1,W),indexing='ij')
    bg=np.stack([(60+150*xx),(80+120*yy),(120+80*(1-xx))],axis=2).astype(np.uint8)  # smooth gradient
    cv2.rectangle(bg,(60,60),(220,200),(70,110,180),-1)        # structured shapes
    cv2.circle(bg,(470,150),70,(180,170,60),-1)
    cv2.putText(bg,"ROOM CAM",(180,360),cv2.FONT_HERSHEY_SIMPLEX,2,(240,240,240),4)
    out=[]
    for i in range(n):
        f=bg.copy()
        x=int(20+i*7)%(W-80)                                    # moving box
        f[200:280,x:x+70]=(40,200,40)
        f=np.clip(f.astype(np.int16)+rng.integers(-4,5,f.shape),0,255).astype(np.uint8)  # mild noise
        if 30<=i<45: f=np.clip(f.astype(np.int16)+22,0,255).astype(np.uint8)  # AGC drift
        if 50<=i<65: f=np.roll(f,(i-50)*4,axis=1)              # global pan
        out.append(f)
    return out

def gray(im): return cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
def enc(im,q): return cv2.imencode('.jpg',im,[cv2.IMWRITE_JPEG_QUALITY,q])[1]
def dec(b): return cv2.imdecode(np.frombuffer(b.tobytes(),np.uint8),cv2.IMREAD_COLOR)
def ssim(a,b):   # PSNR in dB (higher = better); bulletproof R-D metric
    mse=np.mean((a.astype(np.float64)-b.astype(np.float64))**2)
    return 99.0 if mse<1e-9 else float(10*np.log10(255*255/mse))

def crop(im): H,W=im.shape[:2]; return im[:H//TILE*TILE,:W//TILE*TILE]

def mjpeg(imgs,q):
    B=[];S=[]
    for im in imgs:
        e=enc(im,q);B.append(e.nbytes);S.append(ssim(gray(dec(e)),gray(im)))
    return np.array(B),np.array(S),0.0

def tile_replenish(imgs,q,background):
    H,W=imgs[0].shape[:2];ty,tx=H//TILE,W//TILE
    canvas=imgs[0].copy();ref=gray(canvas).astype(np.float32);bg=ref.copy();B=[];S=[]
    for n,im in enumerate(imgs):
        if n%KEY==0:
            e=enc(im,q);canvas=dec(e);ref=gray(canvas).astype(np.float32);bg=ref.copy();B.append(e.nbytes)
        else:
            g=gray(im).astype(np.float32);cmp=bg if background else ref
            tm=np.abs(g-cmp).reshape(ty,TILE,tx,TILE).mean(axis=(1,3))
            ys,xs=np.where(tm>THRESH);nd=len(ys)
            if nd==0: B.append(30)
            else:
                strip=np.zeros((nd*TILE,TILE,3),np.uint8)
                for k,(yy,xx) in enumerate(zip(ys,xs)): strip[k*TILE:(k+1)*TILE]=im[yy*TILE:(yy+1)*TILE,xx*TILE:(xx+1)*TILE]
                e=enc(strip,q);ds=dec(e)
                for k,(yy,xx) in enumerate(zip(ys,xs)): canvas[yy*TILE:(yy+1)*TILE,xx*TILE:(xx+1)*TILE]=ds[k*TILE:(k+1)*TILE]
                B.append(e.nbytes+nd*2)
            ref=gray(canvas).astype(np.float32)
            if background: bg=0.92*bg+0.08*g
        S.append(ssim(gray(canvas),gray(im)))
    return np.array(B),np.array(S),0.0

def moco(imgs,q):
    H,W=imgs[0].shape[:2];recon=imgs[0].copy();pg=gray(imgs[0]).astype(np.float32);B=[];S=[]
    for n,im in enumerate(imgs):
        if n%KEY==0:
            e=enc(im,q);recon=dec(e);B.append(e.nbytes)
        else:
            g=gray(im).astype(np.float32)
            try:(dx,dy),_=cv2.phaseCorrelate(pg,g)
            except:dx=dy=0.0
            pred=cv2.warpAffine(recon,np.float32([[1,0,dx],[0,1,dy]]),(W,H),borderMode=cv2.BORDER_REPLICATE)
            res=np.clip(im.astype(np.int16)-pred.astype(np.int16)+128,0,255).astype(np.uint8)
            e=enc(res,q);rd=dec(e).astype(np.int16)-128
            recon=np.clip(pred.astype(np.int16)+rd,0,255).astype(np.uint8);B.append(e.nbytes)
        pg=gray(im).astype(np.float32);S.append(ssim(gray(recon),gray(im)))
    return np.array(B),np.array(S),0.0

def block_hashes(g):
    """64-bit aHash per 16x16 block (vectorized). g: HxW gray uint8. -> dict of (yy,xx)->int."""
    H,W=g.shape;ty,tx=H//TILE,W//TILE
    g2=g.reshape(H//2,2,W//2,2).mean(axis=(1,3))                # 2x2 downsample
    blk=g2.reshape(ty,8,tx,8)                                   # 8x8 per block
    bm=blk.mean(axis=(1,3),keepdims=True)
    bits=(blk>bm).reshape(ty,8,tx,8).transpose(0,2,1,3).reshape(ty,tx,64)
    packed=np.packbits(bits.astype(np.uint8),axis=2)            # (ty,tx,8) bytes
    return {(yy,xx):int.from_bytes(packed[yy,xx].tobytes(),'big') for yy in range(ty) for xx in range(tx)}

def block_means(g):
    H,W=g.shape;ty,tx=H//TILE,W//TILE
    return g.reshape(ty,TILE,tx,TILE).mean(axis=(1,3))           # (ty,tx) luma mean

MEAN_TOL=4
def recast(imgs,q):
    """ReCAST v2: content-addressed block dedup + 1-byte luma-mean correction."""
    H,W=imgs[0].shape[:2];ty,tx=H//TILE,W//TILE
    cache={};B=[];S=[];hit=0;chg=0                              # cache: hash -> (pixels, luma_mean)
    e=enc(imgs[0],q);canvas=dec(e);B.append(e.nbytes)
    ph=block_hashes(gray(canvas));pm=block_means(gray(canvas))  # painted hash/mean per position
    for (yy,xx),h in ph.items():
        blk=canvas[yy*TILE:(yy+1)*TILE,xx*TILE:(xx+1)*TILE]
        cache[h]=(blk.copy(),float(pm[yy,xx]))
    S.append(ssim(gray(canvas),gray(imgs[0])))
    for n in range(1,len(imgs)):
        im=imgs[n];g=gray(im);cur=block_hashes(g);cm=block_means(g);cost=8;new=[]
        nph={};npm=pm.copy()
        for (yy,xx),h in cur.items():
            same_hash=(h^ph[(yy,xx)]).bit_count()<=TOL
            same_mean=abs(cm[yy,xx]-pm[yy,xx])<=MEAN_TOL
            if same_hash and same_mean: nph[(yy,xx)]=ph[(yy,xx)]; continue   # truly unchanged
            chg+=1
            hk=h if h in cache else next((ck for ck in cache if (h^ck).bit_count()<=TOL),None)
            if hk is not None:                                  # cache hit + brightness correct
                hit+=1; cblk,cmean=cache[hk]
                shifted=np.clip(cblk.astype(np.int16)+int(cm[yy,xx]-cmean),0,255).astype(np.uint8)
                canvas[yy*TILE:(yy+1)*TILE,xx*TILE:(xx+1)*TILE]=shifted
                cost+=5; nph[(yy,xx)]=hk; npm[yy,xx]=cm[yy,xx]  # ref(4) + mean(1)
            else:
                new.append((yy,xx,h)); nph[(yy,xx)]=h; npm[yy,xx]=cm[yy,xx]
        if new:
            strip=np.zeros((len(new)*TILE,TILE,3),np.uint8)
            for k,(yy,xx,h) in enumerate(new): strip[k*TILE:(k+1)*TILE]=im[yy*TILE:(yy+1)*TILE,xx*TILE:(xx+1)*TILE]
            e=enc(strip,q);ds=dec(e)
            for k,(yy,xx,h) in enumerate(new):
                pix=ds[k*TILE:(k+1)*TILE];canvas[yy*TILE:(yy+1)*TILE,xx*TILE:(xx+1)*TILE]=pix
                cache[h]=(pix.copy(),float(cm[yy,xx]))
            cost+=e.nbytes+len(new)*4
        ph=nph;pm=npm;B.append(cost);S.append(ssim(gray(canvas),gray(im)))
    return np.array(B),np.array(S),hit/max(chg,1)

def main():
    imgs=[crop(f) for f in synth()]
    print(f"SYNTHETIC stress: {len(imgs)} frames {imgs[0].shape[1]}x{imgs[0].shape[0]} "
          f"(box motion; AGC drift f30-45; pan f50-65)")
    QS=[30,60,90]
    codecs={"MJPEG":lambda q:mjpeg(imgs,q),"DeltaCam":lambda q:tile_replenish(imgs,q,False),
            "BG-Delta":lambda q:tile_replenish(imgs,q,True),"MoCo-resid":lambda q:moco(imgs,q),
            "ReCAST":lambda q:recast(imgs,q)}
    rd={}; timeline={}
    for name,fn in codecs.items():
        rd[name]=[]
        for q in QS:
            B,Sx,hr=fn(q); rd[name].append((B.mean(),Sx.mean()))
            if q==60: timeline[name]=B; extra=f"  cache-hit={hr*100:.0f}%" if name=="ReCAST" else ""
        print(f"  {name:<11}"+" ".join(f"{b/1024:5.2f}KB/{s:.1f}dB" for b,s in rd[name])+extra)
    print("  (per quality: KB-per-frame / PSNR(dB). Up-left on the curve wins.)")
    try:
        import matplotlib;matplotlib.use("Agg");import matplotlib.pyplot as plt
        col={"MJPEG":"#94a3b8","DeltaCam":"#ef4444","BG-Delta":"#f59e0b","MoCo-resid":"#3b82f6","ReCAST":"#22c55e"}
        fig,(ax1,ax2)=plt.subplots(1,2,figsize=(14,5))
        for k,v in rd.items():
            ax1.plot([b/1024 for b,s in v],[s for b,s in v],'-o',label=k,color=col[k],lw=2)
        ax1.set_xlabel("KB / frame (less = better)");ax1.set_ylabel("PSNR dB (more = better)")
        ax1.set_title("Rate-distortion");ax1.legend();ax1.grid(alpha=.3);ax1.set_xscale('log')
        for k,B in timeline.items(): ax2.plot(B/1024,label=k,color=col[k],lw=1.8)
        ax2.axvspan(30,45,color='orange',alpha=.12);ax2.axvspan(50,65,color='red',alpha=.10)
        ax2.text(37,ax2.get_ylim()[1]*.9,"AGC",ha='center',fontsize=9)
        ax2.text(57,ax2.get_ylim()[1]*.9,"PAN",ha='center',fontsize=9)
        ax2.set_xlabel("frame");ax2.set_ylabel("KB this frame (q60)")
        ax2.set_title("Per-frame cost");ax2.legend();ax2.grid(alpha=.3);ax2.set_yscale('log')
        plt.tight_layout();plt.savefig("sim/codec_bakeoff.png",dpi=130);print("chart -> sim/codec_bakeoff.png")
    except Exception as e:print("chart skip",e)
if __name__=="__main__":main()
