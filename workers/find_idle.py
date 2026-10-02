"""Find the lowest-mouth-motion window in a video: a usable closed-mouth 'idle' base clip."""
import sys, cv2, numpy as np
def main(video, win_s=3.0):
    cap=cv2.VideoCapture(video); fps=cap.get(cv2.CAP_PROP_FPS) or 25
    det=cv2.CascadeClassifier(cv2.data.haarcascades+"haarcascade_frontalface_default.xml")
    prev=None; diffs=[]; box=None; i=0
    while True:
        ok,f=cap.read()
        if not ok: break
        g=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY)
        if box is None:
            fs=det.detectMultiScale(g,1.1,6,minSize=(80,80))
            if len(fs)==0: continue
            x,y,w,h=max(fs,key=lambda a:a[2]*a[3]); box=(x,y,w,h)
        x,y,w,h=box; m=g[y+int(h*.62):y+int(h*1.0), x+int(w*.2):x+int(w*.8)].astype(np.float32)
        if prev is not None: diffs.append(float(np.abs(m-prev).mean()))
        else: diffs.append(0.0)
        prev=m; i+=1
    d=np.array(diffs); W=int(win_s*fps); best=None
    for s in range(0,len(d)-W,int(fps/5)):
        sc=d[s:s+W].mean()
        if best is None or sc<best[0]: best=(sc,s)
    print(f"fps {fps} frames {len(d)} best window start {best[1]/fps:.2f}s score {best[0]:.2f} (median motion {np.median(d):.2f})")
    return best[1]/fps
if __name__=="__main__": main(sys.argv[1], float(sys.argv[2]) if len(sys.argv)>2 else 3.0)
