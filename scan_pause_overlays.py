"""Read-only, every-frame pause-overlay candidates for manual edit review.

This detects one known game pause label, not personal information. It does not
approve media, edit originals, or replace visual/audio review of the export.
"""
import argparse
import hashlib
import json
from pathlib import Path

import av
import cv2
import numpy as np

METHOD_VERSION='luma-center-v2'


def scan(video, reference, threshold=.90):
    ref=cv2.imread(str(reference),cv2.IMREAD_GRAYSCALE)
    if ref is None or ref.shape!=(720,1280):
        raise ValueError('Expected an inspected 1280x720 paused game reference')
    template=ref[341:360,609:671]
    intervals=[];start=None;previous_time=None;count=0;matches=0
    with av.open(str(video)) as container:
        stream=container.streams.video[0];stream.codec_context.thread_count=2
        fps=float(stream.average_rate) if stream.average_rate else None
        if not fps:raise ValueError('Video frame rate is unavailable')
        for frame in container.decode(stream):
            if frame.time is None:raise ValueError('Frame timestamp missing')
            if frame.width!=1280 or frame.height!=720:raise ValueError('Unexpected video dimensions')
            t=frame.time
            if previous_time is not None and t<previous_time:raise ValueError('Nonmonotonic video time')
            # OBS uses a planar 8-bit luma surface. Read that plane directly;
            # converting an entire 720p frame to gray dominated scan time.
            # Correlation is invariant to the limited/full-range luma scale.
            if frame.format.name in ('yuv420p','yuvj420p','nv12'):
                plane=frame.planes[0]
                gray=np.frombuffer(plane,dtype=np.uint8).reshape(plane.height,plane.line_size)
            else:
                gray=frame.to_ndarray(format='gray')
            score=float(cv2.matchTemplate(gray[339:362,607:673],template,cv2.TM_CCOEFF_NORMED).max())
            found=score>=threshold
            count+=1;matches+=found
            if found and start is None:start=t
            elif not found and start is not None:
                intervals.append([round(start,6),round(t,6)]);start=None
            previous_time=t
        if start is not None:intervals.append([round(start,6),round(previous_time+1/fps,6)])
    return {'file':video.name,'frames_decoded_and_checked':count,'matching_frames':matches,
        'last_frame_time':previous_time,'threshold':threshold,'pause_candidates':intervals,
        'template_sha256':hashlib.sha256(reference.read_bytes()).hexdigest(),
        'method_version':METHOD_VERSION,
        'method':'Every decoded frame compared against the inspected game pause label in its expected center region.',
        'boundary_review':'pending','privacy_review':'pending','audio_review':'pending',
        'limitation':'Only pause-overlay candidates. Not a full visual privacy review, console detector or publication approval.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('video',type=Path)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();result=scan(args.video,args.reference)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.out.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2),encoding='utf-8');tmp.replace(args.out)
    print(json.dumps(result))


if __name__=='__main__':main()
