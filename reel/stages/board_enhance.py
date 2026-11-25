"""Low-gain board-only luminance enhancement before blur; face pixels are excluded.

No confident board or no functional face detector => original pixels. Never applied
on a projector: tone changes there would alter slide artwork.
"""
import cv2
import numpy as np
from pathlib import Path


def target_quad(plan,t,width,height):
    if not plan or plan.get('version')!=2:return None
    for row in plan.get('timeline',[]):
        if row['start']<=t<row['end'] and row.get('mode') in ('board','both') and row.get('corners') is not None:
            return np.asarray(row['corners'],np.float32)*[width,height]
    return None


class BoardEnhancer:
    def __init__(self,plan,options,detector=None):
        self.plan=plan;self.amount=options['amount'];self.previous=None;self.skipped=0;self.frames=0
        values=np.arange(256,dtype=np.float32)
        self.glare_lut=np.clip(values-np.maximum(values-220,0)*self.amount,16,235).astype(np.uint8)
        self.detector=detector
        if detector is None:
            model=Path(__file__).resolve().parents[2]/'models/face_detection_yunet_2023mar.onnx'
            try:self.detector=cv2.FaceDetectorYN.create(str(model),'',(320,320),.5,.3,5000)
            except (cv2.error,OSError):self.detector=None

    def apply(self,y,t):
        self.mask=None;self.white=False
        height,width=y.shape;quad=target_quad(self.plan,t,width,height)
        if quad is None or self.detector is None:self.skipped+=1;return y
        # Detection on ORIGINAL pixels on every frame. Failure closes the enhancement.
        scale=min(1.,320/width);sw=max(2,round(width*scale));sh=max(2,round(height*scale))
        gray=cv2.resize(y,(sw,sh));bgr=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
        try:
            if callable(self.detector):faces=self.detector(bgr)
            else:
                self.detector.setInputSize((sw,sh));_,faces=self.detector.detect(bgr)
        except Exception:self.skipped+=1;return y
        mask=np.zeros_like(y);cv2.fillConvexPoly(mask,quad.astype(np.int32),255)
        # Motion excludes writing hand/body, including missed profile detections.
        if self.previous is not None and self.previous.shape==gray.shape:
            motion=cv2.absdiff(gray,self.previous)>8
            motion=cv2.dilate(motion.astype(np.uint8),np.ones((17,17),np.uint8))
            mask[cv2.resize(motion,(width,height),interpolation=cv2.INTER_NEAREST)>0]=0
        else:mask[:]=0
        self.previous=gray.copy()
        for f in ([] if faces is None else faces):
            x,yy,w,h=np.asarray(f[:4],float)/scale
            x0,y0=max(0,int(x-w*.5)),max(0,int(yy-h*.6))
            x1,y1=min(width,int(x+w*1.5)),min(height,int(yy+h*1.6))
            mask[y0:y1,x0:x1]=0
        # Sharpen existing strokes only, bounded to a few code values. No reconstruction.
        blur=cv2.GaussianBlur(y,(0,0),1.)
        self.mask=mask
        self.white=cv2.mean(y,mask=mask)[0]>140
        corrected=cv2.addWeighted(y,1+2*self.amount,blur,-2*self.amount,0)
        corrected=cv2.LUT(corrected,self.glare_lut)
        out=y.copy();cv2.copyTo(corrected,mask,out);self.frames+=1
        return out


    def chroma(self,u,v):
        """Neutralise only a mild colour cast on white boards; preserve green boards."""
        if self.mask is None or not self.white:return
        mask=cv2.resize(self.mask,(u.shape[1],u.shape[0]),interpolation=cv2.INTER_AREA)==255
        if not np.any(mask):return
        shifts=[cv2.mean(p,mask=mask.astype(np.uint8))[0]-128 for p in (u,v)]
        if any(abs(s)>24 for s in shifts):return
        for plane,shift in zip((u,v),shifts):
            if abs(shift)<.5:continue
            values=np.clip(plane.astype(np.float32)-np.clip(shift*self.amount,-4,4),16,240).astype(np.uint8)
            plane[mask]=values[mask]
