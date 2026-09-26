"""Resume a low-priority private pause-candidate scan of selected source files."""
import ctypes
import datetime as dt
import json
import os
from pathlib import Path

os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import cv2
from scan_pause_overlays import scan

ROOT=Path(__file__).resolve().parent


def save(path,row):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2),encoding='utf-8');tmp.replace(path)


def main():
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000):
            raise ctypes.WinError(ctypes.get_last_error())
    cv2.setNumThreads(1)
    plan=json.loads((ROOT/'private/postproduction/successful-attempt-edit-candidates.json').read_text())
    names=sorted({s['file'] for c in plan['chapters'] for s in c['candidates']})
    from project_paths import PAUSE_REFERENCE
    reference=PAUSE_REFERENCE
    out=ROOT/'private/postproduction/pause-scans';out.mkdir(exist_ok=True)
    status={'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'files_total':len(names),
        'files_done':0,'frames_checked':0,'paused_seconds_candidates':0,'status':'running',
        'privacy_review':'pending','audio_review':'pending','manual_boundary_review':'pending'}
    save(out/'batch-status.json',status)
    for name in names:
        source=ROOT/'recordings'/name;stat=source.stat();dest=out/(source.stem+'.json')
        row=json.loads(dest.read_text()) if dest.exists() else {}
        if row.get('source_bytes')!=stat.st_size or row.get('source_mtime_ns')!=stat.st_mtime_ns:
            row=scan(source,reference)
            after=source.stat()
            if after.st_size!=stat.st_size or after.st_mtime_ns!=stat.st_mtime_ns:
                raise RuntimeError('Source recording changed during inspection')
            row.update(source_bytes=stat.st_size,source_mtime_ns=stat.st_mtime_ns)
            save(dest,row)
        status['files_done']+=1;status['frames_checked']+=row['frames_decoded_and_checked']
        status['paused_seconds_candidates']+=sum(b-a for a,b in row['pause_candidates'])
        status['last_file']=name;status['updated_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
        save(out/'batch-status.json',status)
        print(json.dumps({k:status[k] for k in ['files_done','files_total','frames_checked','last_file']}),flush=True)
    status['status']='pause_candidates_complete';save(out/'batch-status.json',status)


if __name__=='__main__':main()
